"""Real-HTTP backend integration check for the commute module.

This script does not touch the iOS App. It starts the API plus one-shot
scheduler/worker processes against a throwaway SQLite database, provisions two
local users, and exercises the persisted commute flow end to end:

login -> plan create/read/update/conflict -> manual check -> queued Advisory
-> devices -> action/idempotency -> auto scheduler/worker -> outbox -> delete.

Mock providers are used explicitly; the providers remain labelled `mock` and no
Apple network call is made. Real WeatherKit/Maps/APNs need credentials and are
covered separately in docs/commute/real-provider-verification.md.
"""

from __future__ import annotations

import os
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
TZ = "Asia/Shanghai"
PASSED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail}")
    PASSED.append(name)
    print(f"[PASS] {name:<42} {detail}")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_health(client: httpx.Client, url: str, process: subprocess.Popen) -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"API exited early with {process.returncode}")
        try:
            response = client.get(url)
            if response.status_code == 200:
                return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError("API health check did not become ready")


def env_for(database: Path, *, password: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
            "ASSISTANT_ENVIRONMENT": "development",
            "ASSISTANT_COMMUTE_PROVIDER_MODE": "mock",
            "ASSISTANT_COMMUTE_MOCK_RAIN_PROBABILITY": "0.55",
            "ASSISTANT_COMMUTE_MOCK_ETA_SECONDS": "1560",
            "ASSISTANT_COMMUTE_MOCK_PUSH_RESULT": "accepted",
            # Explicit test switch only; startup defaults keep paid analysis off.
            "ASSISTANT_COMMUTE_AUTO_AI_ENABLED": "true",
            "ASSISTANT_NEXUS_BASE_URL": "http://127.0.0.1:1",
            "ASSISTANT_NEXUS_APPLICATION_TOKEN": "e2e-application-token-32-characters",
            "ASSISTANT_TOOL_CALLBACK_TOKEN": "e2e-tool-callback-token-32-characters",
            # Avoid host proxies intercepting loopback traffic.
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    if password is not None:
        env["ASSISTANT_PROVISION_PASSWORD"] = password
    return env


def provision(database: Path, env: dict[str, str], email: str, display: str) -> None:
    result = subprocess.run(
        [
            PYTHON,
            "-m",
            "assistant_api.commands.seed_dev_user",
            "--email",
            email,
            "--display-name",
            display,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"seed failed: {result.stderr or result.stdout}")
    if "created user" not in result.stdout and "already exists" not in result.stdout:
        raise RuntimeError(f"unexpected seed output: {result.stdout}")


def run_module(env: dict[str, str], module: str) -> str:
    result = subprocess.run(
        [PYTHON, "-m", module, "--once"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{module} failed: {result.stderr or result.stdout}")
    return (result.stdout + result.stderr).strip()


def api_headers(token: str, extra: dict | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(extra or {})
    return headers


def plan_body(now_local: datetime, *, name: str) -> dict:
    arrival = now_local + timedelta(hours=2)
    if arrival.date() != now_local.date() or arrival.hour > 23:
        arrival = now_local.replace(hour=23, minute=59)
    check_time = (now_local - timedelta(hours=1)).strftime("%H:%M")
    if check_time >= arrival.strftime("%H:%M"):
        check_time = "00:01"
    return {
        "name": name,
        "enabled": True,
        "timezone": TZ,
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
        "arrival_local_time": arrival.strftime("%H:%M"),
        "check_local_time": check_time,
        "transport_mode": "driving",
        "buffer_minutes": 10,
        "rain_probability_threshold": 0.4,
        "rain_extra_buffer_minutes": 10,
        "notify_enabled": True,
        "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
        "destination": {"label": "公司", "latitude": 31.22, "longitude": 121.45},
    }


def wait_terminal(
    client: httpx.Client, base: str, token: str, check_id: str, *, timeout: float = 30
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        response = client.get(f"{base}/api/v1/checks/{check_id}", headers=api_headers(token))
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"succeeded", "degraded", "failed", "cancelled"}:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"check did not finish: {last}")


def main() -> None:
    tempdir = Path(tempfile.mkdtemp(prefix="commute-e2e-"))
    database = tempdir / "commute.db"
    password = "E2E-" + secrets.token_urlsafe(18)
    env = env_for(database, password=password)
    api_port = free_port()
    base = f"http://127.0.0.1:{api_port}"
    api: subprocess.Popen | None = None
    client = httpx.Client(timeout=15, trust_env=False)
    try:
        api = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "assistant_api.main:app", "--host", "127.0.0.1",
             "--port", str(api_port), "--log-level", "warning"],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        health = wait_health(client, f"{base}/health", api)
        check("API health", health["status"] == "ok" and health["commute_provider_mode"] == "mock")

        provision(database, env, "e2e-a@example.com", "联调甲")
        provision(database, env, "e2e-b@example.com", "联调乙")
        with sqlite3.connect(database) as db:
            user_count = db.execute("select count(*) from users").fetchone()[0]
        check("local users persisted", user_count == 2, str(user_count))

        login_a = client.post(
            f"{base}/api/v1/auth/login",
            json={"email": "e2e-a@example.com", "password": password},
        )
        login_a.raise_for_status()
        token_a = login_a.json()["token"]
        login_b = client.post(
            f"{base}/api/v1/auth/login",
            json={"email": "e2e-b@example.com", "password": password},
        )
        login_b.raise_for_status()
        token_b = login_b.json()["token"]
        check("login returns opaque sessions", len(token_a) > 20 and token_a != token_b)

        now_local = datetime.now(ZoneInfo(TZ))
        body = plan_body(now_local, name="E2E 上班通勤")
        created = client.post(
            f"{base}/api/v1/commute-plans", headers=api_headers(token_a), json=body
        )
        check(
            "create plan",
            created.status_code == 201 and created.json()["revision"] == 1,
            created.text[:120],
        )
        plan = created.json()
        plan_id = plan["id"]
        check("plan persists locations for owner", plan["origin"]["label"] == "家")
        etag = created.headers.get("etag")
        check("plan revision ETag", etag == '"1"', etag or "")

        listed = client.get(f"{base}/api/v1/commute-plans", headers=api_headers(token_a))
        check("list own plans", listed.status_code == 200 and len(listed.json()) == 1)

        missing = client.put(
            f"{base}/api/v1/commute-plans/{plan_id}",
            headers=api_headers(token_a),
            json=body,
        )
        check("PUT without If-Match is 428", missing.status_code == 428,
              missing.json()["error"]["code"])
        stale = client.put(
            f"{base}/api/v1/commute-plans/{plan_id}",
            headers=api_headers(token_a, {"If-Match": '"9"'}),
            json=body,
        )
        check("stale revision is 409", stale.status_code == 409,
              stale.json()["error"]["code"])
        updated_body = dict(body, name="E2E 上班通勤（已更新）")
        updated = client.put(
            f"{base}/api/v1/commute-plans/{plan_id}",
            headers=api_headers(token_a, {"If-Match": etag or '"1"'}),
            json=updated_body,
        )
        check("revisioned update", updated.status_code == 200
              and updated.json()["revision"] == 2, updated.text[:120])
        check("update preserved plan id", updated.json()["id"] == plan_id)

        hidden = client.get(
            f"{base}/api/v1/commute-plans/{plan_id}", headers=api_headers(token_b)
        )
        check("cross-user plan read hidden", hidden.status_code == 404)

        device = client.put(
            f"{base}/api/v1/push-devices/e2e-installation-1",
            headers=api_headers(token_a),
            json={"apns_token": "ab" * 32, "notification_permission": "authorized"},
        )
        check("register push device", device.status_code == 200
              and device.json()["environment"] == "sandbox", device.text[:120])

        manual_key = "e2e-manual-check-key-1"
        manual = client.post(
            f"{base}/api/v1/commute-plans/{plan_id}/checks",
            headers=api_headers(token_a, {"Idempotency-Key": manual_key}),
        )
        check("manual check 202", manual.status_code == 202, manual.text[:120])
        check_id = manual.json()["check_id"]
        replay = client.post(
            f"{base}/api/v1/commute-plans/{plan_id}/checks",
            headers=api_headers(token_a, {"Idempotency-Key": manual_key}),
        )
        check("manual check replay", replay.status_code == 202
              and replay.json()["check_id"] == check_id
              and replay.headers.get("x-idempotent-replay") == "true")

        worker_out = run_module(env, "assistant_api.commute.worker")
        check("worker executes queued manual check", "worker stopped" in worker_out,
              worker_out[-80:])
        terminal = wait_terminal(client, base, token_a, check_id)
        check("manual check terminal", terminal["status"] in {"succeeded", "degraded"},
              terminal["status"])
        check("manual check does not push", terminal["trigger"] == "manual")

        latest = client.get(
            f"{base}/api/v1/commute-plans/{plan_id}/advisories/latest",
            headers=api_headers(token_a),
        )
        latest.raise_for_status()
        advisory = latest.json()
        check("advisory persisted and returned", advisory["plan_id"] == plan_id
              and advisory["quality"] in {"fresh", "degraded"})
        check("advisory has future/time facts", advisory["target_arrival_at"].endswith("Z")
              and advisory["recommended_departure_at"] is not None
              and advisory["eta_seconds"] == 1560)
        check("weather from mock provider is labelled",
              advisory["sources"][0]["provider"] == "mock"
              and advisory["sources"][0]["fetched_at"] is not None)
        check("weather probability is real or null",
              advisory["weather"] is not None
              and advisory["weather"]["precipitation_probability_max"] == 0.55)
        detail = client.get(
            f"{base}/api/v1/advisories/{advisory['id']}", headers=api_headers(token_a)
        )
        check("advisory detail by id", detail.status_code == 200
              and detail.json()["id"] == advisory["id"])
        check("advisory omits address labels",
              "31.2304" not in detail.text and "家" not in detail.text)

        action_key = "e2e-action-key-1"
        opened = client.post(
            f"{base}/api/v1/advisories/{advisory['id']}/actions",
            headers=api_headers(token_a, {"Idempotency-Key": action_key}),
            json={"action": "opened", "installation_id": "e2e-installation-1"},
        )
        check("report opened", opened.status_code == 200
              and opened.json()["replayed"] is False, opened.text[:120])
        opened_replay = client.post(
            f"{base}/api/v1/advisories/{advisory['id']}/actions",
            headers=api_headers(token_a, {"Idempotency-Key": action_key}),
            json={"action": "opened", "installation_id": "e2e-installation-1"},
        )
        check("action idempotency replay", opened_replay.status_code == 200
              and opened_replay.json()["replayed"] is True)
        reused = client.post(
            f"{base}/api/v1/advisories/{advisory['id']}/actions",
            headers=api_headers(token_a, {"Idempotency-Key": action_key}),
            json={"action": "skip_today", "installation_id": "e2e-installation-1"},
        )
        check("same key different body is 409", reused.status_code == 409
              and reused.json()["error"]["code"] == "idempotency_key_reused")
        skipped = client.post(
            f"{base}/api/v1/advisories/{advisory['id']}/actions",
            headers=api_headers(token_a, {"Idempotency-Key": "e2e-skip-key-1"}),
            json={"action": "skip_today", "installation_id": "e2e-installation-1"},
        )
        check("skip today", skipped.status_code == 200)

        # Second plan exercises scheduler -> auto check -> outbox -> mock APNs.
        auto_body = plan_body(now_local, name="E2E 自动通勤")
        auto_created = client.post(
            f"{base}/api/v1/commute-plans", headers=api_headers(token_a), json=auto_body
        )
        auto_created.raise_for_status()
        auto_plan_id = auto_created.json()["id"]
        scheduler_out = run_module(env, "assistant_api.commute.scheduler")
        check("scheduler enqueues due check", "enqueued=" in scheduler_out, scheduler_out)
        worker_out = run_module(env, "assistant_api.commute.worker")
        check("worker processes auto check", "worker stopped" in worker_out,
              worker_out[-80:])
        auto_latest = client.get(
            f"{base}/api/v1/commute-plans/{auto_plan_id}/advisories/latest",
            headers=api_headers(token_a),
        )
        check("auto advisory persisted", auto_latest.status_code == 200,
              auto_latest.text[:120])

        with sqlite3.connect(database) as db:
            outbox = db.execute(
                "select status, reminder_sequence, accepted_at, last_error_code "
                "from notification_outbox where plan_id = ? "
                "order by created_at desc limit 1",
                (auto_plan_id,),
            ).fetchone()
            attempt_count = db.execute(
                "select count(*) from push_attempts where user_id = "
                "(select user_id from commute_plans where plan_id = ?)",
                (auto_plan_id,),
            ).fetchone()[0]
        check("outbox cancelled without a Bark binding", outbox is not None
              and outbox[0] == "cancelled" and outbox[1] == 1
              and outbox[3] == "bark_not_enabled", str(outbox))
        check("no real notification attempt was recorded", attempt_count == 0,
              str(attempt_count))
        check("cancelled is not delivered", "delivered" not in (outbox[0] if outbox else ""))

        no_advisory = client.get(
            f"{base}/api/v1/commute-plans/{auto_plan_id}/advisories/latest",
            headers=api_headers(token_b),
        )
        check("cross-user advisory hidden", no_advisory.status_code == 404)

        deleted = client.delete(
            f"{base}/api/v1/commute-plans/{plan_id}", headers=api_headers(token_a)
        )
        check("delete plan", deleted.status_code == 204)
        gone = client.get(
            f"{base}/api/v1/commute-plans/{plan_id}", headers=api_headers(token_a)
        )
        check("deleted plan hidden", gone.status_code == 404)
        with sqlite3.connect(database) as db:
            leftover = db.execute(
                "select count(*) from commute_advisories where plan_id = ?", (plan_id,)
            ).fetchone()[0]
            checks_left = db.execute(
                "select count(*) from commute_checks where plan_id = ?", (plan_id,)
            ).fetchone()[0]
        check("plan deletion cleans persisted children", leftover == 0 and checks_left == 0,
              f"advisories={leftover}, checks={checks_left}")

        print(f"\n{len(PASSED)}/{len(PASSED)} backend commute API checks passed")
    finally:
        client.close()
        if api is not None and api.poll() is None:
            api.terminate()
            try:
                api.wait(timeout=5)
            except subprocess.TimeoutExpired:
                api.kill()
        import shutil

        shutil.rmtree(tempdir, ignore_errors=True)


if __name__ == "__main__":
    main()
