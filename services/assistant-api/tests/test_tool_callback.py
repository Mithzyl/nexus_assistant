"""Current NexusPilot tool envelope: auth, ownership, formats and receipts."""

from sqlalchemy import func, select

from assistant_api.models import Note, ToolCallReceipt
from conftest import tool_auth, tool_envelope


async def _note_of(harness, user_id: str, title: str | None = None):
    async with harness.app.state.session_factory() as session:
        query = select(Note).where(Note.user_id == user_id)
        if title:
            query = query.where(Note.title == title)
        note = await session.scalar(query)
    assert note is not None
    return note


async def _call(harness, envelope: dict):
    return await harness.client.post(
        "/internal/nexus/tools", json=envelope, headers=tool_auth(harness.settings)
    )


async def test_callback_requires_the_application_credential(harness):
    response = await harness.client.post(
        "/internal/nexus/tools",
        json=tool_envelope(
            external_user_id=harness.user_a,
            tool_execution_id="call-no-token",
            tool_name="personal_assistant.list_tasks",
        ),
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_unknown_external_user_is_not_found(harness):
    response = await _call(
        harness,
        tool_envelope(
            external_user_id="user-does-not-exist",
            tool_execution_id="call-ghost",
            tool_name="personal_assistant.list_tasks",
        ),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "external_user_unknown"


async def test_task_list_only_returns_the_calling_user_rows(harness):
    response = await _call(
        harness,
        tool_envelope(
            external_user_id=harness.user_a,
            tool_execution_id="call-tasks-a",
            tool_name="personal_assistant.list_tasks",
            arguments={"limit": 10},
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["items"] and all(item["task_id"] for item in body["items"])
    assert body["next_cursor"] is None

    async with harness.app.state.session_factory() as session:
        b_titles = set(
            (
                await session.scalars(select(Note.title).where(Note.user_id == harness.user_b))
            ).all()
        )
    assert "体检报告" in b_titles  # same fixture title exists for B...
    assert all("体检报告" not in item["title"] for item in body["items"])


async def test_note_search_is_user_scoped_and_returns_business_read_shape(harness):
    a_note = await _note_of(harness, harness.user_a, "体检报告")
    b_note = await _note_of(harness, harness.user_b, "体检报告")
    assert a_note.note_id != b_note.note_id

    response = await _call(
        harness,
        tool_envelope(
            external_user_id=harness.user_a,
            tool_execution_id="call-notes-a",
            tool_name="personal_assistant.search_notes",
            arguments={"query": "体检", "limit": 10},
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert len(body["items"]) == 1
    assert body["items"][0]["note_id"] == a_note.note_id
    assert "body" not in body["items"][0]
    assert body["items"][0]["excerpt"]


async def test_calendar_uses_current_date_arguments_and_user_scope(harness):
    response = await _call(
        harness,
        tool_envelope(
            external_user_id=harness.user_a,
            tool_execution_id="call-calendar-a",
            tool_name="personal_assistant.list_calendar_events",
            arguments={"start_date": "2026-10-01", "end_date": "2026-10-31", "limit": 10},
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "next_cursor"}
    assert body["items"] and body["items"][0]["title"] == "牙医复诊"


async def test_envelope_cannot_supply_an_extra_identity_field(harness):
    envelope = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-extra-owner",
        tool_name="personal_assistant.list_tasks",
    )
    envelope["user_id"] = harness.user_b
    response = await _call(harness, envelope)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


async def test_model_cannot_supply_identity_in_arguments(harness):
    response = await _call(
        harness,
        tool_envelope(
            external_user_id=harness.user_a,
            tool_execution_id="call-extra",
            tool_name="personal_assistant.list_tasks",
            arguments={"limit": 10, "user_id": harness.user_b},
        ),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_tool_arguments"


async def test_repeated_execution_id_returns_stored_receipt(harness):
    envelope = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-repeat",
        tool_name="personal_assistant.list_tasks",
        arguments={"limit": 10},
    )
    first = await _call(harness, envelope)
    assert first.status_code == 200
    assert first.headers["x-idempotent-replay"] == "false"

    second = await _call(harness, envelope)
    assert second.status_code == 200
    assert second.headers["x-idempotent-replay"] == "true"
    assert second.json() == first.json()

    async with harness.app.state.session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ToolCallReceipt)) == 1
        receipt = await session.scalar(select(ToolCallReceipt))
    assert receipt is not None
    assert receipt.nexus_run_id is None
    assert receipt.tool_name == "personal_assistant.list_tasks"


async def test_same_execution_id_other_user_is_refused(harness):
    envelope = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-shared",
        tool_name="personal_assistant.list_tasks",
    )
    assert (await _call(harness, envelope)).status_code == 200
    envelope["external_user_id"] = harness.user_b
    response = await _call(harness, envelope)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "tool_call_identity_mismatch"


async def test_wrong_application_and_version_are_rejected(harness):
    wrong_app = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-app",
        tool_name="personal_assistant.list_tasks",
        application_id="retail",
    )
    assert (await _call(harness, wrong_app)).status_code == 403

    wrong_version = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-version",
        tool_name="personal_assistant.list_tasks",
        tool_version="9.9.9",
    )
    response = await _call(harness, wrong_version)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "tool_version_unsupported"


async def test_unknown_tool_and_catalog_are_fixed(harness):
    unknown = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-unknown",
        tool_name="personal_assistant.drop_database",
    )
    assert (await _call(harness, unknown)).status_code == 404

    response = await harness.client.get(
        "/internal/nexus/tools",
        headers={
            "Authorization": (
                f"Bearer {harness.settings.tool_callback_token.get_secret_value()}"
            )
        },
    )
    assert response.status_code == 200
    tools = response.json()["tools"]
    assert {tool["name"] for tool in tools} == {
        "personal_assistant.list_tasks",
        "personal_assistant.search_notes",
        "personal_assistant.list_calendar_events",
    }
    assert all("user_id" not in tool["input_schema"].get("properties", {}) for tool in tools)


async def test_same_execution_id_with_different_arguments_is_refused(harness):
    """An idempotency key binds to one argument object, not just user and tool."""

    first = tool_envelope(
        external_user_id=harness.user_a,
        tool_execution_id="call-args",
        tool_name="personal_assistant.list_tasks",
        arguments={"limit": 10},
    )
    assert (await _call(harness, first)).status_code == 200

    changed = {**first, "arguments": {"limit": 1}}
    response = await _call(harness, changed)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "tool_call_arguments_mismatch"

    replay = await _call(harness, first)
    assert replay.status_code == 200
    assert replay.headers["x-idempotent-replay"] == "true"
