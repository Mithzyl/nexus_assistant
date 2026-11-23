"""Verify bounded conversational history and persisted clarification state in isolated SQLite."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from assistant_api.models import NexusRun
from assistant_api.services import matter_plan, operation_intake
from conftest import auth


async def saved_run(h, key, text, *, minutes=5, question=None, receipts=None, user_id=None, matter_id=None, status="succeeded"):
    """Seed a completed historical turn with its actual typed business references."""
    now = datetime.now(UTC) - timedelta(minutes=minutes)
    saved = {"outcome": "needs_clarification", "clarify": question,
             "pending_context": {"choices": ["下午三点"]}} if question else {
                 "outcome": "answer", "answer": "已处理"}
    async with h.app.state.session_factory() as db:
        row = NexusRun(user_id=user_id or h.user_a, purpose=matter_plan.PURPOSE, status=status,
            idempotency_key=key, request_text=text, created_at=now, completed_at=now,
            context_json={"text": text, "unified_intake_result": saved,
                          "api_flow": {"receipts": receipts or [], "matter_id": matter_id}})
        db.add(row)
        await db.commit()
        return row.local_run_id


async def test_short_follow_up_retains_recent_entity_reference(harness):
    """A pronoun-only edit receives two recent turns and the original schedule ID."""
    h = harness
    reference = {"operation_id": "create_schedule", "status": "succeeded",
                 "data": {"schedule_id": "schedule-101", "title": "项目会", "starts_at": "10:00"}}
    previous = await saved_run(h, "prev-schedule", "下周召开项目会", receipts=[reference], minutes=4)
    older = await saved_run(h, "prev-todo", "记得取快递", minutes=9)
    await saved_run(h, "too-old", "午饭吃什么", minutes=35)
    await saved_run(h, "not-mine", "其他账户的会", user_id=h.user_b, minutes=1)
    async with h.app.state.session_factory() as db:
        history = await operation_intake.message_history(db, SimpleNamespace(user_id=h.user_a), "往后推两小时", None)
    assert [entry["source_run_id"] for entry in history] == [older, previous]
    assert history[-1]["references"] == [reference]


async def test_clarification_can_be_recovered_expired_and_dismissed(harness):
    """The history response exposes expiry and cancellation only to the record owner."""
    h = harness
    recent = await saved_run(h, "ask-recent", "周三安排会议", question="请问几点？")
    expired = await saved_run(h, "ask-old", "下次安排会议", minutes=45, question="请问几点？")
    headers = auth(h.token_a)
    response = await h.client.get("/api/v1/matters/intake", headers=headers)
    rows = {row["local_run_id"]: row for row in response.json()}
    assert rows[recent]["status"] == "succeeded"
    assert rows[recent]["reply_state"] == "awaiting_reply"
    assert rows[expired]["reply_state"] == "expired"
    target = f"/api/v1/matters/intake/{recent}/dismiss-reply"
    assert (await h.client.post(target, headers=auth(h.token_b))).status_code == 404
    assert (await h.client.post(target, headers=headers)).status_code == 204
    assert (await h.client.post(target, headers=headers)).status_code == 204
    result = await h.client.get(f"/api/v1/matters/intake/{recent}", headers=headers)
    assert result.json()["reply_state"] == "cancelled"
    assert result.json()["clarify"] == "请问几点？"


async def test_explicit_and_implicit_replies_close_the_saved_parent(harness):
    """Check real Assistant request assembly, terminal storage, and key replay with fixed Nexus results."""
    import json

    h = harness
    headers = auth(h.token_a)
    url = "/api/v1/matters/intake"

    async def submit(text, key, final, **kwargs):
        """Return the Assistant response and the actual payload sent to the isolated Nexus transport."""
        h.fake.final_text = json.dumps(final, ensure_ascii=False)
        response = await h.client.post(url, headers=headers,
            json={"text": text, "idempotency_key": key, **kwargs})
        assert response.status_code == 200, response.text
        sent = next(call for call in reversed(h.fake.calls) if call['path'] == '/api/v1/app/runs')
        return response.json(), json.loads(json.loads(sent['body'])['user_request'])

    first, _ = await submit("周三开会", "real-test-first", {"decision": "ask_user", "question": "周三几点开会？"})
    assert first["reply_state"] == "awaiting_reply"
    parent_id = first["local_run_id"]
    assert first["continuation_run_id"] == parent_id
    assert first["clarification_summary"] == "周三几点开会？"
    expires = datetime.fromisoformat(first["reply_expires_at"])
    assert timedelta(minutes=29, seconds=55) < expires - datetime.now(UTC) <= timedelta(minutes=30)
    end, payload = await submit("下午三点", "real-test-reply", {"decision": "answer", "answer": "收到"},
                                continuation_run_id=parent_id)
    assert end["parent_run_id"] == parent_id and payload["reply_to_run_id"] == parent_id
    response = await h.client.get(f"{url}/{parent_id}", headers=headers)
    assert response.json()["reply_state"] == "resolved"
    replay, _ = await submit("下午三点", "real-test-reply", {"decision": "answer", "answer": "收到"},
                             continuation_run_id=parent_id)
    assert replay["local_run_id"] == end["local_run_id"] and h.fake.run_seq == 2

    older = await saved_run(h, "ask-older", "取资料", question="几点取资料？", minutes=8)
    pending = await saved_run(h, "ask-last", "周五开会", question="周五几点开会？", minutes=2)
    implicit, payload = await submit("中午十二点", "implicit-reply",
        {"decision": "answer", "answer": "收到", "pending_reply_disposition": "answered"})
    assert payload["pending_reply"]["source_run_id"] == pending
    assert implicit["parent_run_id"] == pending
    assert (await h.client.get(f"{url}/{pending}", headers=headers)).json()["reply_state"] == "resolved"
    _, payload = await submit("相对提醒是什么", "new-topic-123",
        {"decision": "answer", "answer": "以事件为基准", "pending_reply_disposition": "new_topic"})
    assert payload["pending_reply"]["source_run_id"] == older
    assert (await h.client.get(f"{url}/{older}", headers=headers)).json()["reply_state"] == "cancelled"

    other = await saved_run(h, "ask-ignore", "取快递", question="几点去取？", minutes=1)
    _, payload = await submit("相对提醒的例子", "ignore-pending-123",
        {"decision": "answer", "answer": "开始前十分钟"}, ignore_pending_reply=True)
    assert "pending_reply" not in payload
    assert other not in {item["source_run_id"] for item in payload["history"]}


async def test_history_scores_lexical_matches_without_duplicates_or_unfinished_turns(harness):
    """Reserve recent records and pick the best older lexical matches, independent of recency."""
    h = harness
    stronger = await saved_run(h, "older-stronger", "项目预算 发布会议", minutes=70)
    middling = await saved_run(h, "older-middling", "项目预算", minutes=55)
    oldest = await saved_run(h, "older-fit", "发布会议", minutes=50)
    await saved_run(h, "newer-weaker", "项目事项", minutes=40)
    await saved_run(h, "irrelevant", "海边散步", minutes=32)
    await saved_run(h, "failed-keywords", "项目预算 发布会议", minutes=4, status="failed")
    await saved_run(h, "running-keywords", "项目预算 发布会议", minutes=2, status="running")
    recent = await saved_run(h, "recent-match", "项目预算 发布会议", minutes=7)
    no_match = await saved_run(h, "recent-plain", "记得取快递", minutes=3)
    async with h.app.state.session_factory() as db:
        result = await operation_intake.message_history(
            db, SimpleNamespace(user_id=h.user_a), "项目预算 发布会议", None)
    assert [item["source_run_id"] for item in result] == [stronger, middling, oldest, recent, no_match]


async def test_pending_scope_and_explicit_skip_are_owned_and_idempotent(harness):
    """Pick the global or exact-matter candidate and close only the first explicitly skipped reply."""
    import json

    h = harness
    older = await saved_run(h, "global-only", "取文件", question="去哪取？", minutes=6)
    scoped = await saved_run(h, "matter-specific", "开会", question="几点开始？", minutes=2, matter_id="matter-a")
    latest = await saved_run(h, "newest-other", "开会", question="几点开会？", minutes=1, matter_id="matter-b")
    await saved_run(h, "other-account", "问对方", question="几点？", minutes=0, user_id=h.user_b)
    async with h.app.state.session_factory() as db:
        assert (await operation_intake.find_pending_reply(db, h.user_a, None))["source_run_id"] == latest
        assert (await operation_intake.find_pending_reply(db, h.user_a, "matter-a"))["source_run_id"] == scoped
        assert await operation_intake.find_pending_reply(db, h.user_a, "matter-c") is None

    headers = auth(h.token_a)
    h.fake.final_text = '{"decision":"answer","answer":"按日程开始时间计算"}'
    request = {"text": "相对提醒是什么意思？", "idempotency_key": "new-topic-skip-1",
               "ignore_pending_clarification": True}
    for _ in range(2):
        result = await h.client.post("/api/v1/matters/intake", headers=headers, json=request)
        assert result.status_code == 200
        assert result.json()["parent_run_id"] is None
    calls = [call for call in h.fake.calls if call["path"] == "/api/v1/app/runs"]
    assert len(calls) == 1
    context = json.loads(json.loads(calls[0]["body"])["user_request"])
    assert "pending_reply" not in context and "reply_to_run_id" not in context
    rows = (await h.client.get("/api/v1/matters/intake", headers=headers)).json()
    states = {row["local_run_id"]: row["reply_state"] for row in rows}
    assert states[latest] == "cancelled"
    assert states[scoped] == states[older] == "awaiting_reply"


async def test_reply_migration_preserves_old_runs(tmp_path):
    """Upgrade an older SQLite run table and keep the original execution data."""
    import sqlite3

    from scripts.migrate_sqlite import init, upgrade

    path = tmp_path / 'old-runs.sqlite3'
    database = f'sqlite+aiosqlite:///{path}'
    await init(database)
    with sqlite3.connect(path) as db:
        db.execute('ALTER TABLE nexus_runs DROP COLUMN reply_state')
        db.execute('ALTER TABLE nexus_runs DROP COLUMN reply_expires_at')
        db.execute("DELETE FROM schema_migrations WHERE version='0031_intake_replies'")
        db.execute("""INSERT INTO nexus_runs (local_run_id,user_id,idempotency_key,status,
            last_event_sequence,request_text,created_at,updated_at,completed_at)
            VALUES ('existing-run','old-user','old-request','succeeded',0,'原来的问题',
                    CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)""")
    await upgrade(database)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT version FROM schema_migrations WHERE version='0031_intake_replies'").fetchone()
        assert db.execute("""SELECT status, request_text, reply_state, reply_expires_at
            FROM nexus_runs WHERE local_run_id='existing-run'""").fetchone() == ('succeeded', '原来的问题', None, None)


async def test_capture_acknowledges_before_worker_and_reuses_key(harness):
    """Store and deduplicate the submission before any remote work."""
    from sqlalchemy import func, select

    h = harness
    headers = auth(h.token_a)
    url = "/api/v1/matters/intake"
    payload = {"text": "问候记录", "idempotency_key": "browser-capture-1", "entry_mode": "async_capture"}
    for _ in range(2):
        response = await h.client.post(url, headers=headers, json=payload)
        assert response.status_code == 202
        assert response.json()["stage"] == "queued"
        assert response.json()["status"] == "processing"
        assert response.json()["request_id"] == payload["idempotency_key"]
        assert response.json()["record_id"] == response.json()["local_run_id"]
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(NexusRun).where(
            NexusRun.idempotency_key == 'matter-intake:browser-capture-1')) == 1
    assert h.fake.run_seq == 0


async def test_schedule_definitions_are_ready_in_initial_chat(harness):
    """Make two common operations directly callable while retaining indexed long-tail tools."""
    from assistant_api.services.api_operations import OperationCatalog
    from assistant_api.services.agent_responses import supplied_definitions, definition_matches

    h = harness
    catalog = OperationCatalog(h.app)
    context = operation_intake.model_context(
        {"text": "明天开会", "now": "2035-05-10T00:00:00+00:00", "timezone": "Asia/Shanghai",
         "history": [], "receipts": [], "step": 0, "trigger_source": "chat"}, catalog)
    definitions, schemas = supplied_definitions(context, [])
    assert set(definitions) == {"resolve_operation_definitions", "list_schedules", "create_schedule"}
    assert all(definition_matches(catalog.operations[oid], definitions, schemas)
               for oid in ("list_schedules", "create_schedule"))


async def test_restored_reply_can_be_classified_as_an_independent_topic(harness):
    """A composer-selected parent does not turn an unrelated question into a reply."""
    import json

    h = harness
    parent = await saved_run(h, "restore-then-switch", "明天想开会", question="想几点开会？")
    h.fake.final_text = json.dumps({"decision": "answer", "answer": "相对提醒按日程时间计算。",
                                    "pending_reply_disposition": "new_topic"}, ensure_ascii=False)
    headers = auth(h.token_a)
    url = "/api/v1/matters/intake"
    response = await h.client.post(url, headers=headers, json={
        "text": "相对提醒是什么意思？", "idempotency_key": "restored-new-topic",
        "continuation_run_id": parent,
    })
    assert response.status_code == 200
    assert response.json()["parent_run_id"] is None
    assert (await h.client.get(f"{url}/{parent}", headers=headers)).json()["reply_state"] == "cancelled"
    launch = next(call for call in h.fake.calls if call["path"].endswith("/agent-workflows"))
    assert "pending_reply_disposition" in json.loads(launch["body"])["final_output_schema"]["properties"]


async def test_reply_and_capture_migrations_roll_back_together(tmp_path, monkeypatch):
    """A failed 0032 index must leave both new column groups and their markers unapplied."""
    import sqlite3

    import pytest
    from sqlalchemy.exc import OperationalError

    from scripts import migrate_sqlite

    path = tmp_path / "rollback-intake.db"
    url = f"sqlite+aiosqlite:///{path}"
    await migrate_sqlite.init(url)
    columns = ("reply_state", "reply_expires_at", "intake_stage",
               "capture_lease_expires_at", "capture_dispatch_started_at")
    with sqlite3.connect(path) as db:
        db.execute("DROP INDEX ix_nexus_runs_intake_queue")
        for column in columns:
            db.execute(f"ALTER TABLE nexus_runs DROP COLUMN {column}")
        db.executemany("DELETE FROM schema_migrations WHERE version = ?", [
            (migrate_sqlite.INTAKE_REPLY_VERSION,), (migrate_sqlite.CAPTURE_VERSION,)])
    monkeypatch.setattr(migrate_sqlite, "INDEX_MIGRATIONS", (
        (migrate_sqlite.CAPTURE_VERSION, "bad_index", "CREATE INDEX bad_index ON missing_table(missing_column)"),
    ))
    with pytest.raises(OperationalError):
        await migrate_sqlite.upgrade(url)
    with sqlite3.connect(path) as db:
        assert set(columns).isdisjoint(row[1] for row in db.execute("PRAGMA table_info(nexus_runs)"))
        assert not db.execute("SELECT 1 FROM schema_migrations WHERE version IN (?, ?)", (
            migrate_sqlite.INTAKE_REPLY_VERSION, migrate_sqlite.CAPTURE_VERSION)).fetchone()
