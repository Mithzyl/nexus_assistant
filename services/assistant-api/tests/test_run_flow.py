"""Run creation contract, idempotency, cancellation and failure mapping."""

import asyncio
import json

from conftest import auth, create_ask
from sqlalchemy import select

from assistant_api.models import NexusRun


def run_create_calls(harness) -> list[dict]:
    """Return only the run-creation calls the fake upstream received."""

    return [
        call
        for call in harness.fake.calls
        if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
    ]


async def _local_run(harness, key: str) -> NexusRun:
    async with harness.app.state.session_factory() as session:
        row = await session.scalar(
            select(NexusRun).where(NexusRun.idempotency_key == key)
        )
    assert row is not None
    return row


async def test_run_request_has_no_client_identity_and_uses_current_workflow_body(harness):
    """Only user_request crosses the wire; owner comes from the trusted header.

    The current NexusPilot general-agent API requires explicit role bindings. They
    are assistant-api deployment configuration, never client input or response data.
    """

    response = await create_ask(harness, token=harness.token_a, key="run-key-0001")
    assert response.status_code == 202

    call = run_create_calls(harness)[0]
    body = json.loads(call["body"])
    assert body == {"user_request": "帮我记一下午饭 28 元"}
    assert "user_id" not in body and "principal_id" not in body

    workflow_calls = [c for c in harness.fake.calls if c["path"].endswith("/agent-workflows")]
    workflow_body = json.loads(workflow_calls[0]["body"])
    assert workflow_body["workflow_name"] == "general_agent"
    assert workflow_body["workflow_version"] == "1.0.0"
    assert workflow_body["execution_profile"] == "general_tools_v1"
    assert workflow_body["stream"] is True
    assert workflow_body["review_policy"] == "never"
    assert "agent_profile" not in workflow_body
    assert workflow_body["role_bindings"]["controller"]["provider"]
    assert "user_id" not in json.dumps(workflow_body)
    assert "principal_id" not in json.dumps(workflow_body)


async def test_external_user_header_is_the_local_user_id(harness):
    """The trusted assertion is the stable local id, not an email address."""

    await create_ask(harness, token=harness.token_a, key="run-key-0002")
    headers = run_create_calls(harness)[0]["headers"]
    assert headers["x-application-id"] == "personal_assistant"
    assert headers["x-external-user-id"] == harness.user_a
    assert headers["authorization"].startswith("Bearer ")
    assert not any(key.startswith("x-nexuspilot-") for key in headers)
    assert "idempotency-key" not in headers


async def test_duplicate_submit_replays_without_a_second_run(harness):
    """The same idempotency key returns the first run and calls nobody."""

    first = await create_ask(harness, token=harness.token_a, key="run-key-0003")
    second = await create_ask(harness, token=harness.token_a, key="run-key-0003")

    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["local_run_id"] == second.json()["local_run_id"]
    assert second.json()["replayed"] is True
    assert len(run_create_calls(harness)) == 1


async def test_same_key_with_different_text_is_rejected(harness):
    """A reused key must never silently return another question's run."""

    first = await create_ask(harness, token=harness.token_a, key="run-key-conflict")
    assert first.status_code == 202

    different = await create_ask(
        harness, token=harness.token_a, key="run-key-conflict",
        text="另一条完全不同的问题",
    )
    assert different.status_code == 409, different.text
    assert different.json()["error"]["code"] == "idempotency_conflict"
    assert len(run_create_calls(harness)) == 1


async def test_cancel_updates_only_the_local_projection(harness):
    """Cancellation asks NexusPilot, then records what it reported."""

    # 受控等待：后台流停在开场帧，取消完成后才释放终态帧。
    harness.fake.auto_complete_workflow = False
    created = (await create_ask(harness, token=harness.token_a, key="run-key-0004")).json()
    response = await harness.client.post(
        f"/api/v1/asks/{created['local_run_id']}/cancel", headers=auth(harness.token_a)
    )
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"

    cancel_calls = [c for c in harness.fake.calls if c["path"].endswith("/cancel")]
    assert len(cancel_calls) == 1
    assert created["workflow_execution_id"] in cancel_calls[0]["path"]

    # 取消已确认；释放后台流仅用于清理，不再等待可能被关闭而永远不会消费的终态帧。
    harness.fake.workflow_release.set()


