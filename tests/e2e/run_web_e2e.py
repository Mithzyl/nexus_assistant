"""Repeatable mobile-web browser checks against an isolated PA backend.

It creates a fresh SQLite database, seeds synthetic plans/chats/weather, starts
the real assistant-api with a fixed clock, starts the production Next.js build,
and runs the Playwright checks. It never starts or writes the shared backend.

Usage:
    ASSISTANT_PYTHON=/path/to/python pnpm --filter @superapp/web test:e2e
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"


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
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except Exception as caught:  # noqa: BLE001 - startup probe
            last_error = caught
        time.sleep(0.4)
    raise RuntimeError(f"{label} did not become ready: {url} ({last_error})")


def stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-port", type=int, default=3300)
    parser.add_argument("--api-port", type=int, default=8300)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument(
        "--only",
        choices=(
            "web_flow_check.py",
            "runtime_state_check.py",
            "work_plan_check.py",
            "matters_enrichment_check.py",
            "matter_option_alignment_check.py",
        ),
    )
    args = parser.parse_args()

    assistant_python = os.environ.get("ASSISTANT_PYTHON")
    if not assistant_python:
        print(
            "ASSISTANT_PYTHON is required (a Python with services/assistant-api deps).\n"
            "Example: python3 -m venv .venv && .venv/bin/pip install -e services/assistant-api",
            file=sys.stderr,
        )
        return 2
    if not Path(assistant_python).exists():
        print(f"ASSISTANT_PYTHON does not exist: {assistant_python}", file=sys.stderr)
        return 2

    for port, label in ((args.web_port, "web"), (args.api_port, "api")):
        if not port_free(port):
            print(f"port {port} ({label}) is already in use", file=sys.stderr)
            return 2

    if not args.skip_build:
        print("==> building web", flush=True)
        build = subprocess.run(
            [
                os.environ.get("PNPM_BIN", "pnpm"),
                "--filter",
                "@superapp/web",
                "build",
            ],
            cwd=ROOT,
            text=True,
        )
        if build.returncode != 0:
            return build.returncode
    elif not (WEB / ".next" / "BUILD_ID").exists():
        print("apps/web/.next/BUILD_ID is missing; run without --skip-build", file=sys.stderr)
        return 2

    runtime = ROOT / ".tmp" / "e2e"
    if runtime.exists():
        shutil.rmtree(runtime)
    runtime.mkdir(parents=True)
    shots = runtime / "shots"
    shots.mkdir()
    database = runtime / "assistant.db"
    fixture = runtime / "fixture.json"
    backend_src = Path(
        os.environ.get(
            "E2E_BACKEND_SRC",
            str(ROOT / "services" / "assistant-api" / "src"),
        )
    ).resolve()

    seed = subprocess.run(
        [
            assistant_python,
            str(ROOT / "tests" / "e2e" / "seed_web_fixture.py"),
            "--database",
            str(database),
            "--fixture",
            str(fixture),
        ],
        cwd=ROOT,
        env={**os.environ, "E2E_BACKEND_SRC": str(backend_src)},
        text=True,
        capture_output=True,
    )
    if seed.returncode != 0:
        print(seed.stdout, file=sys.stderr)
        print(seed.stderr, file=sys.stderr)
        return seed.returncode

    import json

    data = json.loads(fixture.read_text())
    now = data["now"]

    env = {
        **os.environ,
        "E2E_NOW": now,
        "E2E_API_PORT": str(args.api_port),
        "ASSISTANT_ENVIRONMENT": "development",
        "ASSISTANT_DATABASE_URL": f"sqlite+aiosqlite:///{database}",
        "ASSISTANT_ALLOW_SCHEMA_CREATE": "true",
        "ASSISTANT_NEXUS_BASE_URL": "http://127.0.0.1:8001",
        "ASSISTANT_NEXUS_APPLICATION_TOKEN": "stub-application-token-32-characters",
        "ASSISTANT_TOOL_CALLBACK_TOKEN": "tool-callback-token-32-characters",
        "ASSISTANT_COMMUTE_PROVIDER_MODE": "mock",
        "PYTHONPATH": str(backend_src),
        "E2E_BACKEND_SRC": str(backend_src),
        "HTTP_PROXY": "",
        "HTTPS_PROXY": "",
        "ALL_PROXY": "",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    api_log = (runtime / "api.log").open("w")
    web_log = (runtime / "web.log").open("w")
    api = None
    web = None
    try:
        print(f"==> starting isolated API on 127.0.0.1:{args.api_port}", flush=True)
        api = subprocess.Popen(
            [assistant_python, str(ROOT / "tests" / "e2e" / "serve_web_fixture_api.py")],
            cwd=ROOT,
            env=env,
            stdout=api_log,
            stderr=subprocess.STDOUT,
        )
        wait_http(
            f"http://127.0.0.1:{args.api_port}/health",
            timeout=20,
            label="isolated API",
        )

        print(f"==> starting web on 127.0.0.1:{args.web_port}", flush=True)
        web_env = {**os.environ, "PA_API_URL": f"http://127.0.0.1:{args.api_port}"}
        next_bin = WEB / "node_modules" / ".bin" / "next"
        web = subprocess.Popen(
            [str(next_bin), "start", "-p", str(args.web_port)],
            cwd=WEB,
            env=web_env,
            stdout=web_log,
            stderr=subprocess.STDOUT,
        )
        wait_http(
            f"http://localhost:{args.web_port}/today",
            timeout=30,
            label="web build",
        )

        test_env = {
            **os.environ,
            "E2E_BASE_URL": f"http://localhost:{args.web_port}",
            "E2E_API_URL": f"http://127.0.0.1:{args.api_port}",
            "E2E_FIXTURE": str(fixture),
            "E2E_SHOT_DIR": str(shots),
        }
        failed = 0
        names = (
            (args.only,)
            if args.only
            else (
                "web_flow_check.py",
                "runtime_state_check.py",
                "matters_enrichment_check.py",
            )
        )
        for name in names:
            print(f"==> running Playwright flow {name}", flush=True)
            result = subprocess.run(
                [sys.executable, str(ROOT / "tests" / "e2e" / name)],
                cwd=ROOT,
                env=test_env,
            )
            if result.returncode != 0:
                failed = result.returncode
                print(f"\n{name} failed; web log: {runtime / 'web.log'}\napi log: {runtime / 'api.log'}")
                print(f"fixture: {fixture}\nshots: {shots}")
        if not failed:
            print(f"\nfixture: {fixture}\nshots: {shots}")
        return failed
    finally:
        stop(web)
        stop(api)
        api_log.close()
        web_log.close()


if __name__ == "__main__":
    raise SystemExit(main())
