"""Real HTTP + isolated scheduler/worker process verification.

Starts a throwaway API, a long-running scheduler and a long-running worker on a
temporary SQLite database. No shared 8010 instance, no real provider, no real
Bark/APNs send and no model call are used. Processes are stopped in ``finally``.
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
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
    print(f"[PASS] {name:<52} {detail}")


def free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def env_for(database: Path, password: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
            "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
            "ASSISTANT_ENVIRONMENT": "development",
            "ASSISTANT_COMMUTE_PROVIDER_MODE": "mock",
            "ASSISTANT_COMMUTE_MOCK_RAIN_PROBABILITY": "0.55",
            "ASSISTANT_COMMUTE_MOCK_ETA_SECONDS": "1560",
            "ASSISTANT_COMMUTE_AUTO_WEATHER_ENABLED": "true",
            "ASSISTANT_COMMUTE_AUTO_AI_ENABLED": "false",
            "ASSISTANT_COMMUTE_HEARTBEAT_INTERVAL_SECONDS": "1",
            "ASSISTANT_COMMUTE_HEARTBEAT_STALE_SECONDS": "10",
            "ASSISTANT_COMMUTE_SCHEDULER_POLL_SECONDS": "1",
            "ASSISTANT_COMMUTE_WORKER_POLL_SECONDS": "0.5",
            "ASSISTANT_COMMUTE_JOB_LEASE_SECONDS": "30",
            "ASSISTANT_COMMUTE_CHECK_INTERVAL_MINUTES": "5",
            "ASSISTANT_NEXUS_BASE_URL": "http://127.0.0.1:1",
            "ASSISTANT_NEXUS_APPLICATION_TOKEN": "e2e-application-token-32-characters",
            "ASSISTANT_TOOL_CALLBACK_TOKEN": "e2e-tool-callback-token-32-characters",
            "ASSISTANT_PROVISION_PASSWORD": password,
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    return env


def provision(env: dict[str, str], email: str, display: str) -> None:
    result = subprocess.run(
        [PYTHON, "-m", "assistant_api.commands.seed_dev_user",
         "--email", email, "--display-name", display],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


def wait_health(client: httpx.Client, url: str, process: subprocess.Popen) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        if process.poll() is not None:
            output = ""
            if process.stdout is not None:
                output = process.stdout.read()
            raise RuntimeError(f"API exited early ({process.returncode}): {output[-2000:]}")
        try:
            if client.get(url).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise TimeoutError("API did not become ready")


def start_process(env: dict[str, str], module: str) -> subprocess.Popen:
    return subprocess.Popen(
        [PYTHON, "-m", module], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def headers(token: str, extra: dict | None = None) -> dict[str, str]:
    values = {"Authorization": f"Bearer {token}"}
    values.update(extra or {})
    return values


def plan_body(now_local: datetime, name: str) -> dict:
    arrival = now_local + timedelta(hours=2)
    if arrival.date() != now_local.date() or arrival.hour > 23:
        arrival = now_local.replace(hour=23, minute=59)
    check_time = (now_local - timedelta(hours=1)).strftime("%H:%M")
    return {
        "name": name, "enabled": True, "timezone": TZ,
        "weekdays": [1, 2, 3, 4, 5, 6, 7],
        "arrival_local_time": arrival.strftime("%H:%M"),
        "check_local_time": check_time,
        "transport_mode": "driving", "buffer_minutes": 10,
        "rain_probability_threshold": 0.4, "rain_extra_buffer_minutes": 10,
        "notify_enabled": False,
        "origin": {"label": "家", "latitude": 31.2304, "longitude": 121.4737},
        "destination": {"label": "公司", "latitude": 31.22, "longitude": 121.45},
    }


def wait_check(client, base, token, check_id, timeout=20) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        response = client.get(f"{base}/api/v1/checks/{check_id}", headers=headers(token))
        response.raise_for_status()
        last = response.json()
        if last["status"] in {"succeeded", "degraded", "failed", "cancelled", "unknown"}:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"check stayed non-terminal: {last}")


def latest_check_for(client, base, token, plan_id: str) -> dict | None:
    deadline = time.time() + 20
    while time.time() < deadline:
        runtime = client.get(f"{base}/api/v1/commute/runtime", headers=headers(token)).json()
        if runtime.get("latest_check"):
            return runtime["latest_check"]
        time.sleep(0.1)
    return None


def wait_online(client, base, token, component: str, timeout=15) -> dict:
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        last = client.get(f"{base}/api/v1/commute/runtime", headers=headers(token)).json()
        if last[component]["state"] == "online":
            return last
        time.sleep(0.1)
    raise TimeoutError(f"{component} not online: {last}")


def main() -> None:
    tempdir = Path(tempfile.mkdtemp(prefix="commute-runtime-e2e-"))
    database = tempdir / "runtime.db"
    password = "E2E-" + secrets.token_urlsafe(18)
    env = env_for(database, password)
    api_port = free_port()
    base = f"http://127.0.0.1:{api_port}"
    api = scheduler = worker = None
    client = httpx.Client(timeout=20, trust_env=False)
    try:
        api = subprocess.Popen(
            [PYTHON, "-m", "uvicorn", "assistant_api.main:app", "--host", "127.0.0.1",
             "--port", str(api_port), "--log-level", "warning"],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        wait_health(client, f"{base}/health", api)
        provision(env, "runtime-a@example.com", "运行甲")
        provision(env, "runtime-b@example.com", "运行乙")
        token_a = client.post(f"{base}/api/v1/auth/login", json={
            "email": "runtime-a@example.com", "password": password}).json()["token"]
        token_b = client.post(f"{base}/api/v1/auth/login", json={
            "email": "runtime-b@example.com", "password": password}).json()["token"]

        anonymous = client.get(f"{base}/api/v1/commute/runtime")
        check("runtime requires authentication", anonymous.status_code == 401)

        now_local = datetime.now(ZoneInfo(TZ))
        plan = client.post(
            f"{base}/api/v1/commute-plans", headers=headers(token_a),
            json=plan_body(now_local, "隔离运行计划"),
        )
        plan.raise_for_status()
        plan_id = plan.json()["id"]
        etag = plan.headers["etag"]

        scheduler = start_process(env, "assistant_api.commute.scheduler")
        runtime = wait_online(client, base, token_a, "scheduler")
        check("scheduler process heartbeat", runtime["scheduler"]["last_seen_at"] is not None)
        check("worker is not online before start", runtime["worker"]["state"] == "not_seen")

        worker = start_process(env, "assistant_api.commute.worker")
        runtime = wait_online(client, base, token_a, "worker")
        check("worker process heartbeat", runtime["worker"]["last_seen_at"] is not None)
        check("runtime exposes mode separately", runtime["auto_analysis_enabled"] is False
              and runtime["auto_analysis_mode"] == "weather_only")
        check("route provider availability is explicit", runtime["route_provider_available"] is True)

        latest = latest_check_for(client, base, token_a, plan_id)
        check("scheduler enqueued a timed check", latest is not None, str(latest))
        timed = wait_check(client, base, token_a, latest["check_id"])
        check("weather_only check executed", timed["status"] == "succeeded", timed["status"])
        check("weather_only mode is labelled", timed["analysis_mode"] == "weather_only")
        check("weather_only phase is terminal", timed["phase"] == "done")
        check("weather_only has no fake retry", timed["next_attempt_at"] is None)
        check("weather_only created no advisory", timed["advisory_created"] is False
              and timed["advisory_id"] is None)
        latest_advisory = client.get(
            f"{base}/api/v1/commute-plans/{plan_id}/advisories/latest",
            headers=headers(token_a),
        )
        check("weather_only leaves latest unchanged", latest_advisory.status_code == 404)
        runtime = client.get(f"{base}/api/v1/commute/runtime", headers=headers(token_a)).json()
        check("task progress is distinct from heartbeat", runtime["last_progress_at"] is not None
              and runtime["last_progress_at"] >= timed["phase_updated_at"].replace("Z", "+00:00"))

        with sqlite3.connect(database) as db:
            weather_rows = db.execute(
                "select count(*) from commute_weather_snapshots where check_id = ?",
                (timed["check_id"],),
            ).fetchone()[0]
        check("weather snapshot persisted", weather_rows == 1)

        # Full manual analysis is explicit; it creates an advisory for the owner.
        manual = client.post(
            f"{base}/api/v1/commute-plans/{plan_id}/checks",
            headers=headers(token_a, {"Idempotency-Key": "runtime-manual-1"}),
        )
        manual.raise_for_status()
        manual_check = wait_check(client, base, token_a, manual.json()["check_id"])
        check("manual check accepted and executed",
              manual.status_code == 202 and manual_check["status"] == "succeeded")
        check("manual analysis mode is full", manual_check["analysis_mode"] == "full")
        check("manual check saved an advisory", manual_check["advisory_created"] is True)
        advisory = client.get(
            f"{base}/api/v1/commute-plans/{plan_id}/advisories/latest",
            headers=headers(token_a),
        )
        check("latest advisory returned after save", advisory.status_code == 200
              and advisory.json()["plan_id"] == plan_id)

        # Account isolation: another user sees no queue/progress and no check.
        runtime_b = client.get(f"{base}/api/v1/commute/runtime", headers=headers(token_b)).json()
        check("runtime queue is account scoped", runtime_b["queue"]["queued"] == 0
              and runtime_b["latest_check"] is None)
        other_check = client.get(
            f"{base}/api/v1/checks/{manual_check['check_id']}", headers=headers(token_b)
        )
        check("check detail is account scoped", other_check.status_code == 404)
        plans_b = client.get(f"{base}/api/v1/commute-plans", headers=headers(token_b)).json()
        check("plan list is account scoped", plans_b == [])

        # Crash recovery: a dead executor's expired lease is reclaimed, not trusted.
        stop_process(worker)
        worker = None
        crashed = client.post(
            f"{base}/api/v1/commute-plans/{plan_id}/checks",
            headers=headers(token_a, {"Idempotency-Key": "runtime-crashed-1"}),
        )
        crashed.raise_for_status()
        crashed_id = crashed.json()["check_id"]
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        with sqlite3.connect(database) as db:
            db.execute(
                "update commute_checks set status='running', lease_owner='dead-worker', "
                "lease_expires_at=?, phase='fetching_weather', phase_updated_at=? "
                "where check_id=?",
                (past.strftime("%Y-%m-%d %H:%M:%S.000000"), past.strftime("%Y-%m-%d %H:%M:%S.000000"), crashed_id),
            )
            db.commit()
        worker = start_process(env, "assistant_api.commute.worker")
        recovered = wait_check(client, base, token_a, crashed_id, timeout=30)
        check("expired worker lease recovered", recovered["status"] == "succeeded",
              recovered["status"])
        check("recovered check keeps its phase", recovered["phase"] == "done")
        check("claim was renewed to a live owner", recovered["next_attempt_at"] is None)

        # Plan edit while executing: the stale revision is cancelled, not saved.
        stop_process(worker)
        worker = None
        stale = client.post(
            f"{base}/api/v1/commute-plans/{plan_id}/checks",
            headers=headers(token_a, {"Idempotency-Key": "runtime-plan-edit-1"}),
        )
        stale.raise_for_status()
        stale_id = stale.json()["check_id"]
        edited = client.put(
            f"{base}/api/v1/commute-plans/{plan_id}",
            headers=headers(token_a, {"If-Match": etag}),
            json=dict(plan_body(now_local, "隔离运行计划（修改）"), notify_enabled=False),
        )
        check("plan revision edited during execution", edited.status_code == 200
              and edited.json()["revision"] == 2, edited.text[:100])
        worker = start_process(env, "assistant_api.commute.worker")
        cancelled = wait_check(client, base, token_a, stale_id, timeout=30)
        check("old revision result is cancelled", cancelled["status"] == "cancelled"
              and cancelled["error"]["code"] == "plan_revision_superseded",
              str(cancelled))
        deleted = client.delete(
            f"{base}/api/v1/commute-plans/{plan_id}", headers=headers(token_a)
        )
        check("plan deletion still works", deleted.status_code == 204)

        # Liveness is per process, not one global boolean.
        stop_process(worker)
        worker = None
        time.sleep(11)
        stale_runtime = client.get(
            f"{base}/api/v1/commute/runtime", headers=headers(token_a)
        ).json()
        check("stopped worker becomes stale", stale_runtime["worker"]["state"] == "stale")
        check("scheduler stays online independently", stale_runtime["scheduler"]["state"] == "online")

        print(f"\n{len(PASSED)}/{len(PASSED)} runtime checks passed")
    finally:
        stop_process(worker)
        stop_process(scheduler)
        stop_process(api)
        client.close()
        import shutil
        shutil.rmtree(tempdir, ignore_errors=True)
        check("all child processes stopped",
              all(p is None or p.poll() is not None for p in (api, scheduler, worker)))


if __name__ == "__main__":
    main()
