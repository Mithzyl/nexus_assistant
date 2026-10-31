"""Browser checks for chat history, weather/progress, and session safety.

Run through run_web_e2e.py. The fixture and isolated services are created there;
this script only needs E2E_BASE_URL, E2E_FIXTURE, and E2E_SHOT_DIR.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3300")
FIXTURE = json.loads(Path(os.environ["E2E_FIXTURE"]).read_text())
SHOTS = Path(os.environ.get("E2E_SHOT_DIR", "tests/e2e/.artifacts"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []
page_errors: list[str] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    ok = bool(condition)
    results.append((name, ok, str(extra)))
    print(("PASS" if ok else "FAIL"), name, str(extra)[:300], flush=True)


def wait(page, ms: int = 800) -> None:
    page.wait_for_timeout(ms)


def text(page) -> str:
    return page.locator("body").inner_text()


def go(page, path: str, ms: int = 900) -> None:
    page.goto(BASE + path, wait_until="networkidle")
    wait(page, ms)


def login(page, email: str, password: str) -> None:
    go(page, "/login", 300)
    page.fill("#login-email", email)
    page.fill("#login-password", password)
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    wait(page, 800)


def storage_set(page, key: str, value: object) -> None:
    page.evaluate(
        "([name, payload]) => localStorage.setItem(name, JSON.stringify(payload))",
        [key, value],
    )


def db_count(sql: str, args: tuple = ()) -> int:
    connection = sqlite3.connect(FIXTURE["database"])
    try:
        return int(connection.execute(sql, args).fetchone()[0])
    finally:
        connection.close()


def ask_item(
    *,
    server_id: str | None,
    question: str,
    phase: str,
    answer: str | None = None,
    error: str | None = None,
    idempotency_key: str = "",
    created: datetime | None = None,
) -> dict:
    stamp = (created or datetime.now(UTC)).isoformat().replace("+00:00", "Z")
    return {
        "id": f"local-{server_id or idempotency_key}",
        "question": question,
        "idempotencyKey": idempotency_key,
        "serverId": server_id,
        "phase": phase,
        "answer": answer,
        "errorMessage": error,
        "createdAt": stamp,
        "updatedAt": stamp,
        "completedAt": stamp if phase == "succeeded" else None,
        "pollingPaused": False,
    }


def pending_record(
    plan_id: str,
    check: dict,
    *,
    status: str,
    phase: str | None,
    advisory_id: str | None = None,
    error: str | None = None,
    plan_revision: int = 2,
) -> dict:
    return {
        "planId": plan_id,
        "planRevision": plan_revision,
        "idempotencyKey": f"e2e-pending-{check['check_id']}",
        "requestHash": "no-route-snapshot",
        "checkId": check["check_id"],
        "status": status,
        "phase": phase,
        "analysisMode": "full",
        "phaseUpdatedAt": FIXTURE["now"],
        "attemptCount": 0,
        "nextAttemptAt": None,
        "advisoryId": advisory_id,
        "error": error,
        "errorCode": None,
        "createdAt": FIXTURE["now"],
        "updatedAt": FIXTURE["now"],
        "serverCreatedAt": check["created_at"],
        "finishedAt": check["finished_at"],
    }


def main() -> int:
    plan_a = FIXTURE["plans"]["advice"]
    plan_b = FIXTURE["plans"]["progress"]
    plan_c = FIXTURE["plans"]["unconfigured"]
    plan_d = FIXTURE["plans"]["create"]
    plan_e = FIXTURE["plans"]["unknown"]
    plan_g = FIXTURE["plans"]["linked"]
    plan_h = FIXTURE["plans"]["revision"]
    user_a = FIXTURE["user_a_id"]
    user_b = FIXTURE["user_b_id"]
    ask_key = f"pa.web.asks.{user_a}"
    check_key_b = f"pa.web.commute.check.{user_a}.{plan_b}"
    check_key_d = f"pa.web.commute.check.{user_a}.{plan_d}"
    check_key_e = f"pa.web.commute.check.{user_a}.{plan_e}"
    check_key_g = f"pa.web.commute.check.{user_a}.{plan_g}"
    check_key_h = f"pa.web.commute.check.{user_a}.{plan_h}"
    history_key = f"pa.web.advisory.history.{user_a}.{plan_a}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        verify_bodies: list[list[str]] = []
        check_posts: list[str] = []
        check_bodies: list[str | None] = []
        advisory_reads: list[str] = []
        advisory_latest_reads: list[str] = []

        def on_request(request) -> None:
            if request.method == "POST" and request.url.endswith("/api/pa/asks/verify"):
                try:
                    verify_bodies.append(json.loads(request.post_data or "{}").get("local_run_ids", []))
                except json.JSONDecodeError:
                    verify_bodies.append([])
            if request.method == "POST" and "/checks" in request.url:
                check_posts.append(request.url)
                check_bodies.append(request.post_data)
            if request.method == "GET" and "/api/pa/advisories/" in request.url:
                advisory_reads.append(request.url)
                if request.url.endswith("/advisories/latest"):
                    advisory_latest_reads.append(request.url)

        page.on("request", on_request)

        # 1. Login errors and session cookie.
        go(page, "/login", 300)
        page.fill("#login-email", FIXTURE["email_a"])
        page.fill("#login-password", "wrong-password")
        page.locator("form").get_by_role("button", name="登录").click()
        wait(page, 900)
        check("wrong password rejected", "邮箱或密码不正确" in text(page))
        check("still on login after failure", page.url.endswith("/login"), page.url)

        login(page, FIXTURE["email_a"], FIXTURE["password"])
        cookies = {cookie["name"]: cookie for cookie in context.cookies()}
        check("session cookie is httpOnly", cookies.get("pa_session", {}).get("httpOnly") is True)
        check("session cookie is secure", cookies.get("pa_session", {}).get("secure") is True)
        check(
            "session cookie sameSite lax",
            cookies.get("pa_session", {}).get("sameSite") == "Lax",
        )

        # 2. Cross-site write is rejected without ending the session.
        evil = context.request.post(
            BASE + "/api/auth/session/renew",
            headers={"Origin": "https://evil.example"},
            data={},
        )
        check("cross-site renew rejected", evil.status == 403, evil.status)
        same = context.request.post(
            BASE + "/api/auth/session/renew",
            headers={"Origin": BASE},
            data={},
        )
        check("same-origin renew works", same.status == 200, same.status)
        go(page, "/ask", 700)
        check("session retained after rejected write", "E2E 用户 A" in text(page))

        # 3. Old local chat cache: internal/not_chat removed, unknown hidden, chat kept.
        oldest = ask_item(
            server_id=FIXTURE["runs"]["oldest_chat"],
            question=FIXTURE["questions"]["oldest_chat"],
            phase="succeeded",
            answer="OLD_CHAT_ANSWER",
            created=datetime.now(UTC) - timedelta(hours=4),
        )
        internal = ask_item(
            server_id=FIXTURE["runs"]["internal"],
            question=FIXTURE["questions"]["internal"],
            phase="failed",
            error="服务器未能完成这次处理。",
            created=datetime.now(UTC) - timedelta(hours=3),
        )
        unknown = ask_item(
            server_id=FIXTURE["runs"]["unknown"],
            question=FIXTURE["questions"]["unknown"],
            phase="succeeded",
            answer="UNKNOWN_SECRET_ANSWER",
            created=datetime.now(UTC) - timedelta(hours=2),
        )
        unsent = ask_item(
            server_id=None,
            question=FIXTURE["questions"]["unsent"],
            phase="submissionUncertain",
            idempotency_key="e2e-unsent-key-2026",
            error="上次提交结果暂不确定：请求可能已到达服务器，也可能没有。重试会复用同一个请求键。",
            created=datetime.now(UTC) - timedelta(minutes=30),
        )
        storage_set(page, ask_key, [internal, oldest, unknown, unsent])
        page.goto(BASE + "/ask", wait_until="networkidle")
        wait(page, 2600)
        body = text(page)

        check("old local chat remains visible", FIXTURE["questions"]["oldest_chat"] in body)
        check("old local chat answer readable", "OLD_CHAT_ANSWER" in body)
        check("internal prompt is not displayed", "请作为通勤分析器" not in body)
        check("internal JSON is not displayed", '"task":"commute_analysis"' not in body)
        check("unknown body is not displayed", FIXTURE["questions"]["unknown"] not in body)
        check("unknown answer is not displayed", "UNKNOWN_SECRET_ANSWER" not in body)
        check("unknown is labelled pending", "这条记录暂未确认" in body)
        check("unsent local question remains visible", FIXTURE["questions"]["unsent"] in body)
        check("unsent can still retry", "重试同一次提交" in body)
        check(
            "no hidden internal bubble exists",
            page.locator(".bubble-user", has_text="请作为通勤分析器").count() == 0,
        )
        check("failed chat is shown honestly", "失败的聊天问题" in body and "这次没有完成" in body)
        check(
            "server history newest chat is present",
            "E2E 聊天问题 20" in body,
        )
        sent_ids = {value for batch in verify_bodies for value in batch}
        check("verify called for cached ids", bool(verify_bodies), verify_bodies)
        check(
            "verify covers internal, chat and unknown",
            {
                FIXTURE["runs"]["internal"],
                FIXTURE["runs"]["oldest_chat"],
                FIXTURE["runs"]["unknown"],
            }.issubset(sent_ids),
            sorted(sent_ids),
        )
        check("verify does not include unsent input", "e2e-unsent-key-2026" not in str(verify_bodies))
        stored = page.evaluate(
            "([key]) => JSON.parse(localStorage.getItem(key) || '[]')", [ask_key]
        )
        by_server = {item.get("serverId"): item for item in stored if item.get("serverId")}
        check(
            "internal record removed from local cache",
            FIXTURE["runs"]["internal"] not in {item.get("serverId") for item in stored},
        )
        unknown_stored = by_server.get(FIXTURE["runs"]["unknown"], {})
        check(
            "unknown classification persisted",
            unknown_stored.get("classification") == "unknown",
        )
        check(
            "unknown original body is retained locally but hidden",
            unknown_stored.get("answer") == "UNKNOWN_SECRET_ANSWER",
        )
        verify_bodies.clear()
        page.get_by_role("button", name="重新核对").first.click()
        wait(page, 1200)
        recheck_ids = {value for batch in verify_bodies for value in batch}
        check(
            "unknown can be rechecked later",
            FIXTURE["runs"]["unknown"] in recheck_ids,
            recheck_ids,
        )
        page.screenshot(path=str(SHOTS / "ask-history.png"), full_page=True)

        # 4. A verified chat stays readable when the history request fails.
        page.route(
            re.compile(r".*/api/pa/asks\?limit=20$"),
            lambda route: route.abort(),
        )
        page.reload(wait_until="networkidle")
        wait(page, 1200)
        body = text(page)
        check("verified chat survives history failure", FIXTURE["questions"]["oldest_chat"] in body)
        check("unknown stays hidden while offline", FIXTURE["questions"]["unknown"] not in body)
        check("internal stays absent while offline", "请作为通勤分析器" not in body)
        page.unroute(re.compile(r".*/api/pa/asks\?limit=20$"))
        page.reload(wait_until="networkidle")
        wait(page, 1200)

        # 5. Advice detail: newest weather and analysis-time weather stay separate.
        storage_set(
            page,
            history_key,
            [
                {"cachedAt": FIXTURE["now"], "advisory": FIXTURE["advisories"]["current"]},
                {"cachedAt": FIXTURE["now"], "advisory": FIXTURE["advisories"]["expired"]},
                {"cachedAt": FIXTURE["now"], "advisory": FIXTURE["advisories"]["old_revision"]},
                {"cachedAt": FIXTURE["now"], "advisory": FIXTURE["advisories"]["legacy"]},
            ],
        )
        check_posts.clear()
        go(page, f"/me/commute/{plan_a}", 2200)
        body = text(page)
        check("no check created on detail load", len(check_posts) == 0, check_posts)
        check("latest weather area is present", "最新天气" in body)
        check("analysis-time weather is labelled", "上次分析采用的天气" in body)
        check("latest weather value shown", "18°C" in body and "降雨 60%" in body, body[:500])
        check("old advice keeps its weather", "21°C" in body and "降雨 20%" in body)
        check("weather update copy is present", "天气已更新" in body)
        check(
            "weather update does not claim reanalysis",
            "出发建议仍来自上一次分析" in body,
        )
        check("old plan revision is labelled", "设置已更新" in body)
        check("expired advice is labelled", "已过期" in body)
        check("no stale old-label copy", "旧计划版本" not in body)
        history_count = page.locator(".history-advice").count()
        check("expired, revision and legacy records are history", history_count == 3, history_count)
        legacy_card = page.locator(".history-advice", has_text=FIXTURE["titles"]["legacy"])
        if legacy_card.count() == 1:
            legacy_card.locator("summary").click()
            wait(page, 300)
        body = text(page)
        check("legacy total buffer is labelled", "历史总预留" in body and "未拆分 AI 额外" in body)
        check("legacy does not invent AI zero", "AI 额外 0" not in body)
        check(
            "expired title lives in history",
            page.locator(".history-advice", has_text=FIXTURE["titles"]["expired"]).count() == 1,
        )
        check(
            "current departure is shown only for current advice",
            "今天 09:00 的通勤建议" in body,
        )
        page.screenshot(path=str(SHOTS / "commute-advice.png"), full_page=True)
        page.reload(wait_until="networkidle")
        wait(page, 1500)
        check("reload still does not create a check", len(check_posts) == 0, check_posts)

        # 6. Running check shows the real phase and a saved-but-unavailable worker.
        storage_set(
            page,
            check_key_b,
            pending_record(
                plan_b,
                FIXTURE["checks"]["progress"],
                status="running",
                phase="fetching_weather",
            ),
        )
        check_posts.clear()
        go(page, f"/me/commute/{plan_b}", 1200)
        body = text(page)
        check("running phase is shown", "核对通勤时段天气" in body)
        check("missing worker is explained", "尚未收到执行程序的响应" in body)
        check("task is described as saved", "任务已保存" in body)
        check("manual reconcile action exists", "继续核对" in body or "继续等待" in body)
        check("no check created while restoring", len(check_posts) == 0, check_posts)
        check(
            "progress plan has one server check",
            db_count("select count(*) from commute_checks where plan_id=?", (plan_b,)) == 1,
        )
        page.screenshot(path=str(SHOTS / "commute-progress.png"), full_page=True)
        page.reload(wait_until="networkidle")
        wait(page, 1200)
        check("progress reload does not create a check", len(check_posts) == 0, check_posts)
        stored_pending = page.evaluate(
            "([key]) => JSON.parse(localStorage.getItem(key) || 'null')", [check_key_b]
        )
        check(
            "progress reload restores the same check",
            stored_pending and stored_pending["checkId"] == FIXTURE["checks"]["progress"]["check_id"],
            stored_pending,
        )

        # 7. A real click creates once; refresh restores the same check.
        check_posts.clear()
        go(page, f"/me/commute/{plan_d}", 900)
        page.get_by_role("button", name="重新分析").first.click()
        wait(page, 1600)
        body = text(page)
        check("explicit reanalysis posts once", len(check_posts) == 1, check_posts)
        check(
            "reanalysis sends no client route snapshot",
            all(not body for body in check_bodies),
            check_bodies,
        )
        check("queued check is shown as waiting", "等待处理" in body)
        check(
            "create plan has one check",
            db_count("select count(*) from commute_checks where plan_id=?", (plan_d,)) == 1,
        )
        first_pending = page.evaluate(
            "([key]) => JSON.parse(localStorage.getItem(key) || 'null')", [check_key_d]
        )
        check("check id is saved locally", bool(first_pending and first_pending["checkId"]))
        page.reload(wait_until="networkidle")
        wait(page, 1500)
        second_pending = page.evaluate(
            "([key]) => JSON.parse(localStorage.getItem(key) || 'null')", [check_key_d]
        )
        check("reload does not create a second check", len(check_posts) == 1, check_posts)
        check(
            "reload keeps one server check",
            db_count("select count(*) from commute_checks where plan_id=?", (plan_d,)) == 1,
        )
        check(
            "reload restores the same check id",
            first_pending and second_pending and first_pending["checkId"] == second_pending["checkId"],
        )

        # 8. Unknown result is not success or failure.
        storage_set(
            page,
            check_key_e,
            pending_record(
                plan_e,
                FIXTURE["checks"]["unknown"],
                status="unknown",
                phase=None,
                error="服务器暂时没有返回这次检查；任务记录仍保存在本机。",
            ),
        )
        go(page, f"/me/commute/{plan_e}", 1000)
        body = text(page)
        check("unknown result is labelled", "尚未确认本次结果" in body)
        check("unknown keeps a reconcile action", "继续核对" in body)
        check("unknown is not shown as failure", "这次重新分析没有完成" not in body)

        # 9b. A pending operation from a previous plan revision is retired.
        storage_set(
            page,
            check_key_h,
            pending_record(
                plan_h,
                FIXTURE["checks"]["revision"],
                status="succeeded",
                phase="done",
                plan_revision=1,
            ),
        )
        check_posts.clear()
        go(page, f"/me/commute/{plan_h}", 1300)
        body = text(page)
        check("old revision operation is retired", "计划已更新" in body)
        check("old revision advice is labelled", "设置已更新" in body)
        check("retired operation does not create a check", len(check_posts) == 0, check_posts)
        retired = page.evaluate(
            "([key]) => JSON.parse(localStorage.getItem(key) || 'null')", [check_key_h]
        )
        check("retired operation is cleared", retired is None, retired)

        # 9. A terminal check reads its own advisory_id, not latest.
        storage_set(
            page,
            check_key_g,
            pending_record(
                plan_g,
                FIXTURE["checks"]["linked"],
                status="running",
                phase="saving",
            ),
        )
        advisory_reads.clear()
        advisory_latest_reads.clear()
        go(page, f"/me/commute/{plan_g}", 1600)
        body = text(page)
        check("linked advisory title is shown", FIXTURE["titles"]["linked"] in body)
        check("latest advisory is not substituted", "另一条最新建议" not in body)
        check(
            "linked advisory is fetched by id",
            any(FIXTURE["advisories"]["linked"]["id"] in url for url in advisory_reads),
            advisory_reads,
        )
        check(
            "no latest read when restoring a check",
            len(advisory_latest_reads) == 0,
            advisory_latest_reads,
        )

        # 10. Route provider unconfigured is explicit; weather/history remain readable.
        go(page, f"/me/commute/{plan_c}", 1500)
        body = text(page)
        check("route unconfigured is explicit", "路线服务尚未开通" in body)
        check("weather missing is honest", "今天还没有天气记录" in body)
        check("historical advice still readable", FIXTURE["titles"]["unconfigured"] in body)
        check("no fabricated route source", "实时路线查询" not in body, body[:400])
        page.screenshot(path=str(SHOTS / "commute-unconfigured.png"), full_page=True)

        # 11. Narrow width and long chat are checked on account A before switching.
        page.set_viewport_size({"width": 360, "height": 844})
        go(page, "/ask", 1200)
        long_body = text(page)
        metrics = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        check("long chat text does not overflow", metrics["scroll"] <= metrics["client"] + 1, metrics)
        check("long chat is rendered", "长回答：" in long_body)
        page.set_viewport_size({"width": 390, "height": 844})

        # 12. Session renewal, logout, and account switching.
        go(page, "/me", 500)
        page.get_by_role("button", name="延长登录").click()
        wait(page, 1000)
        check("renewal is server-confirmed", "已延长登录。" in text(page))
        page.get_by_role("button", name="退出登录").click()
        wait(page, 300)
        page.locator('[role="alertdialog"]').get_by_role("button", name="退出登录").click()
        page.wait_for_url("**/today", timeout=10000)
        wait(page, 600)
        check("logout returns to guest", "登录后同步今日安排" in text(page), text(page)[:200])
        login(page, FIXTURE["email_b"], FIXTURE["password"])
        go(page, "/ask", 1800)
        body = text(page)
        check("second account sees its own chat", FIXTURE["questions"]["b"] in body)
        check("second account does not see first chat", FIXTURE["questions"]["oldest_chat"] not in body)
        check("second account has no first-account cache", "E2E 聊天问题 20" not in body)

        # 13. Narrow width and dark mode on account B.
        page.set_viewport_size({"width": 360, "height": 844})
        for path in ("/today", "/ask", f"/me/commute/{plan_a}", "/me"):
            go(page, path, 800)
            metrics = page.evaluate(
                "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
            )
            check(
                f"no horizontal overflow at 360 {path}",
                metrics["scroll"] <= metrics["client"] + 1,
                metrics,
            )
        go(page, "/me", 600)
        before = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        page.get_by_role("button", name="深色", exact=True).click()
        wait(page, 500)
        after = page.evaluate("() => getComputedStyle(document.body).backgroundColor")
        check("dark mode changes surface", before != after, f"{before} -> {after}")
        page.screenshot(path=str(SHOTS / "dark-mode.png"), full_page=True)

        check("no page errors", not page_errors, page_errors)
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED:", failed)
    if page_errors:
        print("PAGEERRORS:", page_errors)
    return 0 if not failed and not page_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
