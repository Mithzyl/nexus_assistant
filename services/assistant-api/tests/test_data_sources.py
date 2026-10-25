"""Device-content sources: authorization, scoped sync, isolation and tool reads."""

import json

from conftest import auth, tool_auth, tool_envelope


def source_payload(source_type: str, **overrides):
    base = {
        "source_type": source_type,
        "device_id": "device-1",
        "display_name": f"iPhone {source_type}",
        "scope_keys": ["scope-a"],
        "upload_fields": {
            "calendar": ["title", "start", "end", "location"],
            "reminders": ["title", "due", "status", "notes"],
            "notes": ["title", "body"],
        }[source_type],
        "assistant_enabled": True,
    }
    base.update(overrides)
    return base


async def _create(harness, token, source_type, **overrides):
    response = await harness.client.post(
        "/api/v1/data-sources", headers=auth(token),
        json=source_payload(source_type, **overrides),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _sync(harness, token, source_id, batch_id, items, removed=None):
    response = await harness.client.post(
        f"/api/v1/data-sources/{source_id}/sync", headers=auth(token),
        json={"batch_id": batch_id, "items": items, "removed_external_ids": removed or []},
    )
    return response


def _item(external_id, scope="scope-a", version=1, updated="2026-10-23T10:00:00Z", **fields):
    if "due" in fields:
        fields["due_at"] = fields.pop("due")
    return {
        "external_id": external_id, "scope_key": scope, "version": version,
        "updated_at": updated, **fields,
    }


async def test_source_sync_is_idempotent_and_explicit_removal_keeps_other_rows(harness):
    source = await _create(harness, harness.token_a, "notes")
    first = await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("n1", title="note one", body="alpha"),
        _item("n2", title="note two", body="beta"),
    ])
    assert first.status_code == 200, first.text
    assert first.json()["created"] == 2

    duplicate = await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("n1", title="changed", body="changed")
    ])
    assert duplicate.status_code == 200
    assert duplicate.json()["created"] == 2  # stored batch response, no replay write
    content_after = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"]
    assert {row["external_id"]: row["title"] for row in content_after} == {
        "n1": "note one", "n2": "note two"
    }

    removed = await _sync(harness, harness.token_a, source["source_id"], "b2", [
        _item("n1", title="note one", body="alpha")
    ], removed=["n2"])
    assert removed.status_code == 200, removed.text
    assert removed.json()["removed"] == 1
    content = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"]
    assert [row["external_id"] for row in content] == ["n1"]
    assert content[0]["source_updated_at"].endswith("Z")


async def test_scope_removal_hides_without_deleting_and_readd_requires_resync(harness):
    source = await _create(harness, harness.token_a, "calendar")
    await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("e1", title="event", starts_at="2026-10-24T09:00:00Z",
              ends_at="2026-10-24T10:00:00Z", scope="cal-a")
    ])
    # replace the selected scope set; omission must not delete the uploaded row
    patched = await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"scope_keys": ["cal-b"]},
    )
    assert patched.status_code == 200, patched.text
    content = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"]
    assert content == []
    # re-adding the scope starts a new activation window; stale old row stays hidden
    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"scope_keys": ["cal-a"]},
    )
    content = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"]
    assert content == []


async def test_field_revocation_strips_old_copies_and_assistant_switch_is_independent(harness):
    source = await _create(harness, harness.token_a, "notes", assistant_enabled=False)
    await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("n1", title="secret title", body="secret body")
    ])
    patched = await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"upload_fields": ["title"]},
    )
    assert patched.status_code == 200
    item = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"][0]
    assert item["body"] == ""
    assert item["title"] == "secret title"

    # assistant still disabled even though title remains
    tool = await harness.client.post(
        "/internal/nexus/tools",
        headers=tool_auth(harness.settings),
        json=tool_envelope(
            external_user_id=harness.user_a, tool_execution_id="ds-note-1",
            tool_name="personal_assistant.search_notes",
            arguments={"query": "secret"},
        ),
    )
    assert tool.status_code == 200
    assert not [row for row in tool.json()["items"] if row.get("source") == "device_note"]

    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"assistant_enabled": True},
    )
    tool = await harness.client.post(
        "/internal/nexus/tools",
        headers=tool_auth(harness.settings),
        json=tool_envelope(
            external_user_id=harness.user_a, tool_execution_id="ds-note-2",
            tool_name="personal_assistant.search_notes",
            arguments={"query": "secret"},
        ),
    )
    assert tool.status_code == 200
    assert [row for row in tool.json()["items"] if row.get("source") == "device_note"]


