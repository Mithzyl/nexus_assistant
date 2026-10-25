"""Isolated app, database and fake NexusPilot for the backend contract tests.

No real network, MySQL or MinIO is used. The fake records every outbound request
so a test can assert what Personal Assistant did *not* send (user id in a body,
provider selection, principal id).
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest_asyncio

from assistant_api.core.config import Settings
from assistant_api.core.security import hash_password
from assistant_api.main import create_app
from assistant_api.models import BusinessRecord, Note, ScheduleEvent, TaskItem, User

DEFAULT_SSE = (
    'id: 1\nevent: agent.workflow.started\ndata: {"status": "running"}\n\n'
    'id: 2\nevent: agent.node.completed\ndata: {"node": "controller", "status": "running"}\n\n'
    'id: 3\nevent: agent.workflow.completed\ndata: {"workflow_status": "succeeded"}\n\n'
)


def _principal_for(external_user_id: str) -> str:
    """Return a deterministic principal id so repeated resolves look stable."""

    return "prin_" + hashlib.sha256(external_user_id.encode()).hexdigest()[:12]


class FakeNexus:
    """In-memory current NexusPilot App API with controllable streaming.

    The current server authenticates with X-Application-Id/X-External-User-Id,
    creates the app user while authenticating, starts a general_agent workflow
    with stream=true, and exposes finite persisted event replay plus a result.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.sse_body = self._default_sse("run_remote_1", "wf_remote_1")
        self.fail_run_create_status: int | None = None
        self.fail_stream_status: int | None = None
        self.fail_workflow_status: int | None = None
        self.raise_timeout_on_run_create = False
        self.auto_complete_workflow = True
        self.workflow_release = asyncio.Event()
        self.run_seq = 0
        self.workflow_seq = 0
        self.final_text = "已收到你的问题。"

    @staticmethod
    def _default_sse(run_id: str, workflow_id: str) -> str:
        events = [
            (1, "agent.workflow.started", "running", {}),
            (2, "agent.node.completed", "running", {}),
            (
                3,
                "agent.workflow.completed",
                "completed",
                {"result_path": f"/api/v1/app/agent-workflows/{workflow_id}/result"},
            ),
        ]
        frames = []
        for sequence, event, status, payload in events:
            data = {
                "event_sequence": sequence,
                "event_type": event,
                "workflow_execution_id": workflow_id,
                "run_id": run_id,
                "workflow_status": status,
                "public_payload": payload,
            }
            frames.append(
                f"id: {sequence}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            )
        return "".join(frames)

    def release_workflow(self) -> None:
        self.auto_complete_workflow = True
        self.workflow_release.set()

    async def handler(self, request: httpx.Request) -> httpx.Response:
        """Answer one current App API call and record headers/body for assertions."""

        path = request.url.path
        body = request.content.decode() if request.content else ""
        self.calls.append(
            {
                "method": request.method,
                "path": path,
                "headers": dict(request.headers),
                "body": body,
            }
        )
        if request.headers.get("x-application-id") != "personal_assistant":
            return httpx.Response(401, json={"detail": "Invalid application credential"})
        if not request.headers.get("x-external-user-id"):
            return httpx.Response(422, json={"detail": "Application subject is required"})
        if path == "/api/v1/app/principals/resolve":
            return httpx.Response(404, json={"detail": "Not Found"})

        if path == "/api/v1/app/runs" and request.method == "POST":
            if self.raise_timeout_on_run_create:
                raise httpx.ConnectTimeout("simulated timeout")
            if self.fail_run_create_status is not None:
                return httpx.Response(
                    self.fail_run_create_status, json={"detail": "upstream refused"}
                )
            payload = json.loads(body)
            if set(payload) != {"user_request"}:
                return httpx.Response(422, json={"detail": "extra fields forbidden"})
            self.run_seq += 1
            run_id = f"run_remote_{self.run_seq}"
            now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            return httpx.Response(
                201,
                json={
                    "run_id": run_id,
                    "user_id": "remote-owner-not-returned-to-client",
                    "session_id": None,
                    "user_request": payload["user_request"],
                    "run_type": "general",
                    "status": "pending",
                    "budget_limit": None,
                    "cost_used": 0,
                    "started_at": None,
                    "completed_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )

        if path.endswith("/agent-workflows") and request.method == "POST":
            if self.fail_workflow_status is not None:
                return httpx.Response(
                    self.fail_workflow_status, json={"detail": "workflow refused"}
                )
            payload = json.loads(body)
            required = {
                "workflow_name",
                "workflow_version",
                "execution_profile",
                "idempotency_key",
                "role_bindings",
                "stream",
            }
            if not required <= set(payload) or "agent_profile" in payload:
                return httpx.Response(422, json={"detail": "invalid workflow body"})
            run_id = path.split("/")[6]
            self.workflow_seq += 1
            workflow_id = f"wf_remote_{self.workflow_seq}"
            first = (
                "id: 1\nevent: agent.workflow.started\n"
                'data: {"event_sequence":1,"event_type":"agent.workflow.started",'
                f'"workflow_execution_id":"{workflow_id}","run_id":"{run_id}",'
                '"workflow_status":"running","public_payload":{}}\n\n'
            )
            terminal = (
                "id: 3\nevent: agent.workflow.completed\n"
                'data: {"event_sequence":3,"event_type":"agent.workflow.completed",'
                f'"workflow_execution_id":"{workflow_id}","run_id":"{run_id}",'
                '"workflow_status":"completed","public_payload":'
                f'{{"result_path":"/api/v1/app/agent-workflows/{workflow_id}/result"}}}}\n\n'
            )
            return httpx.Response(
                201,
                headers={"content-type": "text/event-stream"},
                stream=_WorkflowStream(self, first, terminal),
            )

        workflow_id = None
        parts = path.split("/")
        if "agent-workflows" in parts:
            index = parts.index("agent-workflows")
            if index + 1 < len(parts):
                workflow_id = parts[index + 1]

        if path.endswith("/events") and workflow_id:
            if self.fail_stream_status is not None:
                return httpx.Response(
                    self.fail_stream_status, json={"detail": "stream refused"}
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=self.sse_body,
            )

        if path.endswith("/result") and workflow_id:
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": workflow_id,
                    "run_id": "run_remote_1",
                    "workflow_name": "general_agent",
                    "workflow_version": "1.0.0",
                    "execution_profile": "general_tools_v1",
                    "status": "completed",
                    "final_output": {"final_text": self.final_text},
                    "error": None,
                },
            )

        if path.endswith("/cancel") and workflow_id:
            self.workflow_release.set()
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": workflow_id,
                    "run_id": "run_remote_1",
                    "status": "cancelled",
                },
            )

        if "agent-workflows" in path and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "workflow_execution_id": workflow_id,
                    "run_id": "run_remote_1",
                    "status": "completed",
                },
            )

        if path == "/api/v1/app/memory-retrievals":
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "memory_id": "mem_1",
                            "memory_type": "user_preference",
                            "content": "用户偏好提前一天收到提醒",
                            "source_reference": "conversation_1/message_3",
                        }
                    ]
                },
            )

        return httpx.Response(404, json={"detail": f"unexpected path {path}"})


