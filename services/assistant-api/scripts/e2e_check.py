"""Real-HTTP end-to-end check for Personal Assistant + NexusPilot application API.

Starts the stub upstream and the assistant API as separate processes, then drives the
public surface over the network. This is deliberately not a unit test: an in-process
transport cannot prove chunked SSE delivery, header propagation or process startup.

Usage:
    .venv/bin/python scripts/e2e_check.py
Exit code 0 means every check passed.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

ROOT = Path(__file__).resolve().parents[1]
STUB_DIR = ROOT.parent / "nexus-stub"
APP_TOKEN = "e2e-application-token-32-characters"
CALLBACK_TOKEN = "e2e-callback-token-32-characters"
EMAIL = "e2e@example.com"
PASSWORD = "e2e-password-1"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn: Callable[[], str]) -> None:
    """Run one assertion group, recording the outcome instead of aborting early."""

    try:
        detail = fn()
        results.append((name, True, detail))
    except Exception as error:  # noqa: BLE001 - the report needs every failure
        results.append((name, False, f"{type(error).__name__}: {error}"))


def free_port() -> int:
    """Ask the OS for an unused port so repeated runs do not collide."""

    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(base: str, timeout: float = 25.0) -> None:
    """Block until the service answers, failing with the last transport error."""

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            response = httpx.get(f"{base}/health", timeout=1.0, trust_env=False)
            if response.status_code == 200:
                return
        except httpx.HTTPError as error:
            last = error
        time.sleep(0.2)
    raise RuntimeError(f"{base} did not become healthy: {last}")


def frames(response: httpx.Response):
    """Yield (id, event, data, completed_at) for each SSE frame."""

    event_id: str | None = None
    event_name: str | None = None
    data: list[str] = []
    for line in response.iter_lines():
        if line == "":
            if data:
                yield event_id, event_name, "\n".join(data), time.monotonic()
            event_id, event_name, data = None, None, []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            event_id = value
        elif field == "event":
            event_name = value
        elif field == "data":
            data.append(value)
    if data:
        yield event_id, event_name, "\n".join(data), time.monotonic()


def main() -> int:
    """Boot both services, run every check, and print a pass/fail table."""

    workdir = Path(tempfile.mkdtemp(prefix="pa-e2e-"))
    db_path = workdir / "e2e.db"
    stub_port, api_port = free_port(), free_port()
    stub_base = f"http://127.0.0.1:{stub_port}"
    api_base = f"http://127.0.0.1:{api_port}"

    env = {
        **os.environ,
        "ASSISTANT_ENVIRONMENT": "development",
        "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{db_path}",
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
        "ASSISTANT_NEXUS_BASE_URL": stub_base,
        "ASSISTANT_NEXUS_APPLICATION_ID": "personal_assistant",
        "ASSISTANT_NEXUS_APPLICATION_TOKEN": APP_TOKEN,
        "ASSISTANT_TOOL_CALLBACK_TOKEN": CALLBACK_TOKEN,
        "ASSISTANT_PROVISION_PASSWORD": PASSWORD,
        "STUB_APP_ID": "personal_assistant",
        "STUB_APP_TOKEN": APP_TOKEN,
        "STUB_STREAM_DELAY_MS": "70",
        "PYTHONUNBUFFERED": "1",
        # Loopback must never travel through an ambient proxy, including in children.
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }

    stub_log = workdir / "stub.log"
    api_log = workdir / "api.log"
    stub = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--port", str(stub_port), "--log-level", "warning"],
        cwd=STUB_DIR, env=env, stdout=stub_log.open("w"), stderr=subprocess.STDOUT,
    )
    api = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "assistant_api.main:app", "--port", str(api_port),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=api_log.open("w"), stderr=subprocess.STDOUT,
    )

    try:
        wait_health(stub_base)
        wait_health(api_base)
        assert api.poll() is None, f"assistant API exited early; log: {api_log.read_text()[-1500:]}"

        seed = subprocess.run(
            [sys.executable, "-m", "assistant_api.commands.seed_dev_user",
             "--email", EMAIL, "--display-name", "端到端", "--with-sample-data"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        if seed.returncode != 0:
            raise RuntimeError(f"seed failed: {seed.stdout}\n{seed.stderr}")
        seed_b = subprocess.run(
            [sys.executable, "-m", "assistant_api.commands.seed_dev_user",
             "--email", "e2e-b@example.com", "--display-name", "端到端B", "--with-sample-data"],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
        )
        if seed_b.returncode != 0:
            raise RuntimeError(f"seed B failed: {seed_b.stdout}\n{seed_b.stderr}")

        # trust_env=False: otherwise a developer proxy silently intercepts loopback.
        client = httpx.Client(base_url=api_base, timeout=20.0, trust_env=False)
        upstream = httpx.Client(base_url=stub_base, timeout=10.0, trust_env=False)
        state: dict[str, Any] = {}

        def health() -> str:
            body = client.get("/health").json()
            assert body["nexus_application"] == "personal_assistant", body
            return f"api ok, nexus={body['nexus_application']}"

        def api_surface() -> str:
            spec = client.get("/openapi.json").json()
            paths = sorted(spec["paths"])
            assert "/api/v1/auth/login" in paths, paths
            return f"{len(paths)} paths"

        def login_rejects_wrong_password() -> str:
            response = client.post("/api/v1/auth/login",
                                   json={"email": EMAIL, "password": "wrong-password"})
            assert response.status_code == 401, response.text
            code = response.json()["error"]["code"]
            assert code == "unauthenticated", code
            return "401 unauthenticated"

        def login_and_me() -> str:
            response = client.post("/api/v1/auth/login",
                                   json={"email": EMAIL, "password": PASSWORD})
            assert response.status_code == 200, response.text
            token = response.json()["token"]
            state["token"] = token
            state["headers"] = {"Authorization": f"Bearer {token}"}
            me = client.get("/api/v1/auth/me", headers=state["headers"])
            assert me.status_code == 200, me.text
            body = me.json()
            assert set(body) == {"user_id", "email", "display_name"}, body
            state["user_id"] = body["user_id"]
            return f"user={body['user_id'][:8]}… fields=3"

        def unauthenticated_ask() -> str:
            response = client.post("/api/v1/asks", json={"text": "x", "idempotency_key": "anon-key-0001"})
            assert response.status_code == 401, response.text
            return "401"

        def identity_in_body_rejected() -> str:
            response = client.post(
                "/api/v1/asks",
                json={"text": "x", "idempotency_key": "extra-key-0001", "user_id": state["user_id"]},
                headers=state["headers"],
            )
            assert response.status_code == 422, response.text
            return "422 extra field rejected"

        def create_ask() -> str:
            response = client.post(
                "/api/v1/asks",
                json={"text": "昨天下午理发 35 元", "idempotency_key": "e2e-ask-0001"},
                headers=state["headers"],
            )
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["status"] == "running", body
            assert body["workflow_execution_id"], body
            state["ask"] = body
            return f"202 status={body['status']} run={body['nexus_run_id']}"

        def stream_events() -> str:
            started = time.monotonic()
            received: list[tuple[str, str, str, float]] = []
            with client.stream(
                "GET",
                f"/api/v1/asks/{state['ask']['local_run_id']}/events",
                headers={**state["headers"], "Accept": "text/event-stream"},
            ) as response:
                assert response.status_code == 200, response.status_code
                for frame in frames(response):
                    received.append(frame)
            assert len(received) >= 5, f"frames={len(received)}"
            ids = [int(item[0]) for item in received if item[0] and item[0].isdigit()]
            assert ids == sorted(ids) and len(ids) == len(set(ids)), ids
            assert ids[0] == 1, ids
            first_at = received[0][3]
            last_at = received[-1][3]
            assert last_at - first_at > 0.15, f"stream not incremental: {last_at - first_at:.3f}s"
            payload = json.loads(received[-1][2])
            assert payload["workflow_status"] == "completed", payload
            state["last_event_id"] = str(ids[-1])
            return (f"{len(received)} frames, ids {ids[0]}..{ids[-1]}, "
                    f"spread={last_at - first_at:.2f}s, ttfb={first_at - started:.2f}s")

        def resume_after_last_event() -> str:
            received: list[str] = []
            with client.stream(
                "GET",
                f"/api/v1/asks/{state['ask']['local_run_id']}/events",
                headers={**state["headers"], "Last-Event-ID": "3"},
            ) as response:
                for frame in frames(response):
                    received.append(frame[0] or "")
            assert received == ["4", "5"], received
            return f"resumed at 4, got {received}"

        def run_projection() -> str:
            response = client.get(f"/api/v1/asks/{state['ask']['local_run_id']}",
                                  headers=state["headers"])
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["status"] == "succeeded", body
            assert body["last_event_sequence"] == 5, body
            assert body["answer"] == "已记住：昨天下午理发，35 元。", body
            return (f"status={body['status']} last_seq={body['last_event_sequence']} "
                    f"answer={body['answer'][:10]}…")

        def duplicate_submit() -> str:
            response = client.post(
                "/api/v1/asks",
                json={"text": "昨天下午理发 35 元", "idempotency_key": "e2e-ask-0001"},
                headers=state["headers"],
            )
            assert response.status_code == 202, response.text
            body = response.json()
            assert body["replayed"] is True, body
            assert body["local_run_id"] == state["ask"]["local_run_id"], body
            return "replayed=true, same local run"


        def lost_create_response() -> str:
            """Recover with only text + idempotency key; no local_run_id was kept."""
            key = "e2e-lost-create-0001"
            text = "lost create response e2e"
            before = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            first = client.post(
                "/api/v1/asks", json={"text": text, "idempotency_key": key},
                headers=state["headers"],
            )
            assert first.status_code == 202, first.text
            # Deliberately drop first.json(): the client only knows text + key.
            recovered = client.post(
                "/api/v1/asks", json={"text": text, "idempotency_key": key},
                headers=state["headers"],
            )
            assert recovered.status_code == 202, recovered.text
            body = recovered.json()
            assert body["replayed"] is True, body
            after = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            assert len(after) == len(before) + 1, (len(before), len(after))
            for _ in range(60):
                read = client.get(
                    f"/api/v1/asks/{body['local_run_id']}", headers=state["headers"]
                ).json()
                if read["status"] == "succeeded":
                    break
                time.sleep(0.1)
            assert read["status"] == "succeeded", read
            return "same local run, one upstream Run"

        def lost_create_response_at_transport() -> str:
            """Drop the 202 after PA committed it; replay text+key, not a fake pre-send loss."""

            key = "e2e-lost-transport-0001"
            text = "transport-level lost create response e2e"
            before = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            listener = socket.socket()
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(15)
            proxy_port = listener.getsockname()[1]
            captured: dict[str, bytes] = {}

            def serve_once() -> None:
                connection, _ = listener.accept()
                try:
                    request = b""
                    while b"\r\n\r\n" not in request:
                        request += connection.recv(65536)
                    head, _, body = request.partition(b"\r\n\r\n")
                    length = 0
                    for line in head.split(b"\r\n"):
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1].strip())
                    while len(body) < length:
                        body += connection.recv(65536)
                    forwarded = socket.create_connection(("127.0.0.1", api_port), timeout=15)
                    forwarded.sendall(head + b"\r\n\r\n" + body)
                    response = b""
                    while True:
                        chunk = forwarded.recv(65536)
                        if not chunk:
                            break
                        response += chunk
                    captured["response"] = response
                    forwarded.close()
                finally:
                    # The server has already committed the run; dropping the
                    # bytes here is the failure position the client must recover from.
                    connection.close()

            thread = threading.Thread(target=serve_once, daemon=True)
            thread.start()
            raw = socket.create_connection(("127.0.0.1", proxy_port), timeout=15)
            payload = json.dumps({"text": text, "idempotency_key": key}).encode()
            raw.sendall(
                (
                    "POST /api/v1/asks HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{proxy_port}\r\n"
                    f"Authorization: {state['headers']['Authorization']}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode()
                + payload
            )
            raw.settimeout(15)
            try:
                delivered = raw.recv(1)
            except socket.timeout:
                delivered = b""
            raw.close()
            thread.join(timeout=15)
            listener.close()
            assert delivered == b"", delivered
            assert captured.get("response", b"").startswith(b"HTTP/1.1 202"), captured

            recovered = client.post(
                "/api/v1/asks", json={"text": text, "idempotency_key": key},
                headers=state["headers"],
            )
            assert recovered.status_code == 202, recovered.text
            body = recovered.json()
            assert body["replayed"] is True, body
            after = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            assert len(after) == len(before) + 1, (len(before), len(after))
            # Let this run consume its terminal frame before the next test arms
            # the stub's one-shot unknown mode; otherwise it steals that mode.
            for _ in range(60):
                read = client.get(
                    f"/api/v1/asks/{body['local_run_id']}", headers=state["headers"]
                ).json()
                if read["status"] in {"succeeded", "failed", "cancelled"}:
                    break
                time.sleep(0.1)
            assert read["status"] == "succeeded", read
            return "202 dropped after commit; replay found the same run, one upstream Run"

        def cancel_flow() -> str:
            created = client.post(
                "/api/v1/asks",
                json={"text": "帮我看看周六安排", "idempotency_key": "e2e-ask-0002"},
                headers=state["headers"],
            ).json()
            cancelled = client.post(
                f"/api/v1/asks/{created['local_run_id']}/cancel", headers=state["headers"]
            )
            assert cancelled.status_code == 200, cancelled.text
            assert cancelled.json()["status"] == "cancelled", cancelled.text
            return "cancelled"


        def unknown_recovery() -> str:
            """Hold a terminal result in unknown, then recover via GET, no new Run."""
            before = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            upstream.post("/_debug/unknown_hold")
            created = client.post(
                "/api/v1/asks",
                json={"text": "unknown recovery e2e", "idempotency_key": "e2e-unknown-0001"},
                headers=state["headers"],
            ).json()
            local_id = created["local_run_id"]
            seen = None
            for _ in range(60):
                seen = client.get(f"/api/v1/asks/{local_id}", headers=state["headers"]).json()
                if seen["status"] == "unknown":
                    break
                if seen["status"] in {"succeeded", "failed", "cancelled"}:
                    raise AssertionError(f"expected unknown, got {seen['status']}")
                time.sleep(0.1)
            assert seen["status"] == "unknown", seen
            again = client.get(f"/api/v1/asks/{local_id}", headers=state["headers"]).json()
            assert again["status"] == "unknown", again
            upstream.post("/_debug/unknown_recover")
            for _ in range(60):
                recovered = client.get(
                    f"/api/v1/asks/{local_id}", headers=state["headers"]
                ).json()
                if recovered["status"] == "succeeded":
                    break
                time.sleep(0.1)
            assert recovered["status"] == "succeeded", recovered
            assert recovered["answer"], recovered
            after = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            assert len(after) == len(before) + 1, (len(before), len(after))
            return "unknown -> GET recovery, one upstream Run"

        def cross_user_same_key() -> str:
            """The same idempotency key is scoped per user, never global."""

            login_b = client.post(
                "/api/v1/auth/login",
                json={"email": "e2e-b@example.com", "password": PASSWORD},
            )
            assert login_b.status_code == 200, login_b.text
            headers_b = {"Authorization": "Bearer " + login_b.json()["token"]}
            me_b = client.get("/api/v1/auth/me", headers=headers_b)
            assert me_b.status_code == 200, me_b.text
            state["user_b"] = me_b.json()["user_id"]
            before = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            key = "e2e-shared-user-key-0001"
            text = "same key different local users"
            first = client.post(
                "/api/v1/asks", json={"text": text, "idempotency_key": key},
                headers=state["headers"],
            )
            second = client.post(
                "/api/v1/asks", json={"text": text, "idempotency_key": key},
                headers=headers_b,
            )
            assert first.status_code == 202 and second.status_code == 202, (first.text, second.text)
            assert first.json()["replayed"] is False and second.json()["replayed"] is False
            assert first.json()["local_run_id"] != second.json()["local_run_id"]
            after = [
                call for call in upstream.get("/_debug/calls").json()["calls"]
                if call["path"] == "/api/v1/app/runs" and call["method"] == "POST"
            ]
            assert len(after) == len(before) + 2, (len(before), len(after))
            return "two users, same key: two local runs, two upstream Runs"

        def tool_callback_flow() -> str:
            """Call the business tools exactly as the current RemoteToolAdapter would."""

            headers = {"Authorization": f"Bearer {CALLBACK_TOKEN}"}

            def envelope(execution: str, tool: str, arguments: dict) -> dict:
                return {
                    "tool_execution_id": execution,
                    "application_id": "personal_assistant",
                    "external_user_id": state["user_id"],
                    "tool_name": tool,
                    "tool_version": "1.0.0",
                    "arguments": arguments,
                }

            tasks = client.post(
                "/internal/nexus/tools",
                json=envelope("e2e-call-0001", "personal_assistant.list_tasks",
                              {"limit": 10}),
                headers=headers, timeout=10.0,
            )
            assert tasks.status_code == 200, tasks.text
            assert set(tasks.json()) == {"items", "next_cursor"}, tasks.text
            items = tasks.json()["items"]
            assert items and items[0]["title"] == "确认周末交通", items

            notes_env = envelope(
                "e2e-call-0002", "personal_assistant.search_notes", {"query": "体检", "limit": 10}
            )
            notes = client.post(
                "/internal/nexus/tools", json=notes_env, headers=headers, timeout=10.0
            )
            assert notes.status_code == 200, notes.text
            assert set(notes.json()) == {"items", "next_cursor"}
            note_id = notes.json()["items"][0]["note_id"]

            replay = client.post(
                "/internal/nexus/tools", json=notes_env, headers=headers, timeout=10.0
            )
            assert replay.headers["x-idempotent-replay"] == "true", replay.headers
            assert replay.json() == notes.json(), "replay payload drifted"

            calendar = client.post(
                "/internal/nexus/tools",
                json=envelope(
                    "e2e-call-0003",
                    "personal_assistant.list_calendar_events",
                    {"start_date": "2026-10-01", "end_date": "2026-10-31", "limit": 10},
                ),
                headers=headers, timeout=10.0,
            )
            assert calendar.status_code == 200, calendar.text
            assert set(calendar.json()) == {"items", "next_cursor"}
            assert calendar.json()["items"], calendar.text
            return f"3 tools ok, note={note_id[:8]}…, replay cached"

        def memory_recall() -> str:
            response = client.post(
                "/api/v1/memory/search",
                json={"query_text": "提醒", "limit": 5, "idempotency_key": "e2e-mem-0001"},
                headers=state["headers"],
            )
            assert response.status_code == 200, response.text
            items = response.json()["items"]
            assert len(items) == 2, items
            assert all(item.get("source_reference") for item in items), items
            return f"{len(items)} hits with sources"

        def upstream_contract_observance() -> str:
            """Assert what the client never sent, which matters more than what it did."""

            calls = upstream.get("/_debug/calls").json()["calls"]
            assert calls, "stub recorded no calls"
            run_calls = [call for call in calls if call["path"] == "/api/v1/app/runs"
                         and call["method"] == "POST"]
            assert run_calls, "no run creation observed"
            for call in calls:
                try:
                    payload = json.loads(call["body"] or "{}")
                except ValueError:
                    payload = {}
                # Exact key check: `external_user_id` is the agreed field and must
                # not be mistaken for a client-declared owner.
                assert "user_id" not in payload, f"user_id leaked to {call['path']} {payload}"
                assert "principal_id" not in payload, f"principal_id leaked to {call['path']}"
                if call["path"].startswith("/api/v1/app/"):
                    assert call["headers"]["x-application-id"] == "personal_assistant", call
                    allowed_users = {state["user_id"], state.get("user_b")}
                    assert call["headers"]["x-external-user-id"] in allowed_users, call
                    assert "x-nexuspilot-application" not in call["headers"], call
                    assert "x-nexuspilot-external-user" not in call["headers"], call
            assert not [c for c in calls if c["path"].endswith("/principals/resolve")]
            workflow_calls = [c for c in calls if c["path"].endswith("/agent-workflows")
                              and c["method"] == "POST"]
            assert workflow_calls, "no workflow creation observed"
            workflow_body = json.loads(workflow_calls[0]["body"])
            assert workflow_body["workflow_name"] == "general_agent"
            assert workflow_body["workflow_version"] == "1.0.0"
            assert workflow_body["execution_profile"] == "general_tools_v1"
            assert workflow_body["stream"] is True
            assert "agent_profile" not in workflow_body
            return f"{len(calls)} upstream calls, current headers, no identity in body"

        check("health", health)
        check("api surface", api_surface)
        check("login rejects wrong password", login_rejects_wrong_password)
        check("login + me", login_and_me)
        check("ask requires session", unauthenticated_ask)
        check("identity field in body rejected", identity_in_body_rejected)
        check("create ask", create_ask)
        check("SSE streaming", stream_events)
        check("resume with Last-Event-ID", resume_after_last_event)
        check("run projection", run_projection)
        check("duplicate submit replays", duplicate_submit)
        check("lost create response", lost_create_response)
        check("lost 202 at transport", lost_create_response_at_transport)
        check("cancel", cancel_flow)
        check("unknown recovery", unknown_recovery)
        check("same key across users", cross_user_same_key)
        check("business tools + replay", tool_callback_flow)
        check("memory recall", memory_recall)
        check("upstream contract", upstream_contract_observance)
    finally:
        for process in (api, stub):
            process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()

    width = max(len(name) for name, _, _ in results)
    print()
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name:<{width}}  {detail}")
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    if passed != len(results):
        print("\n--- service logs ---")
        for path, label in ((api_log, "assistant-api"), (stub_log, "nexus-stub")):
            if path.exists() and path.read_text().strip():
                print(f"[{label}]\n{path.read_text()[-2000:]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