async def test_field_must_be_authorized_and_content_is_user_isolated(harness):
    source = await _create(harness, harness.token_a, "notes")
    rejected = await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("n1", title="t", notes="not-a-note-field")
    ])
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "field_not_authorized"
    source_state = (await harness.client.get(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a)
    )).json()
    assert source_state["last_sync_status"] == "failed"
    assert source_state["last_error_code"] == "field_not_authorized"

    await _sync(harness, harness.token_a, source["source_id"], "b2", [
        _item("n1", title="A only", body="A body")
    ])
    foreign = await harness.client.get(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_b)
    )
    assert foreign.status_code == 404
    foreign_content = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_b)
    )).json()["items"]
    assert foreign_content == []


async def test_sync_requires_enabled_source_and_delete_items_keeps_source(harness):
    source = await _create(harness, harness.token_a, "reminders")
    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"sync_enabled": False},
    )
    blocked = await _sync(harness, harness.token_a, source["source_id"], "b1", [
        _item("r1", title="todo")
    ])
    assert blocked.status_code == 409
    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"sync_enabled": True},
    )
    await _sync(harness, harness.token_a, source["source_id"], "b2", [
        _item("r1", title="todo", status="open")
    ])
    deleted = await harness.client.delete(
        f"/api/v1/data-sources/{source['source_id']}/items",
        headers=auth(harness.token_a),
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1
    still = await harness.client.get(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a)
    )
    assert still.status_code == 200
    assert still.json()["item_count"] == 0


async def test_existing_tools_include_authorized_reminders_and_calendar(harness):
    reminders = await _create(harness, harness.token_a, "reminders", display_name="iPhone reminders")
    calendar = await _create(harness, harness.token_a, "calendar", display_name="iPhone calendar")
    rr = await _sync(harness, harness.token_a, reminders["source_id"], "r1", [
        _item("r1", title="device todo", status="open", due="2026-10-25T09:00:00Z")
    ])
    assert rr.status_code == 200, rr.text
    cc = await _sync(harness, harness.token_a, calendar["source_id"], "c1", [
        _item("e1", title="device event", starts_at="2026-10-25T09:00:00Z",
              ends_at="2026-10-25T10:00:00Z", location="Room")
    ])
    assert cc.status_code == 200, cc.text

    tasks = await harness.client.post(
        "/internal/nexus/tools", headers=tool_auth(harness.settings),
        json=tool_envelope(
            external_user_id=harness.user_a, tool_execution_id="ds-task-1",
            tool_name="personal_assistant.list_tasks", arguments={"limit": 20},
        ),
    )
    assert tasks.status_code == 200
    assert any(row.get("title") == "device todo" and row.get("source") == "device_reminder"
               for row in tasks.json()["items"])

    events = await harness.client.post(
        "/internal/nexus/tools", headers=tool_auth(harness.settings),
        json=tool_envelope(
            external_user_id=harness.user_a, tool_execution_id="ds-cal-1",
            tool_name="personal_assistant.list_calendar_events",
            arguments={"start_date": "2026-10-25", "end_date": "2026-10-25", "limit": 20},
        ),
    )
    assert events.status_code == 200
    assert any(row.get("title") == "device event" and row.get("source") == "device_calendar"
               for row in events.json()["items"])


async def test_readding_scope_exposes_only_newly_synced_items(harness):
    source = await _create(harness, harness.token_a, "calendar")
    await _sync(harness, harness.token_a, source["source_id"], "c1", [
        _item("old", title="old event", starts_at="2026-10-25T09:00:00Z",
              ends_at="2026-10-25T10:00:00Z")
    ])
    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"scope_keys": []},
    )
    await harness.client.patch(
        f"/api/v1/data-sources/{source['source_id']}", headers=auth(harness.token_a),
        json={"scope_keys": ["scope-a"]},
    )
    # old row's synced_at predates the new activation window
    assert (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"] == []
    c2 = await _sync(harness, harness.token_a, source["source_id"], "c2", [
        _item("new", title="new event", starts_at="2026-10-26T09:00:00Z",
              ends_at="2026-10-26T10:00:00Z")
    ])
    assert c2.status_code == 200, c2.text
    items = (await harness.client.get(
        "/api/v1/content", headers=auth(harness.token_a)
    )).json()["items"]
    assert [row["external_id"] for row in items] == ["new"]
