"""Regression: the running NexusPilot App API is header- and body-specific.

These tests intentionally use the current NexusPilot /api/v1/app contract, not
the superseded /principals/resolve + X-NexusPilot-* shape described in older PA
design docs. They fail against the pre-fix client and must pass after the fix.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest_asyncio
from sqlalchemy import select

from assistant_api.core.config import Settings
from assistant_api.core.security import hash_password
from assistant_api.main import create_app
from assistant_api.models import NexusRun, User

from conftest import auth, build_settings, login, seed_user


def _iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class CurrentAppApiFake:
    """Small in-memory implementation of the current App Run + workflow surface."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.workflow_release = asyncio.Event()
        self.auto_complete = True
        self.hold_workflow = False
        self.fail_run_status: int | None = None
        self.fail_workflow_status: int | None = None
        self.raise_timeout = False
        self.final_text = "已经查到：今天适合带伞。"
        self.events = self._events()

    def _events(self) -> list[tuple[int, str, dict]]:
        return [
            (
                1,
                "agent.workflow.started",
                {
                    "event_sequence": 1,
                    "event_type": "agent.workflow.started",
                    "workflow_execution_id": "wf-current-1",
                    "run_id": "run-current-1",
                    "workflow_status": "running",
                    "public_payload": {"execution_profile": "general_tools_v1"},
                },
            ),
            (
                2,
                "agent.workflow.completed",
                {
                    "event_sequence": 2,
                    "event_type": "agent.workflow.completed",
                    "workflow_execution_id": "wf-current-1",
                    "run_id": "run-current-1",
                    "workflow_status": "completed",
                    "public_payload": {
                        "result_path": "/api/v1/app/agent-workflows/wf-current-1/result"
                    },
                },
            ),
        ]

    def _authorized(self, request: httpx.Request) -> bool:
        return (
            request.headers.get("x-application-id") == "personal_assistant"
            and request.headers.get("x-external-user-id")
            and request.headers.get("authorization", "").startswith("Bearer ")
            and not any(key.startswith("x-nexuspilot-") for key in request.headers)
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = request.content.decode() if request.content else ""
        self.calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "headers": dict(request.headers),
                "body": json.loads(body) if body else None,
            }
        )
        if not self._authorized(request):
            return httpx.Response(401, json={"detail": "Invalid application credential"})
        path = request.url.path
        if path == "/api/v1/app/runs" and request.method == "POST":
            if self.raise_timeout:
                raise httpx.ConnectTimeout("simulated timeout")
            if self.fail_run_status is not None:
                return httpx.Response(self.fail_run_status, json={"detail": "upstream"})
            payload = json.loads(body)
            if set(payload) != {"user_request"}:
                return httpx.Response(422, json={"detail": "extra fields"})
            return httpx.Response(
                201,
                json={
                    "run_id": "run-current-1",
                    "user_id": "remote-principal-must-not-leak",
                    "session_id": None,
                    "user_request": payload["user_request"],
                    "run_type": "general",
                    "status": "pending",
                    "budget_limit": None,
                    "cost_used": 0,
                    "started_at": None,
                    "completed_at": None,
                    "created_at": _iso(),
                    "updated_at": _iso(),
                },
            )
        if path == "/api/v1/app/runs/run-current-1/agent-workflows" and request.method == "POST":
            if self.fail_workflow_status is not None:
                return httpx.Response(self.fail_workflow_status, json={"detail": "workflow"})
            payload = json.loads(body)
            required = {
                "workflow_name", "workflow_version", "execution_profile",
                "idempotency_key", "role_bindings", "stream",
            }
            if not required <= set(payload) or "agent_profile" in payload:
                return httpx.Response(422, json={"detail": "wrong workflow body"})
            if payload["workflow_name"] != "general_agent":
                return httpx.Response(422, json={"detail": "workflow name"})
            return httpx.Response(
                201,
                headers={"content-type": "text/event-stream; charset=utf-8"},
                text="".join(self._sse(index) for index in self._send_indices()),
            )
        if path == "/api/v1/app/agent-workflows/wf-current-1/events":
            last = request.headers.get("last-event-id")
            after = int(last) if last and last.isdigit() else 0
            frames = [self._sse(i) for i, _, _ in self._events() if i > after]
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text="".join(frames)
            )
        if path == "/api/v1/app/agent-workflows/wf-current-1" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": "wf-current-1",
                    "run_id": "run-current-1",
                    "workflow_name": "general_agent",
                    "workflow_version": "1.0.0",
                    "execution_profile": "general_tools_v1",
                    "status": "completed",
                },
            )
        if path == "/api/v1/app/agent-workflows/wf-current-1/result":
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": "wf-current-1",
                    "run_id": "run-current-1",
                    "status": "completed",
                    "workflow_name": "general_agent",
                    "workflow_version": "1.0.0",
                    "execution_profile": "general_tools_v1",
                    "final_output": {"final_text": self.final_text},
                    "error": None,
                },
            )
        if path == "/api/v1/app/agent-workflows/wf-current-1/cancel":
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": "wf-current-1",
                    "run_id": "run-current-1",
                    "status": "cancelled",
                },
            )
        return httpx.Response(404, json={"detail": f"unexpected {path}"})

    def _send_indices(self) -> list[int]:
        return [index for index, _, _ in self.events]

    def _sse(self, index: int) -> str:
        for seq, event, data in self.events:
            if seq == index:
                return f"id: {seq}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        raise KeyError(index)


