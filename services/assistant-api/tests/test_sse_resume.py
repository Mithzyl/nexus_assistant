"""SSE proxying: ordering, resume, terminal state and broken streams."""

import asyncio
import uuid

from conftest import auth, create_ask
from sqlalchemy import select

from assistant_api.models import NexusRun, UserAsk


async def _paid_run(harness, key: str) -> dict:
    response = await create_ask(harness, token=harness.token_a, key=key)
    assert response.status_code == 202, response.text
    return response.json()


async def _wait_for_sequence(harness, local_run_id: str, sequence: int) -> NexusRun:
    """Condition wait: return as soon as the observer persists the frame."""

    deadline = asyncio.get_running_loop().time() + 30.0
    while asyncio.get_running_loop().time() < deadline:
        row = await _row(harness, local_run_id)
        if row.last_event_sequence >= sequence:
            return row
        await asyncio.sleep(0.01)
    row = await _row(harness, local_run_id)
    raise AssertionError(
        f"event sequence did not reach {sequence}: status={row.status} "
        f"seq={row.last_event_sequence} error={row.error_code}"
    )


async def _row(harness, local_run_id: str) -> NexusRun:
    async with harness.app.state.session_factory() as session:
        row = await session.scalar(
            select(NexusRun).where(NexusRun.local_run_id == local_run_id)
        )
    assert row is not None
    return row


async def test_events_are_forwarded_with_ids_and_terminal_state(harness):
    """Committed frames keep their ids, and the last one settles the local state."""

    run = await _paid_run(harness, "sse-key-0001")
    response = await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events", headers=auth(harness.token_a)
    )

    assert response.status_code == 200
    text = response.text
    assert "id: 1" in text and "event: agent.workflow.started" in text
    assert "id: 3" in text and "event: agent.workflow.completed" in text
    assert text.count("data:") >= 3

    row = await _row(harness, run["local_run_id"])
    assert row.last_event_sequence == 3
    assert row.status == "succeeded"


async def test_resume_sends_the_last_event_id_upstream(harness):
    """A reconnect must ask for what it has not seen, not replay everything."""

    run = await _paid_run(harness, "sse-key-0002")
    await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events",
        headers={**auth(harness.token_a), "Last-Event-ID": "2"},
    )

    stream_calls = [c for c in harness.fake.calls if c["path"].endswith("/events")]
    assert stream_calls[-1]["headers"]["last-event-id"] == "2"


async def test_rejected_stream_becomes_an_explicit_error_frame(harness):
    """A refused subscription must not look like a finished run."""

    # Seed the state a background observer has already reached: the opening
    # frame is committed. This removes the observer/HTTP race from the assertion
    # while still testing that a refused independent subscription keeps cursor 1.
    harness.fake.fail_stream_status = 403
    async with harness.app.state.session_factory() as db:
        row = NexusRun(
            local_run_id=str(uuid.uuid4()),
            user_id=harness.user_a,
            nexus_run_id="run_remote_existing",
            workflow_execution_id="wf_existing_1",
            idempotency_key="sse-key-0003",
            request_text="已经开始的运行",
            status="running",
            last_event_sequence=1,
            purpose="chat",
            trigger="chat",
            input_version=1,
        )
        db.add(row)
        db.add(
            UserAsk(
                user_id=harness.user_a,
                local_run_id=row.local_run_id,
                request_text=row.request_text,
            )
        )
        await db.commit()
        local_run_id = row.local_run_id

    response = await harness.client.get(
        f"/api/v1/asks/{local_run_id}/events", headers=auth(harness.token_a)
    )

    assert response.status_code == 200
    assert "event: assistant.error" in response.text
    assert "nexus_permission_denied" in response.text

    row = await _row(harness, local_run_id)
    assert row.status == "running"
    assert row.error_code == "nexus_permission_denied"
    assert row.last_event_sequence == 1


async def test_malformed_frame_does_not_settle_the_run(harness):
    """A non-JSON body is forwarded for transparency but never read as a result."""

    harness.fake.auto_complete_workflow = False
    harness.fake.sse_body = 'id: 1\nevent: agent.workflow.started\ndata: not-json\n\n'
    run = await _paid_run(harness, "sse-key-0004")

    response = await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events", headers=auth(harness.token_a)
    )
    assert "data: not-json" in response.text

    row = await _row(harness, run["local_run_id"])
    assert row.status == "running"
    assert row.last_event_sequence == 1


async def test_unfinished_trailing_frame_is_still_delivered(harness):
    """A stream that ends without a blank line must not lose its last transition."""

    harness.fake.sse_body = (
        'id: 7\nevent: agent.node.completed\ndata: {"workflow_status": "succeeded"}'
    )
    run = await _paid_run(harness, "sse-key-0005")

    response = await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events", headers=auth(harness.token_a)
    )
    assert "id: 7" in response.text

    row = await _row(harness, run["local_run_id"])
    assert row.last_event_sequence == 7
    assert row.status == "succeeded"


async def test_replaying_completed_events_preserves_completed_at(harness):
    """A finite replay must not rewrite the moment the run actually finished."""

    run = await _paid_run(harness, "sse-key-0006")
    first = await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events", headers=auth(harness.token_a)
    )
    assert first.status_code == 200
    after_first = await _row(harness, run["local_run_id"])
    assert after_first.status == "succeeded"
    assert after_first.completed_at is not None

    await asyncio.sleep(0.05)
    second = await harness.client.get(
        f"/api/v1/asks/{run['local_run_id']}/events", headers=auth(harness.token_a)
    )
    assert second.status_code == 200
    after_second = await _row(harness, run["local_run_id"])

    assert after_second.completed_at == after_first.completed_at