class _WorkflowStream(httpx.AsyncByteStream):
    """Yield an opening frame, then optionally hold the live workflow open."""

    def __init__(self, fake: FakeNexus, first: str, terminal: str) -> None:
        self.fake = fake
        self.first = first
        self.terminal = terminal

    async def __aiter__(self):
        yield self.first.encode()
        if not self.fake.auto_complete_workflow:
            await self.fake.workflow_release.wait()
        if self.fake.auto_complete_workflow:
            yield self.terminal.encode()

def build_settings() -> Settings:
    """Return explicit settings so environment variables cannot change a test run."""

    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        nexus_base_url="http://nexus.test",
        nexus_application_id="personal_assistant",
        nexus_application_token="test-application-token-32-characters",
        tool_callback_token="test-tool-callback-token-32-characters",
        allow_schema_create=True,
        tool_result_max_chars=600,
    )


async def seed_user(app, *, email: str, display_name: str, password: str = "secret-pass"):
    """Insert one local account with a real scrypt verifier."""

    async with app.state.session_factory() as session:
        user = User(
            email=email,
            display_name=display_name,
            password_verifier=hash_password(password),
            status="active",
        )
        session.add(user)
        await session.commit()
        return user.user_id


async def seed_business_data(app, user_id: str) -> None:
    """Give one user a small, identifiable data set used by the tool tests."""

    now = datetime.now(UTC)
    async with app.state.session_factory() as session:
        session.add_all(
            [
                TaskItem(user_id=user_id, title="交物业费", status="open", due_at=now),
                Note(user_id=user_id, title="体检报告", body="血压正常，建议半年复查。" * 80),
                ScheduleEvent(
                    user_id=user_id, title="牙医复诊", starts_at=now, location="口腔医院"
                ),
                BusinessRecord(
                    user_id=user_id,
                    kind="expense",
                    title="午饭",
                    amount=28,
                    occurred_at=now,
                    payload_json={},
                ),
            ]
        )
        await session.commit()


