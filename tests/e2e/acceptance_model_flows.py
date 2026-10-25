"""Model-outcome and worker-heartbeat acceptance for acceptance/round1.

Two flows, both against the merged code in THIS worktree and a simulated upstream:

  A. VALID_MODEL     the stub returns a structured commute result; the worker
                     stores an advisory whose ai_analysis.status is "available",
                     and the web client shows the model's title/summary.
  B. UNKNOWN_MODEL   the stub's /_debug/unknown_once makes the run outcome
                     unknown; the worker stores ai_analysis.status "unknown",
                     the advisory is still readable (regression) and the web
                     client shows "模型分析未完成" instead of hiding it.

Heartbeat is checked separately from route availability and from task phase:

  * before any worker runs: worker.state == "not_seen", the queued check still
    shows waiting, and the web says "尚未收到执行程序的响应";
  * after `worker --once`: worker.state == "online" (meaning "recently
    responded", derived from service_heartbeats.last_seen_at). The finished
    task must not keep a prominent background banner.

Nothing here calls a real model, weather, route or notification service.
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
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "services" / "assistant-api"
WEB_DIR = ROOT / "apps" / "web"
NEXUS_DIR = ROOT / "services" / "nexus-stub"
RUNTIME = ROOT / ".tmp" / "model-flows"

APP_TOKEN = "stub-application-token-32-characters"
TOOL_TOKEN = "tool-callback-token-32-characters"
PASSWORD = "acceptance-password-2026"
ACCOUNT = "acceptance-model@example.com"

MODEL_TITLE = "模型建议：提前 45 分钟出发"
MODEL_SUMMARY = "模拟模型返回的有效建议，用于验证客户端能显示服务端校验后的结果。"

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  {detail}" if detail else ""), flush=True)


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


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


def rows(sql: str, args: tuple = ()) -> list[dict]:
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, args)]
    finally:
        connection.close()


def main() -> int:
    global DB, PYTHON, API_URL, WEB_URL, STUB_URL, BASE_ENV

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-port", type=int, default=8411)
    parser.add_argument("--web-port", type=int, default=3411)
    parser.add_argument("--nexus-port", type=int, default=8421)
    args = parser.parse_args()

    assistant_python = os.environ.get("ASSISTANT_PYTHON")
    if not assistant_python or not Path(assistant_python).exists():
        print("ASSISTANT_PYTHON must point at the acceptance worktree venv", file=sys.stderr)
        return 2
    PYTHON = Path(assistant_python)

    for port, label in ((args.api_port, "api"), (args.web_port, "web"), (args.nexus_port, "stub")):
        if not port_free(port):
            print(f"port {port} ({label}) is in use", file=sys.stderr)
            return 2

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    (RUNTIME / "shots").mkdir(parents=True)
    DB = RUNTIME / "assistant.db"
    API_URL = f"http://127.0.0.1:{args.api_port}"
    STUB_URL = f"http://127.0.0.1:{args.nexus_port}"
    WEB_URL = f"http://localhost:{args.web_port}"

    # Fixed target for the synthetic plan: today 23:30 Asia/Shanghai.
    shanghai = timezone(timedelta(hours=8))
    now_local = datetime.now(shanghai)
    target = now_local.replace(hour=23, minute=30, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    target_utc = target.astimezone(timezone.utc)
    duration = 1800
    extra = 900
    base = 600
    departure = target_utc - timedelta(seconds=duration + extra)

    model_json = json.dumps(
        {
            "used_duration_seconds": duration,
            "duration_source": "duration_seconds",
            "extra_buffer_seconds": extra,
            "additional_buffer_seconds": extra - base,
            "recommended_departure_at": departure.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "title": MODEL_TITLE,
            "summary": MODEL_SUMMARY,
            "basis": ["模拟上游：路线时长来自提交的快照"],
            "missing_information": [],
            "route_reference": "accept-route-1",
        },
        ensure_ascii=False,
    )

    BASE_ENV = {
        **os.environ,
        "ASSISTANT_ENVIRONMENT": "development",
        "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{DB}",
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "false",
        "ASSISTANT_NEXUS_BASE_URL": STUB_URL,
        "ASSISTANT_NEXUS_APPLICATION_TOKEN": APP_TOKEN,
        "ASSISTANT_TOOL_CALLBACK_TOKEN": TOOL_TOKEN,
        "ASSISTANT_COMMUTE_PROVIDER_MODE": "mock",
        "ASSISTANT_COMMUTE_AUTO_AI_ENABLED": "false",
        "ASSISTANT_COMMUTE_AUTO_WEATHER_ENABLED": "false",
        "ASSISTANT_COMMUTE_HEARTBEAT_STALE_SECONDS": "60",
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "127.0.0.1,localhost",
        # The interpreter may have an editable install pointing at another
        # checkout. Force child processes to import THIS worktree's backend.
        "PYTHONPATH": str(API_DIR / "src")
        + (os.pathsep + os.environ["PYTHONPATH"] if os.environ.get("PYTHONPATH") else ""),
    }

    diagnostic = subprocess.run(
        [
            str(PYTHON),
            "-c",
            "import sys, sqlite3, sqlalchemy, aiosqlite, assistant_api;"
            "print('python=' + sys.version.split()[0]);"
            "print('sqlalchemy=' + sqlalchemy.__version__);"
            "print('aiosqlite=' + aiosqlite.__version__);"
            "print('sqlite=' + sqlite3.sqlite_version);"
            "print('assistant_api=' + assistant_api.__file__)",
        ],
        cwd=API_DIR,
        env=BASE_ENV,
        text=True,
        capture_output=True,
    )
    print(diagnostic.stdout.strip(), flush=True)
    if diagnostic.returncode != 0 or str(API_DIR / "src" / "assistant_api") not in diagnostic.stdout:
        print(diagnostic.stderr, file=sys.stderr)
        raise RuntimeError("acceptance backend is not imported from this worktree")

    logs = (RUNTIME / "processes.log").open("w")
    stub = api = web = None
    try:
        print("==> schema + synthetic account", flush=True)
        seeded = subprocess.run(
            [
                str(PYTHON),
                "-m",
                "assistant_api.commands.seed_dev_user",
                "--email",
                ACCOUNT,
                "--display-name",
                "模型流程验收",
            ],
            cwd=API_DIR,
            env={
                **BASE_ENV,
                "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
                "ASSISTANT_PROVISION_PASSWORD": PASSWORD,
            },
            text=True,
            capture_output=True,
        )
        if seeded.returncode != 0:
            raise RuntimeError(seeded.stdout + seeded.stderr)

        print("==> starting simulated upstream with a structured result", flush=True)
        stub = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "app:app", "--host", "127.0.0.1",
             "--port", str(args.nexus_port), "--log-level", "warning"],
            cwd=NEXUS_DIR,
            env={**BASE_ENV, "STUB_APP_TOKEN": APP_TOKEN, "STUB_FINAL_TEXT": model_json},
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{STUB_URL}/health", timeout=20, label="stub")

        print(f"==> API {API_URL}", flush=True)
        api = subprocess.Popen(
            [str(PYTHON), "-m", "uvicorn", "assistant_api.main:app", "--host", "127.0.0.1",
             "--port", str(args.api_port), "--log-level", "warning"],
            cwd=API_DIR, env=BASE_ENV, stdout=logs, stderr=subprocess.STDOUT,
        )
        wait_http(f"{API_URL}/health", timeout=40, label="api")

        status, login = http("POST", f"{API_URL}/api/v1/auth/login",
                             payload={"email": ACCOUNT, "password": PASSWORD})
        token = login["token"]

        status, plan = http(
            "POST", f"{API_URL}/api/v1/commute-plans", token=token,
            payload={
                "name": "模型流程通勤",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
                "arrival_local_time": target.strftime("%H:%M"),
                "check_local_time": "07:30",
                "transport_mode": "driving",
                "buffer_minutes": 10,
                "notify_enabled": False,
                "origin": {"label": "流程起点", "latitude": 31.2304, "longitude": 121.4737},
                "destination": {"label": "流程终点", "latitude": 31.2019, "longitude": 121.606},
            },
        )
        if status != 201:
            raise RuntimeError(f"plan: {status} {plan}")
        plan_id = plan["id"]

        snapshot = {
            "provider": "stub",
            "origin": {"latitude": 31.2304, "longitude": 121.4737},
            "destination": {"latitude": 31.2019, "longitude": 121.606},
            "transport_mode": "driving",
            "query_type": "arrival_deadline",
            "requested_arrival_at": target_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "queried_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "distance_meters": 9000,
            "duration_seconds": 1500,
            "duration_in_traffic_seconds": duration,
            "route_reference": "accept-route-1",
        }

        runtime_before = http("GET", f"{API_URL}/api/v1/commute/runtime", token=token)[1]
        check(
            "before any worker runs, heartbeat state is not_seen",
            runtime_before["worker"]["state"] == "not_seen"
            and runtime_before["worker"]["last_seen_at"] is None,
            json.dumps(runtime_before["worker"], ensure_ascii=False),
        )
        check(
            "route availability is a separate signal from worker heartbeat",
            runtime_before["route_provider_available"] is True,
            f"route={runtime_before['route_provider_available']}",
        )

        print(f"==> web {WEB_URL}", flush=True)
        web = subprocess.Popen(
            [str(WEB_DIR / "node_modules" / ".bin" / "next"), "start", "-p", str(args.web_port)],
            cwd=WEB_DIR, env={**os.environ, "PA_API_URL": API_URL},
            stdout=logs, stderr=subprocess.STDOUT,
        )
        wait_http(f"{WEB_URL}/today", timeout=60, label="web")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(f"{WEB_URL}/login", wait_until="networkidle")
            page.fill("#login-email", ACCOUNT)
            page.fill("#login-password", PASSWORD)
            page.get_by_role("button", name="登录").click()
            page.wait_for_url("**/today", timeout=20000)
            page.goto(f"{WEB_URL}/me/commute/{plan_id}", wait_until="networkidle")
            page.wait_for_timeout(1200)

            # Flow A step 1: queue a real check from the client, worker not running yet.
            page.get_by_text("模型流程通勤").first.wait_for(timeout=20000)
            queued = []
            for attempt in range(3):
                page.get_by_role("button", name="重新分析").first.click()
                for _ in range(24):
                    queued = rows("select check_id, status from commute_checks order by created_at desc")
                    if queued:
                        break
                    page.wait_for_timeout(250)
                if queued:
                    break
                page.wait_for_timeout(1000)
            check("client submission created one queued check", len(queued) == 1 and queued[0]["status"] == "queued", str(queued))
            check_id_a = queued[0]["check_id"]
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text()
            check("web shows the waiting state from the check", "等待处理" in body or "处理中" in body, body[:80].replace("\n", " / "))
            check(
                'web says the worker has not responded, separately from the queued task',
                "尚未收到执行程序的响应" in body and "等待处理" in body,
                "not_seen copy present" if "尚未收到执行程序的响应" in body else body[:240].replace("\n", " / "),
            )
            page.screenshot(path=str(RUNTIME / "shots" / "01-queued-offline.png"), full_page=True)

            # Flow A step 2: the real worker claims it; the stub returns a structured result.
            worker = subprocess.run(
                [str(PYTHON), "-m", "assistant_api.commute.worker", "--once"],
                cwd=API_DIR, env=BASE_ENV, text=True, capture_output=True, timeout=180,
            )
            check("worker --once exits cleanly", worker.returncode == 0, (worker.stdout + worker.stderr)[-160:].replace("\n", " / "))

            terminal = {}
            for _ in range(60):
                found = rows("select status, phase from commute_checks where check_id=?", (check_id_a,))
                if found and found[0]["status"] in ("succeeded", "degraded", "failed", "cancelled"):
                    terminal = found[0]
                    break
                time.sleep(0.5)
            check("check reached a terminal status", terminal.get("status") == "succeeded", str(terminal))

            advisory_a = rows("select * from commute_advisories where check_id=?", (check_id_a,))
            analysis_a = json.loads(advisory_a[0]["ai_analysis_json"]) if advisory_a else {}
            check("valid model flow stores ai_analysis.status=available", analysis_a.get("status") == "available", str(analysis_a.get("status")))
            check("valid model flow keeps the model duration", analysis_a.get("used_duration_seconds") == duration, str(analysis_a.get("used_duration_seconds")))
            check("advisory title comes from the model result", bool(advisory_a) and advisory_a[0]["title"] == MODEL_TITLE, advisory_a[0]["title"] if advisory_a else "missing")

            status, read_advisory = http("GET", f"{API_URL}/api/v1/advisories/{advisory_a[0]['advisory_id']}", token=token)
            check(
                "GET /advisories returns the available model analysis",
                status == 200 and read_advisory.get("ai_analysis", {}).get("status") == "available",
                f"{status} {read_advisory.get('ai_analysis', {}).get('status')}",
            )

            runtime_after = http("GET", f"{API_URL}/api/v1/commute/runtime", token=token)[1]
            check(
                "after worker runs, heartbeat state is online from last_seen_at",
                runtime_after["worker"]["state"] == "online" and runtime_after["worker"]["last_seen_at"],
                json.dumps(runtime_after["worker"], ensure_ascii=False),
            )

            # Reload after the worker terminal state so the detail page restores
            # the finished task rather than relying on a poll timing window.
            page.reload(wait_until="networkidle")
            page.wait_for_timeout(1800)
            body = page.locator("body").inner_text()
            check("web shows the model title after the real worker finishes", MODEL_TITLE in body, MODEL_TITLE if MODEL_TITLE in body else body[:200].replace("\n", " / "))
            badge_ok = "模型分析（基于开发/测试数据）" in body
            check(
                "mock-backed advisory is labelled as test data",
                badge_ok,
                "test-data badge present" if badge_ok else body[:300].replace("\n", " / "),
            )
            check(
                "completed advice does not keep a background banner",
                "后台执行程序在线" not in body and "最近有响应" not in body,
                body[:240].replace("\n", " / "),
            )
            page.screenshot(path=str(RUNTIME / "shots" / "02-valid-model.png"), full_page=True)

            # Flow B: the client queues another check; the stub then reports an unknown
            # run outcome, and the client must still show that check's advisory.
            second = []
            for _ in range(3):
                page.get_by_role("button", name="重新分析").first.click()
                for _ in range(24):
                    second = rows("select check_id, status from commute_checks order by created_at desc")
                    if len(second) == 2:
                        break
                    page.wait_for_timeout(250)
                if len(second) == 2:
                    break
                page.wait_for_timeout(1000)
            check("second client submission queued another check", len(second) == 2 and second[0]["status"] == "queued", str(second))
            check_id_b = second[0]["check_id"]

            http("POST", f"{STUB_URL}/_debug/unknown_once")
            worker = subprocess.run(
                [str(PYTHON), "-m", "assistant_api.commute.worker", "--once"],
                cwd=API_DIR, env=BASE_ENV, text=True, capture_output=True, timeout=180,
            )
            check("worker --once exits cleanly for the unknown flow", worker.returncode == 0, (worker.stdout + worker.stderr)[-160:].replace("\n", " / "))

            for _ in range(60):
                found = rows("select status from commute_checks where check_id=?", (check_id_b,))
                if found and found[0]["status"] in ("succeeded", "degraded", "failed", "cancelled", "unknown"):
                    break
                time.sleep(0.5)

            advisory_b = rows("select * from commute_advisories where check_id=?", (check_id_b,))
            analysis_b = json.loads(advisory_b[0]["ai_analysis_json"]) if advisory_b else {}
            check("unknown model flow stores ai_analysis.status=unknown", analysis_b.get("status") == "unknown", str(analysis_b.get("status")))
            check("unknown model flow still stores an advisory", len(advisory_b) == 1, advisory_b[0]["advisory_id"] if advisory_b else "missing")

            status, read_b = http("GET", f"{API_URL}/api/v1/advisories/{advisory_b[0]['advisory_id']}", token=token)
            check(
                "unknown analysis is readable instead of 500",
                status == 200 and read_b.get("ai_analysis", {}).get("status") == "unknown",
                f"{status} {read_b.get('ai_analysis', {}).get('status')}",
            )

            unknown_copy = False
            for _ in range(30):
                body = page.locator("body").inner_text()
                if "模型分析未完成" in body:
                    unknown_copy = True
                    break
                page.wait_for_timeout(1000)
            check("web shows the unknown-analysis copy", unknown_copy, "unknown copy present" if "模型分析未完成" in body else body[:200].replace("\n", " / "))
            page.screenshot(path=str(RUNTIME / "shots" / "03-unknown-model.png"), full_page=True)

            runtime_end = http("GET", f"{API_URL}/api/v1/commute/runtime", token=token)[1]
            check(
                "heartbeat stays online after both flows",
                runtime_end["worker"]["state"] == "online",
                runtime_end["worker"]["state"],
            )
            check(
                "queue is empty after both checks (no duplicate execution)",
                runtime_end["queue"]["queued"] == 0 and runtime_end["queue"]["running"] == 0,
                json.dumps(runtime_end["queue"]),
            )

            browser.close()

        summary = {
            "worktree": str(ROOT),
            "db": str(DB),
            "ports": {"api": args.api_port, "web": args.web_port, "stub": args.nexus_port},
            "checks": [{"name": n, "pass": p, "detail": d} for n, p, d in RESULTS],
        }
        (RUNTIME / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        failed = [name for name, ok, _ in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        print(f"artifacts: {RUNTIME}")
        if failed:
            print("FAILED: " + "; ".join(failed))
            return 1
        return 0
    finally:
        stop(web)
        stop(api)
        stop(stub)
        logs.close()


if __name__ == "__main__":
    raise SystemExit(main())