async def test_temporary_upstream_failure_is_retryable_and_marked_failed(harness):
    """A 503 is surfaced as retryable, and no remote run id is invented."""

    harness.fake.fail_run_create_status = 503
    response = await create_ask(harness, token=harness.token_a, key="run-key-0005")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "nexus_unavailable"
    assert error["retryable"] is True

    row = await _local_run(harness, "run-key-0005")
    assert row.status == "failed"
    assert row.nexus_run_id is None
    assert row.error_code == "nexus_unavailable"


async def test_timeout_leaves_an_unknown_outcome(harness):
    """A timed-out create must not be retried automatically as if nothing happened."""

    harness.fake.raise_timeout_on_run_create = True
    response = await create_ask(harness, token=harness.token_a, key="run-key-0006")

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "nexus_timeout"

    row = await _local_run(harness, "run-key-0006")
    assert row.status == "unknown"
    assert row.completed_at is None
    assert row.error_code == "nexus_timeout"


async def test_a_run_without_a_workflow_cannot_be_streamed(harness):
    """The proxy refuses to open a stream it could never fill."""

    harness.fake.fail_run_create_status = 503
    await create_ask(harness, token=harness.token_a, key="run-key-0007")
    row = await _local_run(harness, "run-key-0007")

    response = await harness.client.get(
        f"/api/v1/asks/{row.local_run_id}/events", headers=auth(harness.token_a)
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_streamable"


async def test_cancel_after_completion_is_truthful_and_local(harness):
    """A finished run must not be turned into cancelled, nor cancelled upstream."""

    created = (await create_ask(harness, token=harness.token_a, key="run-key-completed")).json()
    local_id = created["local_run_id"]
    for _ in range(50):
        read = await harness.client.get(
            f"/api/v1/asks/{local_id}", headers=auth(harness.token_a)
        )
        if read.json()["status"] == "succeeded":
            break
        await asyncio.sleep(0.01)
    assert read.json()["status"] == "succeeded"
    before = [c for c in harness.fake.calls if c["path"].endswith("/cancel")]

    response = await harness.client.post(
        f"/api/v1/asks/{local_id}/cancel", headers=auth(harness.token_a)
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "succeeded"
    after = [c for c in harness.fake.calls if c["path"].endswith("/cancel")]
    assert len(after) == len(before)


async def test_crash_after_remote_accept_without_local_mapping_is_not_retried(harness):
    """A saved idempotency key alone must not justify a second remote Run."""

    async with harness.app.state.session_factory() as session:
        row = NexusRun(
            user_id=harness.user_a,
            idempotency_key="run-key-crash-window",
            request_text="帮我记一下午饭 28 元",
            status="pending",
        )
        session.add(row)
        await session.commit()

    response = await create_ask(harness, token=harness.token_a, key="run-key-crash-window")

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "unknown"
    assert response.json()["replayed"] is True
    assert run_create_calls(harness) == []
    row = await _local_run(harness, "run-key-crash-window")
    assert row.nexus_run_id is None
    assert row.error_code == "nexus_run_mapping_unknown"


async def test_resume_reconciles_the_saved_remote_run_id(harness):
    """Recovery attaches to the saved run instead of creating another one."""

    async with harness.app.state.session_factory() as session:
        row = NexusRun(
            user_id=harness.user_a,
            idempotency_key="run-key-resume-saved",
            request_text="帮我记一下午饭 28 元",
            status="pending",
            nexus_run_id="run_remote_saved",
        )
        session.add(row)
        await session.commit()

    response = await create_ask(harness, token=harness.token_a, key="run-key-resume-saved")

    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["nexus_run_id"] == "run_remote_saved"
    assert payload["workflow_execution_id"] is not None
    assert payload["status"] in {"running", "succeeded"}
    assert run_create_calls(harness) == []
    workflow_calls = [c for c in harness.fake.calls if c["path"].endswith("/agent-workflows")]
    assert len(workflow_calls) == 1
    assert "/runs/run_remote_saved/agent-workflows" in workflow_calls[0]["path"]