async def login(client: httpx.AsyncClient, email: str, password: str = "secret-pass"):
    """Authenticate through the public endpoint and return the bearer token."""

    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def tool_auth(settings: Settings) -> dict[str, str]:
    """Return the service token that NexusPilot's RemoteToolAdapter sends."""

    return {"Authorization": f"Bearer {settings.tool_callback_token.get_secret_value()}"}


def tool_envelope(
    *,
    external_user_id: str,
    tool_execution_id: str,
    tool_name: str,
    arguments: dict | None = None,
    tool_version: str = "1.0.0",
    application_id: str = "personal_assistant",
) -> dict:
    """Build the current flat NexusPilot tool envelope (no identity headers)."""

    return {
        "tool_execution_id": tool_execution_id,
        "application_id": application_id,
        "external_user_id": external_user_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "arguments": arguments or {},
    }


@pytest_asyncio.fixture
async def harness():
    """Yield a running app, two logged-in users, and the fake upstream."""

    fake = FakeNexus()
    settings = build_settings()
    app = create_app(settings, nexus_transport=httpx.MockTransport(fake.handler))

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pa") as client:
            user_a = await seed_user(app, email="a@example.com", display_name="甲")
            user_b = await seed_user(app, email="b@example.com", display_name="乙")
            await seed_business_data(app, user_a)
            await seed_business_data(app, user_b)

            yield SimpleNamespace(
                client=client,
                app=app,
                fake=fake,
                settings=settings,
                user_a=user_a,
                user_b=user_b,
                token_a=await login(client, "a@example.com"),
                token_b=await login(client, "b@example.com"),
            )


def auth(token: str) -> dict[str, str]:
    """Return the bearer header for a logged-in local user."""

    return {"Authorization": f"Bearer {token}"}


async def create_ask(harness, *, token: str, key: str, text: str = "帮我记一下午饭 28 元"):
    """Submit one ask through the public endpoint and return the raw response."""

    return await harness.client.post(
        "/api/v1/asks",
        json={"text": text, "idempotency_key": key},
        headers=auth(token),
    )


@pytest_asyncio.fixture
async def commute_harness():
    """Fixed clock plus controllable weather/ETA/push providers and two users."""

    from tests.commute_support import (
        FakeAdviceTextProvider,
        FakeBark,
        FakeEtaProvider,
        FakePushProvider,
        FakeWeatherProvider,
        FixedClock,
        build_test_runtime,
    )

    clock = FixedClock(datetime(2026, 10, 23, 0, 0, tzinfo=UTC))
    fake = FakeNexus()
    settings = build_settings()
    weather = FakeWeatherProvider(now=clock)
    eta = FakeEtaProvider(now=clock)
    push = FakePushProvider()
    bark = FakeBark()
    advice_text = FakeAdviceTextProvider()
    runtime = build_test_runtime(
        settings,
        clock=clock,
        weather=weather,
        eta=eta,
        advice_text=advice_text,
        push=push,
        bark=bark,
    )
    app = create_app(
        settings,
        nexus_transport=httpx.MockTransport(fake.handler),
        commute_runtime=runtime,
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://pa") as client:
            user_a = await seed_user(app, email="commuter-a@example.com", display_name="通勤甲")
            user_b = await seed_user(app, email="commuter-b@example.com", display_name="通勤乙")
            from assistant_api.models import BarkReminderSetting
            async with app.state.session_factory() as db:
                db.add(BarkReminderSetting(user_id=user_a, auto_reminders_enabled=True))
                await db.commit()
            yield SimpleNamespace(
                client=client,
                app=app,
                fake=fake,
                settings=settings,
                clock=clock,
                runtime=runtime,
                weather=weather,
                eta=eta,
                push=push,
                bark=bark,
                advice_text=advice_text,
                user_a=user_a,
                user_b=user_b,
                token_a=await login(client, "commuter-a@example.com"),
                token_b=await login(client, "commuter-b@example.com"),
            )