@pytest_asyncio.fixture
async def current_harness():
    fake = CurrentAppApiFake()
    settings = build_settings()
    app = create_app(settings, nexus_transport=httpx.MockTransport(fake.handler))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pa") as client:
            user_a = await seed_user(app, email="cur-a@example.com", display_name="当前甲")
            user_b = await seed_user(app, email="cur-b@example.com", display_name="当前乙")
            yield SimpleNamespace(
                client=client,
                app=app,
                fake=fake,
                settings=settings,
                user_a=user_a,
                user_b=user_b,
                token_a=await login(client, "cur-a@example.com"),
                token_b=await login(client, "cur-b@example.com"),
            )


async def _wait_terminal(harness, local_run_id: str):
    for _ in range(50):
        response = await harness.client.get(
            f"/api/v1/asks/{local_run_id}", headers=auth(harness.token_a)
        )
        if response.status_code == 200 and response.json()["status"] in {
            "succeeded", "failed", "cancelled", "unknown"
        }:
            return response
        await asyncio.sleep(0.02)
    raise AssertionError("run did not reach a terminal local state")


@pytest_asyncio.fixture(autouse=True)
async def _complete_workflows(current_harness):
    yield
    current_harness.fake.workflow_release.set()


async def test_client_uses_current_headers_and_request_bodies(current_harness):
    h = current_harness
    response = await h.client.post(
        "/api/v1/asks",
        json={"text": "今天要带伞吗", "idempotency_key": "current-key-0001"},
        headers=auth(h.token_a),
    )
    assert response.status_code == 202, response.text
    local_run_id = response.json()["local_run_id"]

    run_calls = [c for c in h.fake.calls if c["path"] == "/api/v1/app/runs"]
    assert len(run_calls) == 1
    headers = run_calls[0]["headers"]
    assert headers["x-application-id"] == "personal_assistant"
    assert headers["x-external-user-id"] == h.user_a
    assert not any(key.startswith("x-nexuspilot-") for key in headers)
    assert run_calls[0]["body"] == {"user_request": "今天要带伞吗"}
    assert not any(c["path"].endswith("/principals/resolve") for c in h.fake.calls)

    workflow_calls = [
        c for c in h.fake.calls if c["path"].endswith("/agent-workflows")
    ]
    assert len(workflow_calls) == 1
    workflow_body = workflow_calls[0]["body"]
    assert workflow_body["workflow_name"] == "general_agent"
    assert workflow_body["workflow_version"] == "1.0.0"
    assert workflow_body["execution_profile"] == "general_tools_v1"
    assert workflow_body["stream"] is True
    assert workflow_body["role_bindings"]["controller"]["provider"]
    assert "agent_profile" not in workflow_body
    assert "provider" not in json.dumps(
        (await h.client.get(f"/api/v1/asks/{local_run_id}", headers=auth(h.token_a))).json()
    )


