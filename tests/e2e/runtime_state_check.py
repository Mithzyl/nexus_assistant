"""Controlled browser checks for the commute runtime/worker status.

The runtime endpoint is fulfilled by the test so stale heartbeats, request
failure, aging and visibility can be exercised without waiting for a real
worker. The plans/checks themselves still come from the isolated real backend.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3300")
FIXTURE = json.loads(Path(os.environ["E2E_FIXTURE"]).read_text())
SHOTS = Path(os.environ.get("E2E_SHOT_DIR", ".tmp/e2e/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []
page_errors: list[str] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    ok = bool(condition)
    results.append((name, ok, str(extra)))
    print(("PASS" if ok else "FAIL"), name, str(extra)[:300], flush=True)


def iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def wait(page, ms: int = 700) -> None:
    page.wait_for_timeout(ms)


def text(page) -> str:
    return page.locator("body").inner_text()


def login(page, email: str, password: str) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    wait(page, 600)


def storage_set(page, key: str, value: object) -> None:
    page.evaluate(
        "([name, payload]) => localStorage.setItem(name, JSON.stringify(payload))",
        [key, value],
    )


def pending(plan_id: str, check_id: str, revision: int) -> dict:
    return {
        "planId": plan_id,
        "planRevision": revision,
        "idempotencyKey": f"e2e-runtime-{check_id}",
        "requestHash": "no-route-snapshot",
        "checkId": check_id,
        "status": "queued",
        "phase": "queued",
        "analysisMode": "full",
        "phaseUpdatedAt": FIXTURE["now"],
        "attemptCount": 0,
        "nextAttemptAt": None,
        "advisoryId": None,
        "error": None,
        "errorCode": None,
        "createdAt": FIXTURE["now"],
        "updatedAt": FIXTURE["now"],
        "serverCreatedAt": FIXTURE["now"],
        "finishedAt": None,
    }


def set_visibility(page, value: str) -> None:
    page.evaluate(
        """(state) => {
          Object.defineProperty(document, 'visibilityState', {
            configurable: true,
            get: () => state,
          });
          document.dispatchEvent(new Event('visibilitychange'));
        }""",
        value,
    )


def main() -> int:
    user_a = FIXTURE["user_a_id"]
    plan_a = FIXTURE["plans"]["queued"]
    check_a = FIXTURE["checks"]["queued"]["check_id"]
    key_a = f"pa.web.commute.check.{user_a}.{plan_a}"

    mode: dict[str, object] = {
        "kind": "ok",
        "state": "not_seen",
        "offset": 0,
        "stale": 60,
        "route": True,
    }
    request_count = [0]
    check_posts: list[str] = []

    def payload() -> dict:
        now = datetime.now(UTC)
        state = str(mode["state"])
        offset = int(mode["offset"])
        seen = None if state == "not_seen" else iso(now - timedelta(seconds=offset))
        return {
            "server_time": iso(now),
            "scheduler": {"state": state, "last_seen_at": seen},
            "worker": {"state": state, "last_seen_at": seen},
            "stale_after_seconds": int(mode["stale"]),
            "queue": {"queued": 1, "running": 0},
            "latest_check": {
                "check_id": check_a,
                "status": "queued",
                "phase": "queued",
                "phase_updated_at": FIXTURE["now"],
            },
            "last_progress_at": FIXTURE["now"],
            "route_provider_available": bool(mode["route"]),
            "auto_analysis_enabled": False,
            "auto_analysis_mode": "weather_only",
        }

    def runtime_route(route) -> None:
        request_count[0] += 1
        if mode["kind"] == "fail":
            route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {
                        "error": {
                            "code": "runtime_unavailable",
                            "message": "后台状态暂时无法读取。",
                            "retryable": True,
                        }
                    },
                    ensure_ascii=False,
                ),
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload(), ensure_ascii=False),
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))
        page.on(
            "request",
            lambda request: check_posts.append(request.url)
            if request.method == "POST" and "/checks" in request.url
            else None,
        )
        page.route(re.compile(r".*/api/pa/commute/runtime$"), runtime_route)

        login(page, FIXTURE["email_a"], FIXTURE["password"])
        storage_set(page, key_a, pending(plan_a, check_a, 2))
        page.goto(BASE + f"/me/commute/{plan_a}", wait_until="networkidle")
        wait(page, 1500)

        # 1. Route is available, worker never reported, task still queued.
        body = text(page)
        check("not_seen is labelled separately", "尚未收到执行程序的响应" in body)
        check("queued task remains waiting", "等待处理" in body and "等待后台领取" in body)
        check("route availability is not confused with heartbeat", "路线服务尚未配置" not in body)
        check("not_seen is not shown as recent", "最近有响应" not in body)
        page.screenshot(path=str(SHOTS / "runtime-not-seen.png"), full_page=True)

        # 2. A recent heartbeat does not mean the task was claimed.
        mode.update(state="online", offset=10, stale=60, kind="ok")
        page.reload(wait_until="networkidle")
        wait(page, 1200)
        body = text(page)
        check("recent heartbeat is worded as recent", "最近有响应" in body)
        check("recent heartbeat keeps queued task waiting", "等待处理" in body)
        check("recent heartbeat is not called permanently online", "后台执行程序在线" not in body)
        page.screenshot(path=str(SHOTS / "runtime-recent.png"), full_page=True)

        # 3. Heartbeat beyond the threshold stays a saved task, not a failure.
        mode.update(state="online", offset=120, stale=60, kind="ok")
        page.reload(wait_until="networkidle")
        wait(page, 1200)
        body = text(page)
        check("old heartbeat is aged to stale", "近期未响应" in body)
        check("stale heartbeat does not fail the task", "失败" not in body.split("本次操作状态")[-1][:20])
        check("stale heartbeat keeps the task queued", "等待处理" in body)
        page.screenshot(path=str(SHOTS / "runtime-stale.png"), full_page=True)

        # 4. A failed refresh must not keep the previous online claim.
        mode.update(state="online", offset=5, stale=60, kind="ok")
        page.reload(wait_until="networkidle")
        wait(page, 1000)
        check("precondition: recent before failure", "最近有响应" in text(page))
        mode.update(kind="fail")
        set_visibility(page, "visible")
        wait(page, 1200)
        body = text(page)
        check("failed runtime query is explicit", "暂时无法获取后台状态" in body)
        check("failed query does not keep recent claim", "最近有响应" not in body)
        check("failed query shows last successful query", "上次成功查询" in body)
        check("failed query does not claim worker offline", "近期未响应" not in body and "尚未收到" not in body)
        page.screenshot(path=str(SHOTS / "runtime-unavailable.png"), full_page=True)

        # 5. A long-open page ages an online heartbeat without a new response.
        mode.update(state="online", offset=0, stale=2, kind="ok")
        page.reload(wait_until="networkidle")
        wait(page, 1000)
        check("fresh heartbeat starts recent", "最近有响应" in text(page))
        wait(page, 6500)  # clock ticks every 5s; stale_after is 2s
        check("long-open page ages recent to stale", "近期未响应" in text(page))

        # 6. Hidden pages do not poll; returning refreshes immediately.
        mode.update(state="online", offset=0, stale=60, kind="ok")
        page.reload(wait_until="networkidle")
        wait(page, 800)
        before = request_count[0]
        set_visibility(page, "hidden")
        wait(page, 21_000)
        hidden_count = request_count[0]
        check("hidden page pauses runtime requests", hidden_count == before, (before, hidden_count))
        set_visibility(page, "visible")
        wait(page, 1200)
        check("visible page refreshes runtime", request_count[0] > hidden_count, (hidden_count, request_count[0]))

        # 7. Account switch does not inherit account A's runtime state.
        page.goto(BASE + "/me", wait_until="networkidle")
        page.get_by_role("button", name="退出登录").click()
        wait(page, 250)
        page.locator('[role="alertdialog"]').get_by_role("button", name="退出登录").click()
        page.wait_for_url("**/today", timeout=10000)
        login(page, FIXTURE["email_b"], FIXTURE["password"])
        key_b = f"pa.web.commute.check.{FIXTURE['user_b_id']}.{FIXTURE['plan_b']}"
        storage_set(page, key_b, pending(FIXTURE["plan_b"], FIXTURE["check_b"], 1))
        mode.update(state="not_seen", offset=0, stale=60, kind="ok")
        page.goto(BASE + f"/me/commute/{FIXTURE['plan_b']}", wait_until="networkidle")
        wait(page, 1200)
        body = text(page)
        check("account B sees its own not_seen state", "尚未收到执行程序的响应" in body)
        check("account B does not inherit A recent state", "最近有响应" not in body)

        check("runtime checks created no checks", not check_posts, check_posts)
        check("no page errors", not page_errors, page_errors)
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} runtime checks passed")
    if failed:
        print("FAILED:", failed)
    if page_errors:
        print("PAGEERRORS:", page_errors)
    return 0 if not failed and not page_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
