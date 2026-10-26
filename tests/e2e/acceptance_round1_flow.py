"""Round-1 joint acceptance: one real background commute flow plus cross-client checks.

Everything here runs from THIS worktree (the temporary acceptance merge):
  * backend: services/assistant-api (editable install in .tmp/backend-venv)
  * upstream: services/nexus-stub on a private port (simulated Nexus, in-memory)
  * web: the production build in apps/web, pointed at the isolated API
  * database: a fresh SQLite file under .tmp/acceptance-flow/ (never the shared one)
  * ports: 8310/8311 API, 8320 Nexus stub, 3310 web (all checked to be free)

What it proves, separately labelled:
  1. BACKGROUND_FLOW  a client-submitted check is queued in the DB, claimed and
     advanced by the real worker, an advisory is stored and linked by check_id,
     and the client shows that advisory; a reload keeps the same check.
  2. CHAT_SHARED      one account's chat is one server record that both clients
     read (API surface used by both), and an internal commute run is absent.
  3. WEATHER_SEPARATE simulated new provider weather does not rewrite the
     advisory that was computed earlier.
  4. ACCOUNT_ISOLATION  a second account sees none of the first account's data.

It does NOT prove real Nexus, real weather/route providers, or notification
delivery; those are labelled simulated/mock in the summary.
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
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_DIR = ROOT / "services" / "assistant-api"
WEB_DIR = ROOT / "apps" / "web"
NEXUS_DIR = ROOT / "services" / "nexus-stub"
RUNTIME = ROOT / ".tmp" / "acceptance-flow"

APP_TOKEN = "stub-application-token-32-characters"
TOOL_TOKEN = "tool-callback-token-32-characters"
PASSWORD = "acceptance-password-2026"
ACCOUNT_A = "acceptance-a@example.com"
ACCOUNT_B = "acceptance-b@example.com"

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
        except Exception as caught:  # noqa: BLE001 - startup probe
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


def http(
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict]:
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
            parsed = {"raw": raw.decode(errors="replace")[:400]}
        return caught.code, parsed


def db(sql: str, args: tuple = ()) -> list[dict]:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute(sql, args)]
    finally:
        connection.close()


def login(email: str) -> str:
    status, body = http(
        "POST", f"{API_URL}/api/v1/auth/login", payload={"email": email, "password": PASSWORD}
    )
    if status != 200:
        raise RuntimeError(f"login failed for {email}: {status} {body}")
    return body["token"]


def seed_user(email: str, display_name: str) -> None:
    environment = {
        **BASE_ENV,
        "ASSISTANT_PROVISION_PASSWORD": PASSWORD,
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
    }
    done = subprocess.run(
        [
            str(PYTHON),
            "-m",
            "assistant_api.commands.seed_dev_user",
            "--email",
            email,
            "--display-name",
            display_name,
        ],
        cwd=API_DIR,
        env=environment,
        text=True,
        capture_output=True,
    )
    if done.returncode != 0:
        raise RuntimeError(f"seed {email} failed: {done.stdout}\n{done.stderr}")


def run_worker_once() -> tuple[int, str]:
    done = subprocess.run(
        [str(PYTHON), "-m", "assistant_api.commute.worker", "--once"],
        cwd=API_DIR,
        env=BASE_ENV,
        text=True,
        capture_output=True,
        timeout=180,
    )
    return done.returncode, (done.stdout + done.stderr)[-1200:]


def local_pending_check(page) -> dict | None:
    raw = page.evaluate(
        """() => {
            const out = {};
            for (let i = 0; i < localStorage.length; i += 1) {
                const key = localStorage.key(i);
                out[key] = localStorage.getItem(key);
            }
            return out;
        }"""
    )
    for value in raw.values():
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict) and "checkId" in parsed and "planId" in parsed:
            return parsed
    return None


def main() -> int:
    global API_URL, BASE_ENV, PYTHON, DB_PATH

    parser = argparse.ArgumentParser()
    parser.add_argument("--api-port", type=int, default=8310)
    parser.add_argument("--web-port", type=int, default=3310)
    parser.add_argument("--nexus-port", type=int, default=8320)
    args = parser.parse_args()

    assistant_python = os.environ.get("ASSISTANT_PYTHON")
    if not assistant_python or not Path(assistant_python).exists():
        print("ASSISTANT_PYTHON must point at the acceptance worktree venv", file=sys.stderr)
        return 2
    PYTHON = Path(assistant_python)

    for port, label in (
        (args.api_port, "api"),
        (args.web_port, "web"),
        (args.nexus_port, "nexus-stub"),
    ):
        if not port_free(port):
            print(f"port {port} ({label}) is already in use", file=sys.stderr)
            return 2

    if RUNTIME.exists():
        shutil.rmtree(RUNTIME)
    (RUNTIME / "shots").mkdir(parents=True)
    DB_PATH = RUNTIME / "assistant.db"
    API_URL = f"http://127.0.0.1:{args.api_port}"
    NEXUS_URL = f"http://127.0.0.1:{args.nexus_port}"
    WEB_URL = f"http://localhost:{args.web_port}"

    BASE_ENV = {
        **os.environ,
        "ASSISTANT_ENVIRONMENT": "development",
        "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH}",
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "false",
        "ASSISTANT_NEXUS_BASE_URL": NEXUS_URL,
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

    logs = (RUNTIME / "processes.log").open("w")
    nexus = api = web = None
    try:
        print("==> starting simulated upstream (nexus stub)", flush=True)
        nexus = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.nexus_port),
                "--log-level",
                "warning",
            ],
            cwd=NEXUS_DIR,
            env={**BASE_ENV, "STUB_APP_TOKEN": APP_TOKEN, "STUB_APP_ID": "personal_assistant"},
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{NEXUS_URL}/health", timeout=20, label="nexus stub")

        print("==> creating schema and synthetic accounts (single process)", flush=True)
        seed_user(ACCOUNT_A, "验收甲")
        seed_user(ACCOUNT_B, "验收乙")

        print(f"==> starting isolated API on {API_URL}", flush=True)
        api = subprocess.Popen(
            [
                str(PYTHON),
                "-m",
                "uvicorn",
                "assistant_api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(args.api_port),
                "--log-level",
                "warning",
            ],
            cwd=API_DIR,
            env=BASE_ENV,
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{API_URL}/health", timeout=40, label="assistant-api")
        imported = subprocess.run(
            [str(PYTHON), "-c", "import assistant_api;print(assistant_api.__file__)"],
            cwd=API_DIR,
            text=True,
            capture_output=True,
        ).stdout.strip()
        check("backend imports from this acceptance worktree", str(ROOT) in imported, imported)

        token_a = login(ACCOUNT_A)
        token_b = login(ACCOUNT_B)
        print("==> users seeded and logged in", flush=True)

        shanghai = timezone(timedelta(hours=8))
        target = (datetime.now(shanghai) + timedelta(minutes=15)).replace(second=0, microsecond=0)

        status, plan = http(
            "POST",
            f"{API_URL}/api/v1/commute-plans",
            token=token_a,
            payload={
                "name": "验收通勤",
                "enabled": True,
                "timezone": "Asia/Shanghai",
                "weekdays": [1, 2, 3, 4, 5, 6, 7],
                "arrival_local_time": target.strftime("%H:%M"),
                "check_local_time": (target - timedelta(minutes=10)).strftime("%H:%M"),
                "transport_mode": "driving",
                "buffer_minutes": 10,
                "notify_enabled": False,
                "origin": {"label": "验收起点", "latitude": 31.2304, "longitude": 121.4737},
                "destination": {"label": "验收终点", "latitude": 31.2019, "longitude": 121.606},
            },
        )
        if status != 201:
            raise RuntimeError(f"plan creation failed: {status} {plan}")
        plan_id = plan["id"]
        check("plan created for synthetic account", True, plan_id)

        print(f"==> starting web build on {WEB_URL}", flush=True)
        web = subprocess.Popen(
            [str(WEB_DIR / "node_modules" / ".bin" / "next"), "start", "-p", str(args.web_port)],
            cwd=WEB_DIR,
            env={**os.environ, "PA_API_URL": API_URL},
            stdout=logs,
            stderr=subprocess.STDOUT,
        )
        wait_http(f"{WEB_URL}/today", timeout=60, label="web")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(f"{WEB_URL}/login", wait_until="networkidle")
            page.fill("#login-email", ACCOUNT_A)
            page.fill("#login-password", PASSWORD)
            page.get_by_role("button", name="登录").click()
            page.wait_for_url("**/today", timeout=20000)
            check("web login succeeds against merged backend", True)

            page.goto(f"{WEB_URL}/me/commute/{plan_id}", wait_until="networkidle")
            page.wait_for_timeout(800)
            before = db("select count(*) as n from commute_checks")[0]["n"]
            check("opening the plan page creates no check", before == 0, f"checks={before}")

            page.get_by_role("button", name="重新分析").first.click()
            queued = []
            for _ in range(20):
                queued = db("select check_id, status, phase from commute_checks")
                if queued:
                    break
                page.wait_for_timeout(250)
            if not queued:
                print("page text after click: " + page.locator("body").inner_text()[:600], flush=True)
            check("client submission queued exactly one check", len(queued) == 1, str(queued))
            check_id = queued[0]["check_id"] if queued else None
            check(
                "queued check is recorded in the database before the worker runs",
                bool(queued) and queued[0]["status"] in ("queued", "running"),
                str(queued[0]) if queued else "",
            )
            body = page.locator("body").inner_text()
            check(
                "client shows waiting state, not a fabricated stage",
                ("等待处理" in body or "处理中" in body) and "已完成" not in body,
                body[:120].replace("\n", " / "),
            )
            page.screenshot(path=str(RUNTIME / "shots" / "01-queued.png"), full_page=True)

            returncode, worker_out = run_worker_once()
            check("worker --once exits cleanly", returncode == 0, worker_out[-200:].replace("\n", " / "))

            terminal = {}
            for _ in range(60):
                rows = db("select status, phase from commute_checks where check_id=?", (check_id,))
                if rows and rows[0]["status"] in ("succeeded", "degraded", "failed", "cancelled"):
                    terminal = rows[0]
                    break
                time.sleep(0.5)
            check(
                "worker advanced the queued check to a terminal status",
                bool(terminal) and terminal["status"] in ("succeeded", "degraded"),
                str(terminal),
            )
            check(
                "worker recorded an execution phase",
                bool(terminal) and bool(terminal.get("phase")),
                str(terminal.get("phase")),
            )

            advisory = db("select * from commute_advisories where check_id=?", (check_id,))
            check(
                "advisory stored and linked to this check",
                len(advisory) == 1,
                advisory[0]["advisory_id"] if advisory else "missing",
            )
            advisory_id = advisory[0]["advisory_id"] if advisory else None
            status, read_check = http(
                "GET", f"{API_URL}/api/v1/checks/{check_id}", token=token_a
            )
            check(
                "GET /checks returns this check's advisory_id",
                status == 200 and read_check.get("advisory_id") == advisory_id,
                str(read_check.get("advisory_id")),
            )
            status, read_advisory = http(
                "GET", f"{API_URL}/api/v1/advisories/{advisory_id}", token=token_a
            )
            check(
                "GET /advisories/{advisory_id} returns this advisory for the plan",
                status == 200
                and read_advisory.get("id") == advisory_id
                and read_advisory.get("plan_id") == plan_id
                and advisory[0]["check_id"] == check_id,
                f"{status} id={read_advisory.get('id')}",
            )

            page.wait_for_timeout(4000)
            body = page.locator("body").inner_text()
            check("client reached a completed state", "已完成" in body, body[:100].replace("\n", " / "))
            internal_runs = db(
                "select count(*) as n from nexus_runs where purpose='commute_internal'"
            )[0]["n"]
            check(
                "exactly one internal model run backs this check",
                internal_runs == 1,
                f"commute_internal={internal_runs}",
            )
            page.screenshot(path=str(RUNTIME / "shots" / "02-result.png"), full_page=True)

            pending = local_pending_check(page) or {}
            check(
                "client stored the server check_id",
                bool(pending) and pending.get("checkId") == check_id,
                str(pending.get("checkId")),
            )

            page.close()
            page = context.new_page()
            page.goto(f"{WEB_URL}/me/commute/{plan_id}", wait_until="networkidle")
            page.wait_for_timeout(2500)
            restored = local_pending_check(page) or {}
            check(
                "reopening the page restores the same check",
                bool(restored) and restored.get("checkId") == check_id,
                str(restored.get("checkId")),
            )
            check(
                "reopening created no second check",
                db("select count(*) as n from commute_checks")[0]["n"] == 1,
                f"checks={db('select count(*) as n from commute_checks')[0]['n']}",
            )
            check(
                "reopening created no second advisory",
                db("select count(*) as n from commute_advisories")[0]["n"] == 1,
                f"advisories={db('select count(*) as n from commute_advisories')[0]['n']}",
            )
            check(
                "reopening created no second model run",
                db(
                    "select count(*) as n from nexus_runs where purpose='commute_internal'"
                )[0]["n"]
                == internal_runs,
                "commute_internal unchanged",
            )
            page.screenshot(path=str(RUNTIME / "shots" / "03-restored.png"), full_page=True)

            # --- cross-client: shared chat, account isolation, weather separation
            ask_text = f"验收问题 {uuid.uuid4().hex[:6]}：今天要带伞吗"
            status, ask = http(
                "POST",
                f"{API_URL}/api/v1/asks",
                token=token_a,
                payload={"text": ask_text, "idempotency_key": uuid.uuid4().hex},
            )
            check("chat submission accepted by simulated upstream", status == 202, f"{status} {ask}")
            run_id = ask.get("local_run_id")

            status, listed = http("GET", f"{API_URL}/api/v1/asks", token=token_a)
            items = listed if isinstance(listed, list) else listed.get("items", [])
            check(
                "the same account reads its own chat from the server",
                status == 200 and any(item.get("local_run_id") == run_id for item in items),
                f"{status} items={len(items)}",
            )
            internal_texts = [item.get("request_text", "") for item in items]
            check(
                "no internal commute prompt is listed as a chat",
                all("通勤" not in text and "分析器" not in text for text in internal_texts),
                str(internal_texts[:2]),
            )
            chat_store = db("select count(*) as n from user_asks")[0]["n"]
            check("server records the chat as a user ask", chat_store >= 1, f"user_asks={chat_store}")

            page.goto(f"{WEB_URL}/ask", wait_until="networkidle")
            page.wait_for_timeout(1500)
            ask_body = page.locator("body").inner_text()
            check(
                "web shows the same chat text the API returned",
                ask_text.split("：")[0] in ask_body or "今天要带伞吗" in ask_body,
                ask_body[:80].replace("\n", " / "),
            )

            # simulated provider weather update: old advisory must keep its own basis
            snapshot = db("select * from commute_advisories where advisory_id=?", (advisory_id,))[0]
            old_weather = json.loads(snapshot["weather_json"] or "{}")
            weather_rows = db("select * from commute_weather_snapshots")
            check("a weather snapshot exists for the executed check", len(weather_rows) >= 1, str(len(weather_rows)))
            if weather_rows:
                db_path = str(RUNTIME / "assistant.db")
                connection = sqlite3.connect(db_path)
                current_summary = json.loads(weather_rows[0]["summary_json"] or "{}")
                simulated = {**current_summary, "temperature_c": 9.9, "coverage": "full"}
                connection.execute(
                    "update commute_weather_snapshots set summary_json=?, freshness='fresh' where snapshot_id=?",
                    (json.dumps(simulated), weather_rows[0]["snapshot_id"]),
                )
                connection.commit()
                connection.close()
                status, weather = http(
                    "GET",
                    f"{API_URL}/api/v1/commute-plans/{plan_id}/weather",
                    token=token_a,
                )
                after = db("select * from commute_advisories where advisory_id=?", (advisory_id,))[0]
                check(
                    "new provider weather is readable without rewriting the old advisory",
                    status == 200
                    and after["weather_json"] == snapshot["weather_json"]
                    and after["generated_at"] == snapshot["generated_at"]
                    and json.loads(after["weather_json"] or "{}") == old_weather,
                    f"{status} changed={after['weather_json'] != snapshot['weather_json']}",
                )

            status, plans_b = http("GET", f"{API_URL}/api/v1/commute-plans", token=token_b)
            status_asks_b, asks_b = http("GET", f"{API_URL}/api/v1/asks", token=token_b)
            items_b = asks_b if isinstance(asks_b, list) else asks_b.get("items", [])
            check(
                "second account sees no first-account plans or chats",
                status == 200
                and (plans_b if isinstance(plans_b, list) else plans_b.get("items", [])) == []
                and items_b == [],
                f"plans={len(plans_b if isinstance(plans_b, list) else plans_b.get('items', []))} asks={len(items_b)}",
            )

            status, me_b = http("GET", f"{API_URL}/api/v1/auth/me", token=token_b)
            check("second account identity differs", status == 200 and me_b.get("email") == ACCOUNT_B, str(me_b.get("email")))

            browser.close()

        summary = {
            "worktree": str(ROOT),
            "backend_python": str(PYTHON),
            "backend_import": imported,
            "web_dir": str(WEB_DIR),
            "db": str(DB_PATH),
            "ports": {"api": args.api_port, "web": args.web_port, "nexus": args.nexus_port},
            "checks": [{"name": n, "pass": p, "detail": d} for n, p, d in RESULTS],
        }
        (RUNTIME / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
        failed = [name for name, ok, _ in RESULTS if not ok]
        print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
        print(f"artifacts: {RUNTIME}")
        if failed:
            print("FAILED: " + ", ".join(failed))
            return 1
        return 0
    finally:
        stop(web)
        stop(api)
        stop(nexus)
        logs.close()


if __name__ == "__main__":
    raise SystemExit(main())
