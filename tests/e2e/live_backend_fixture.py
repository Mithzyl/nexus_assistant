"""Long-running isolated backend for the iOS simulator UI acceptance test.

Creates a fresh SQLite database, two synthetic accounts with one commute plan
each, executes one real worker run per plan against the Nexus stub (structured
model result, no real model/weather/route/notification), then serves the API on
127.0.0.1:8410 until SIGTERM. The simulator app points at it through
PA_API_BASE_URL, so nothing touches the shared 8010 or the original database.

Usage:
    ASSISTANT_PYTHON=.tmp/backend-venv/bin/python \
      python3 tests/e2e/live_backend_fixture.py
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "services" / "assistant-api"
NEXUS_DIR = ROOT / "services" / "nexus-stub"
RUNTIME = ROOT / ".tmp" / "live-ios"

APP_TOKEN = "stub-application-token-32-characters"
TOOL_TOKEN = "tool-callback-token-32-characters"
PASSWORD = "acceptance-password-2026"
ACCOUNTS = [
    ("acceptance-a@example.com", "验收甲", "甲的通勤计划"),
    ("acceptance-b@example.com", "验收乙", "乙的通勤计划"),
]
# Used by the simulator runtime tests: a plan with no check yet.
RUNTIME_ACCOUNT = ("runtime-a@example.com", "运行验证", "运行验证通勤")

MODEL_TITLE = "模型建议：提前 45 分钟出发"


def wait_http(url: str, *, timeout: float, label: str) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as caught:  # noqa: BLE001
            last = caught
        time.sleep(0.3)
    raise RuntimeError(f"{label} not ready: {url} ({last})")


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def http(method, url, *, token=None, payload=None, headers=None):
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as caught:
        raw = caught.read()
        try:
            parsed = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            parsed = {"raw": raw.decode(errors="replace")[:300]}
        return caught.code, parsed


def _start_control(port: int, database: Path, env: dict, python: Path):
    """Tiny test-only control plane for the simulator runtime tests.

    It can set the worker heartbeat, run one worker tick, and stop/start the API
    process so the app can be observed while the backend does not answer.
    """

    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs

    state: dict = {"api": None, "api_factory": None}

    def db_execute(sql: str, args: tuple = ()) -> None:
        connection = sqlite3.connect(database, timeout=30)
        try:
            connection.execute(sql, args)
            connection.commit()
        finally:
            connection.close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # noqa: D401 - quiet
            return

        def _json(self, payload: dict, status: int = 200) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if urlparse(self.path).path == "/stats":
                connection = sqlite3.connect(database)
                connection.row_factory = sqlite3.Row
                checks = [dict(r) for r in connection.execute(
                    "select check_id, plan_id, status from commute_checks")]
                advisories = [dict(r) for r in connection.execute(
                    "select advisory_id, check_id, title from commute_advisories")]
                heartbeat = [dict(r) for r in connection.execute(
                    "select component, last_seen_at from service_heartbeats")]
                connection.close()
                self._json({"checks": checks, "advisories": advisories,
                            "heartbeats": heartbeat})
                return
            self._json({"error": "not_found"}, 404)

        def do_POST(self):
            try:
                self._post()
            except Exception as caught:  # noqa: BLE001 - report instead of dropping
                self._json({"error": repr(caught)[:200]}, 500)

        def _post(self):
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            if parsed.path == "/heartbeat":
                mode = (query.get("state") or ["online"])[0]
                if mode == "never":
                    db_execute("delete from service_heartbeats where component='worker'")
                else:
                    seen = datetime.now(timezone.utc)
                    if mode == "stale":
                        seen -= timedelta(hours=2)
                    stamp = seen.strftime("%Y-%m-%d %H:%M:%S.%f")
                    db_execute("delete from service_heartbeats where component='worker'")
                    db_execute(
                        "insert into service_heartbeats "
                        "(component, instance_id, last_seen_at, created_at, updated_at) "
                        "values ('worker', 'control', ?, ?, ?)",
                        (stamp, stamp, stamp),
                    )
                self._json({"state": mode})
                return
            if parsed.path == "/worker":
                done = subprocess.run(
                    [str(python), "-m", "assistant_api.commute.worker", "--once"],
                    cwd=API_DIR, env=env, text=True, capture_output=True, timeout=180,
                )
                self._json({"returncode": done.returncode,
                            "output": (done.stdout + done.stderr)[-400:]})
                return
            if parsed.path in ("/api/stop", "/api/start"):
                process = state.get("api")
                if parsed.path == "/api/stop":
                    stop(process)
                    state["api"] = None
                    self._json({"stopped": True})
                else:
                    factory = state["api_factory"]
                    state["api"] = factory()
                    self._json({"started": True})
                return
            self._json({"error": "not_found"}, 404)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return state, server


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-port", type=int, default=8410)
    parser.add_argument("--stub-port", type=int, default=8420)
    parser.add_argument("--control-port", type=int, default=8412)
    args = parser.parse_args()

    assistant_python = os.environ.get("ASSISTANT_PYTHON")
    if not assistant_python or not Path(assistant_python).exists():
        print("ASSISTANT_PYTHON must point at the acceptance venv", file=sys.stderr)
        return 2
    python = Path(assistant_python)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", args.api_port))
        except OSError:
            print(f"port {args.api_port} is in use", file=sys.stderr)
            return 2

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    RUNTIME.mkdir(parents=True)
    database = RUNTIME / "assistant.db"
    api_url = f"http://127.0.0.1:{args.api_port}"
    stub_url = f"http://127.0.0.1:{args.stub_port}"

    shanghai = timezone(timedelta(hours=8))
    now_local = datetime.now(shanghai)
    target = now_local.replace(hour=23, minute=30, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    target_utc = target.astimezone(timezone.utc)
    duration, extra, base = 1800, 900, 600
    departure = target_utc - timedelta(seconds=duration + extra)
    model_json = json.dumps(
        {
            "used_duration_seconds": duration,
            "duration_source": "duration_seconds",
            "extra_buffer_seconds": extra,
            "additional_buffer_seconds": extra - base,
            "recommended_departure_at": departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": MODEL_TITLE,
            "summary": "iOS 模拟器联调用的模拟模型建议。",
            "basis": ["模拟上游：服务端 mock 路线时长"],
            "missing_information": [],
        },
        ensure_ascii=False,
    )

    env = {
        **os.environ,
        "ASSISTANT_ENVIRONMENT": "development",
        "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "false",
        "ASSISTANT_NEXUS_BASE_URL": stub_url,
        "ASSISTANT_NEXUS_APPLICATION_TOKEN": APP_TOKEN,
        "ASSISTANT_TOOL_CALLBACK_TOKEN": TOOL_TOKEN,
        "ASSISTANT_COMMUTE_PROVIDER_MODE": "mock",
        "ASSISTANT_COMMUTE_AUTO_AI_ENABLED": "false",
        "ASSISTANT_COMMUTE_AUTO_WEATHER_ENABLED": "false",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "127.0.0.1,localhost",
    }

    log = (RUNTIME / "fixture.log").open("w")
    stub = api = None
    control = None
    control_server = None
    try:
        for email, display, _ in [*ACCOUNTS, RUNTIME_ACCOUNT]:
            seeded = subprocess.run(
                [str(python), "-m", "assistant_api.commands.seed_dev_user",
                 "--email", email, "--display-name", display],
                cwd=API_DIR,
                env={**env, "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
                     "ASSISTANT_PROVISION_PASSWORD": PASSWORD},
                text=True, capture_output=True,
            )
            if seeded.returncode != 0:
                raise RuntimeError(seeded.stdout + seeded.stderr)

        stub = subprocess.Popen(
            [str(python), "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(args.stub_port), "--log-level", "warning"],
            cwd=NEXUS_DIR,
            env={**env, "STUB_APP_TOKEN": APP_TOKEN, "STUB_FINAL_TEXT": model_json},
            stdout=log, stderr=subprocess.STDOUT,
        )
        wait_http(f"{stub_url}/health", timeout=20, label="stub")

        def start_api():
            return subprocess.Popen(
                [str(python), "-m", "uvicorn", "assistant_api.main:app",
                 "--host", "127.0.0.1", "--port", str(args.api_port),
                 "--log-level", "warning"],
                cwd=API_DIR, env=env, stdout=log, stderr=subprocess.STDOUT,
            )

        api = start_api()
        wait_http(f"{api_url}/health", timeout=40, label="api")

        fixture = {"api_url": api_url, "control_url": f"http://127.0.0.1:{args.control_port}", "accounts": {}}
        for email, _, plan_name in ACCOUNTS:
            status, login = http("POST", f"{api_url}/api/v1/auth/login",
                                 payload={"email": email, "password": PASSWORD})
            if status != 200:
                raise RuntimeError(f"login {email}: {status} {login}")
            token = login["token"]
            status, plan = http(
                "POST", f"{api_url}/api/v1/commute-plans", token=token,
                payload={
                    "name": plan_name, "enabled": True, "timezone": "Asia/Shanghai",
                    "weekdays": [1, 2, 3, 4, 5, 6, 7],
                    "arrival_local_time": target.strftime("%H:%M"),
                    "check_local_time": "07:30",
                    "transport_mode": "driving", "buffer_minutes": 10,
                    "notify_enabled": False,
                    "origin": {"label": "起点", "latitude": 31.2304, "longitude": 121.4737},
                    "destination": {"label": "终点", "latitude": 31.2019, "longitude": 121.606},
                },
            )
            if status != 201:
                raise RuntimeError(f"plan {email}: {status} {plan}")
            status, created = http(
                "POST", f"{api_url}/api/v1/commute-plans/{plan['id']}/checks",
                token=token, headers={"Idempotency-Key": f"live-{plan['id'][:8]}-0001"},
            )
            if status != 202:
                raise RuntimeError(f"check {email}: {status} {created}")
            worker = subprocess.run(
                [str(python), "-m", "assistant_api.commute.worker", "--once"],
                cwd=API_DIR, env=env, text=True, capture_output=True, timeout=180,
            )
            if worker.returncode != 0:
                raise RuntimeError(worker.stdout + worker.stderr)
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            advisory = connection.execute(
                "select advisory_id, title, ai_analysis_json from commute_advisories where plan_id=?",
                (plan["id"],),
            ).fetchone()
            connection.close()
            analysis = json.loads(advisory["ai_analysis_json"]) if advisory else {}
            print(f"{email}: advisory={advisory['advisory_id'] if advisory else None} "
                  f"ai={analysis.get('status')} title={advisory['title'] if advisory else None}",
                  flush=True)
            fixture["accounts"][email] = {
                "plan_id": plan["id"], "plan_name": plan_name,
                "advisory_id": advisory["advisory_id"] if advisory else None,
                "ai_status": analysis.get("status"),
            }

        # runtime account: a plan with no check, used by the simulator runtime tests
        email_r, _, plan_name_r = RUNTIME_ACCOUNT
        status, login_r = http("POST", f"{api_url}/api/v1/auth/login",
                               payload={"email": email_r, "password": PASSWORD})
        token_r = login_r["token"]
        status, plan_r = http(
            "POST", f"{api_url}/api/v1/commute-plans", token=token_r,
            payload={
                "name": plan_name_r, "enabled": True, "timezone": "Asia/Shanghai",
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
                "arrival_local_time": target.strftime("%H:%M"),
                "check_local_time": "07:30",
                "transport_mode": "driving", "buffer_minutes": 10,
                "notify_enabled": False,
                "origin": {"label": "起点", "latitude": 31.2304, "longitude": 121.4737},
                "destination": {"label": "终点", "latitude": 31.2019, "longitude": 121.606},
            },
        )
        if status != 201:
            raise RuntimeError(f"runtime plan: {status} {plan_r}")
        fixture["accounts"][email_r] = {
            "plan_id": plan_r["id"], "plan_name": plan_name_r,
            "advisory_id": None, "ai_status": None,
        }

        # The two iOS read tests expect a backend that has not reported a worker yet.
        connection = sqlite3.connect(database)
        connection.execute("delete from service_heartbeats")
        connection.commit()
        connection.close()

        control, control_server = _start_control(
            args.control_port, database, env, python
        )
        control["api"] = api
        control["api_factory"] = start_api
        (RUNTIME / "ready.json").write_text(json.dumps(fixture, ensure_ascii=False, indent=2))
        print(f"READY {api_url} control=http://127.0.0.1:{args.control_port} (db={database})", flush=True)

        stopping = {"stop": False}

        def _handle(*_):
            stopping["stop"] = True

        signal.signal(signal.SIGTERM, _handle)
        signal.signal(signal.SIGINT, _handle)
        while not stopping["stop"]:
            time.sleep(0.5)
        return 0
    finally:
        if control is not None:
            stop(control.get("api"))
        if control_server is not None:
            control_server.shutdown()
        stop(api)
        stop(stub)
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