async def test_terminal_answer_is_persisted_and_survives_reconnect(current_harness):
    h = current_harness
    response = await h.client.post(
        "/api/v1/asks",
        json={"text": "查一下今天天气", "idempotency_key": "current-key-0002"},
        headers=auth(h.token_a),
    )
    assert response.status_code == 202
    local_run_id = response.json()["local_run_id"]
    terminal = await _wait_terminal(h, local_run_id)
    body = terminal.json()
    assert body["status"] == "succeeded"
    assert body["answer"] == h.fake.final_text
    assert body["workflow_execution_id"] == "wf-current-1"
    serialized = json.dumps(body)
    assert "remote-owner" not in serialized
    assert "role_bindings" not in serialized
    assert "gpt-4o-mini" not in serialized

    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(NexusRun).where(NexusRun.local_run_id == local_run_id))
    assert row is not None
    assert row.result_json and row.result_json["final_text"] == h.fake.final_text
    assert row.nexus_principal_id is None


async def test_duplicate_key_replays_one_remote_run(current_harness):
    h = current_harness
    first = await h.client.post(
        "/api/v1/asks",
        json={"text": "重复", "idempotency_key": "current-key-0003"},
        headers=auth(h.token_a),
    )
    second = await h.client.post(
        "/api/v1/asks",
        json={"text": "重复", "idempotency_key": "current-key-0003"},
        headers=auth(h.token_a),
    )
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["local_run_id"] == second.json()["local_run_id"]
    assert second.json()["replayed"] is True
    assert len([c for c in h.fake.calls if c["path"] == "/api/v1/app/runs"]) == 1


async def test_cross_user_cannot_read_or_cancel(current_harness):
    h = current_harness
    created = await h.client.post(
        "/api/v1/asks",
        json={"text": "私有", "idempotency_key": "current-key-0004"},
        headers=auth(h.token_a),
    )
    local_run_id = created.json()["local_run_id"]
    read = await h.client.get(f"/api/v1/asks/{local_run_id}", headers=auth(h.token_b))
    cancel = await h.client.post(
        f"/api/v1/asks/{local_run_id}/cancel", headers=auth(h.token_b)
    )
    events = await h.client.get(
        f"/api/v1/asks/{local_run_id}/events", headers=auth(h.token_b)
    )
    assert read.status_code == 404
    assert cancel.status_code == 404
    assert events.status_code == 404
    assert not any(c["path"].endswith("/cancel") for c in h.fake.calls)
    assert not any(c["path"].endswith("/events") for c in h.fake.calls)


async def test_workflow_start_failure_is_visible_and_not_streamable(current_harness):
    h = current_harness
    h.fake.fail_workflow_status = 422
    response = await h.client.post(
        "/api/v1/asks",
        json={"text": "坏工作流", "idempotency_key": "current-key-0005"},
        headers=auth(h.token_a),
    )
    # A server-side body rejected by NexusPilot is a bad-gateway condition, not a
    # client validation error; the stable code identifies the cause.
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "nexus_invalid_request"

    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(NexusRun))
    assert row is not None
    assert row.status == "failed"
    assert row.workflow_execution_id is None
    assert row.error_code == "nexus_invalid_request"


async def test_refresh_reconstructs_answer_without_the_original_stream(current_harness):
    """A fresh GET recovers status/result from NexusPilot, not from memory."""

    h = current_harness
    created = await h.client.post(
        "/api/v1/asks",
        json={"text": "刷新后还要看到", "idempotency_key": "current-key-0006"},
        headers=auth(h.token_a),
    )
    assert created.status_code == 202
    local_run_id = created.json()["local_run_id"]
    await _wait_terminal(h, local_run_id)

    # Simulate a process restart that lost the local terminal result but kept the
    # durable workflow id. The read path must reconcile from NexusPilot.
    async with h.app.state.session_factory() as db:
        row = await db.scalar(select(NexusRun).where(NexusRun.local_run_id == local_run_id))
        row.status = "running"
        row.result_json = None
        row.completed_at = None
        await db.commit()

    refreshed = await h.client.get(
        f"/api/v1/asks/{local_run_id}", headers=auth(h.token_a)
    )
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["status"] == "succeeded"
    assert body["answer"] == h.fake.final_text
    assert body["completed_at"] is not None
