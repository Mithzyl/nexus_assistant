"""Focused browser checks for enriched today data, direct edits and history.

The fixture database and API come from backend/dev 3e01ac1a. Model-dependent
responses are replaced at the browser proxy boundary so this check can verify
the frontend de-duplication path without calling a real model.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3300")
FIXTURE = json.loads(Path(os.environ["E2E_FIXTURE"]).read_text())
SHOTS = Path(os.environ.get("E2E_SHOT_DIR", ".tmp/e2e/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    ok = bool(condition)
    results.append((name, ok, str(extra)))
    print(("PASS" if ok else "FAIL"), name, str(extra)[:320], flush=True)


def page_text(page) -> str:
    return page.locator("body").inner_text()


def login(page) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#login-email", FIXTURE["email_a"])
    page.fill("#login-password", FIXTURE["password"])
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    page.wait_for_timeout(700)


def direct_question_response(route) -> None:
    route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(
            {
                "draft": None,
                "replayed": False,
                "intent": "question",
                "message": "这句话更像问题或闲聊，已直接回答，不会创建事项。",
                "answer": "服务器在这次整理请求中直接返回了回答。",
            },
            ensure_ascii=False,
        ),
    )


def main() -> int:
    matter_id = FIXTURE["matters"]["lin"]
    local_day = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        ask_posts: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on(
            "request",
            lambda request: ask_posts.append(request.url)
            if request.method == "POST" and request.url.endswith("/api/pa/asks")
            else None,
        )

        login(page)
        text = page_text(page)
        check("today lists overdue and due deadlines", "需要跟进" in text and "催法务发合同修订版" in text)
        check("today lists pending draft content", "待确认" in text and "临时电话" in text)
        check("today shows unverified route copy", "路线未核实" in text and "尚无与该日程关联的路线结果" in text)
        check("today separates confirmed changes", "已生效变更" in text)
        page.screenshot(path=str(SHOTS / "enriched-today.png"), full_page=True)

        draft_card = page.locator(".draft-card").first
        check("today pending draft is directly actionable", draft_card.count() == 1)
        draft_card.click()
        page.locator(".sheet-panel").wait_for(state="visible")
        sheet = page.locator(".sheet-panel")
        check("today opens the existing draft sheet", "确认事项变更" in sheet.inner_text())
        options = sheet.get_by_role("radio")
        if options.count() > 0:
            options.first.click()
            page.wait_for_timeout(900)
        acknowledge = sheet.get_by_role("checkbox", name=re.compile("我已了解"))
        if acknowledge.count() > 0 and acknowledge.is_enabled():
            acknowledge.check()
        page.get_by_role("button", name="确认更新").click()
        page.locator(".sheet-panel").wait_for(state="detached", timeout=15000)
        page.wait_for_timeout(900)
        refreshed = page_text(page)
        check("today refreshes after confirmation", "临时电话" in refreshed and page.locator(".draft-card").count() == 0)

        page.goto(f"{BASE}/matters/{matter_id}", wait_until="networkidle")
        page.wait_for_timeout(800)
        detail_text = page_text(page)
        check("history is available after navigation", "变更历史" in detail_text and "已生效" in detail_text)
        check("history uses readable result text", "日程：" in detail_text and "change" not in detail_text.lower())

        schedule = page.locator(".schedule-block").filter(has_text="陆家嘴面谈").first
        schedule.get_by_role("button", name=re.compile("调整")).click()
        page.locator(".sheet-panel").wait_for(state="visible")
        sheet = page.locator(".sheet-panel")
        check("schedule edit is schedule-only", "调整日程" in sheet.inner_text() and sheet.get_by_label("事项标题").count() == 0)
        sheet.get_by_label("日程标题").fill("陆家嘴面谈（调整）")
        sheet.get_by_label("开始").fill(f"{local_day}T16:30")
        sheet.get_by_label("结束").fill(f"{local_day}T17:30")
        sheet.get_by_role("button", name="保存修改并重新核对").click()
        page.wait_for_timeout(1000)
        check("schedule preview can be saved without a matter title", "修改已保存到预览" in sheet.inner_text())
        page.get_by_role("button", name="确认更新").click()
        page.locator(".sheet-panel").wait_for(state="detached", timeout=15000)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(800)
        check("confirmed schedule survives reload", "陆家嘴面谈（调整）" in page_text(page))
        check("confirmed schedule appears in history", "陆家嘴面谈（调整）" in page.locator("#changes").inner_text())

        page.locator("#changes").get_by_role("button", name=re.compile("撤回")).first.click()
        dialog = page.locator('[role="alertdialog"]')
        dialog.wait_for(state="visible")
        check("revert explains what will be restored", "恢复到" in dialog.inner_text())
        dialog.get_by_role("button", name="确认撤回").click()
        page.wait_for_timeout(1200)
        schedule_titles = page.locator(".schedule-block .card-title").all_inner_texts()
        check(
            "revert restores the previous schedule",
            "陆家嘴面谈（调整）" not in schedule_titles and "陆家嘴面谈" in schedule_titles,
            schedule_titles,
        )
        check("revert records a readable progress entry", "已撤回上一次确认的变更" in page_text(page))
        page.goto(BASE + "/today", wait_until="networkidle")
        page.wait_for_timeout(700)
        check("today rereads a reverted schedule", "陆家嘴面谈" in page_text(page) and "陆家嘴面谈（调整）" not in page_text(page))
        page.goto(f"{BASE}/matters/{matter_id}", wait_until="networkidle")
        page.wait_for_timeout(700)

        todo = page.locator(".todo-row").filter(has_text="催法务发合同修订版").first
        todo.get_by_role("button", name="时间").click()
        page.locator(".sheet-panel").wait_for(state="visible")
        sheet = page.locator(".sheet-panel")
        check("todo edit is todo-only", "调整待办" in sheet.inner_text() and sheet.get_by_label("事项标题").count() == 0)
        check("todo exposes separate deadline and reminder controls", sheet.get_by_label("截止时间").count() == 1 and sheet.get_by_label("提醒时间").count() == 1)
        sheet.get_by_role("button", name="清空提醒").click()
        check("clearing reminder explains pending cancellation", "确认后会取消这条待办对应的待发提醒" in sheet.inner_text())
        sheet.get_by_role("button", name="保存修改并重新核对").click()
        page.wait_for_timeout(900)
        sheet.get_by_role("button", name="确认更新").click()
        page.locator(".sheet-panel").wait_for(state="detached", timeout=15000)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(800)
        legal_row = page.locator(".todo-row").filter(has_text="催法务发合同修订版").first
        check("cleared reminder is not restored", "未设提醒" in legal_row.inner_text())

        page.route("**/api/pa/matters/intake", direct_question_response)
        page.route(re.compile(r".*/api/pa/matters/[^/]+/intake$"), direct_question_response)

        before_asks = len(ask_posts)
        page.goto(BASE + "/ask", wait_until="networkidle")
        page.locator(".composer__field").fill("今天天气怎么样？")
        page.get_by_role("button", name="整理成事项").click()
        page.wait_for_timeout(700)
        answer_text = page_text(page)
        check("organize question displays server answer once", answer_text.count("服务器在这次整理请求中直接返回了回答。") == 1)
        check("organize question does not submit a second ask", len(ask_posts) == before_asks)

        page.goto(f"{BASE}/matters/{matter_id}", wait_until="networkidle")
        page.get_by_role("button", name="补充进展").first.click()
        page.get_by_label("这次发生了什么").fill("今天天气怎么样？")
        page.get_by_role("button", name="交给服务器整理").click()
        page.get_by_text("助手回答", exact=True).wait_for(state="visible", timeout=10000)
        check("matter progress keeps a direct question answer", "服务器在这次整理请求中直接返回了回答。" in page.locator(".sheet-panel").inner_text())

        page.locator(".sheet-panel").get_by_role("button", name="关闭", exact=True).click()
        page.set_viewport_size({"width": 360, "height": 780})
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(400)
        actions = page.locator(".work-actions").bounding_box()
        tabbar = page.locator(".tabbar").bounding_box()
        check(
            "detail actions occupy space above navigation",
            bool(actions and tabbar and actions["y"] + actions["height"] <= tabbar["y"] + 1),
            {"actions": actions, "tabbar": tabbar},
        )
        metrics = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})"
        )
        check("no horizontal overflow at 360", metrics["scroll"] <= metrics["client"], metrics)
        page.screenshot(path=str(SHOTS / "enriched-detail-360.png"), full_page=True)
        check("no page errors", not errors, errors)
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} enriched matter checks passed")
    if failed:
        print("FAILED:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
