"""Real matters/draft/today browser checks against backend/dev 02a79ed2.

These run against the isolated database seeded by seed_web_fixture.py and the
real API from E2E_BACKEND_SRC. The local preview stays in /me/lab/plan and is
checked separately, never mixed into the formal list.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3300")
FIXTURE = json.loads(Path(os.environ["E2E_FIXTURE"]).read_text())
SHOTS = Path(os.environ.get("E2E_SHOT_DIR", ".tmp/e2e/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    ok = bool(condition)
    results.append((name, ok, str(extra)))
    print(("PASS" if ok else "FAIL"), name, str(extra)[:260], flush=True)


def body(page) -> str:
    return page.locator("body").inner_text()


def login(page, email: str) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#login-email", email)
    page.fill("#login-password", FIXTURE["password"])
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    page.wait_for_timeout(600)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page, FIXTURE["email_a"])
        nav = body(page)
        check("bottom navigation has four entries", all(label in nav for label in ("今天", "事项", "问助手", "我的")))
        manifest_response = context.request.get(BASE + "/manifest.webmanifest")
        manifest = manifest_response.json()
        check("PWA manifest is standalone at /today", manifest_response.ok and manifest.get("display") == "standalone" and manifest.get("start_url") == "/today")
        icon = context.request.get(BASE + "/icon-192.png")
        check("PWA icon is served", icon.ok and icon.headers.get("content-type", "").startswith("image/png"))

        # Real today response, no fake empty state, pending draft count.
        page.goto(BASE + "/today", wait_until="networkidle")
        page.wait_for_timeout(900)
        text = body(page)
        check("today shows real schedules", "静安寺洗牙" in text and "陆家嘴面谈" in text, text[:260])
        check("today shows the real reminder", "催法务发合同修订版" in text)
        check("today is not a fake empty state", "今天没有安排" not in text)
        check("today exposes pending draft count", "待确认草稿" in text and "去问助手查看" in text)
        today_str = __import__("datetime").datetime.now(__import__("zoneinfo").ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        check("today has real date/timezone", today_str in text and "Asia/Shanghai" in text, text[:120])
        page.screenshot(path=str(SHOTS / "real-today.png"), full_page=True)

        # Real grouped list, no consumer demo amounts.
        page.goto(BASE + "/matters", wait_until="networkidle")
        page.wait_for_timeout(700)
        text = body(page)
        check("real matters are grouped", all(label in text for label in ("正在推进", "等待外部反馈", "已完成／归档")), text[:260])
        check("real work matter is listed", "与林总谈年度合作合同" in text)
        check("consumer demo is not mixed into formal list", "¥129" not in text and "便携咖啡机" not in text)
        page.screenshot(path=str(SHOTS / "real-matters.png"), full_page=True)

        page.get_by_role("link", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(700)
        text = body(page)
        check("detail is from server", "服务器保存" in text)
        check("detail has four blocks", all(label in text for label in ("目标与背景", "已确定安排", "待处理清单", "跟进记录")), text[:300])
        check("detail shows real schedule and status", "静安寺洗牙" in text and "已记入应用内日程" in text)
        check("detail shows real todo and progress", "催法务发合同修订版" in text and "把合同初稿发给法务" in text)
        check("version is not shown as product metadata", "版本 3" not in text and "version" not in text.lower())
        check("no fake iPhone write", "已写入你的 iPhone 系统日历" not in text and "已写入 iPhone 系统日历" not in text)
        page.screenshot(path=str(SHOTS / "real-detail.png"), full_page=True)

        # Restore the pending draft from the server, not page memory.
        page.goto(BASE + "/ask", wait_until="networkidle")
        page.wait_for_timeout(900)
        check("pending draft is restored in ask", "待确认整理" in body(page), body(page)[:220])
        page.get_by_role("button", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(900)
        sheet = page.locator(".sheet-panel")
        check("draft sheet opens", "确认事项变更" in sheet.inner_text(), sheet.inner_text()[:300])
        check("draft computes a server conflict", "时间冲突" in sheet.inner_text() or "调整本次时间" in sheet.inner_text(), sheet.inner_text()[:300])
        original_title = page.get_by_label("标题", exact=True).input_value()
        page.get_by_label("标题", exact=True).fill("不应保存的标题")
        page.get_by_role("button", name="关闭，不提交").click()
        page.wait_for_timeout(250)
        check("unsaved draft edit warns before close", page.get_by_text("有未保存的修改").count() > 0)
        page.get_by_role("button", name="放弃修改并关闭").click()
        page.wait_for_timeout(300)
        page.get_by_role("button", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(800)
        check("closing draft discards unsaved edit", page.get_by_label("标题", exact=True).input_value() == original_title)

        # Apply an option to the preview, then explicitly confirm it.
        options = page.locator(".sheet-panel .choice-card")
        check("server returns adjustment options", options.count() >= 2, options.count())
        options.first.click()
        page.wait_for_timeout(900)
        check("option application stays in preview", "调整方案已写入预览" in page.locator(".sheet-panel").inner_text(), page.locator(".sheet-panel").inner_text()[:250])
        acknowledge = page.get_by_role("checkbox", name=re.compile("我已了解"))
        if acknowledge.count():
            acknowledge.check()
        page.get_by_role("button", name="确认更新").click()
        page.wait_for_timeout(1500)
        check("confirm closes the draft sheet", page.locator(".sheet-panel").count() == 0)

        page.goto(BASE + "/matters", wait_until="networkidle")
        page.get_by_role("link", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(800)
        check("confirmed schedule is in the real detail", "临时电话" in body(page), body(page)[:300])
        page.screenshot(path=str(SHOTS / "real-confirmed.png"), full_page=True)

        # Direct title correction uses PATCH + expected_version and persists.
        page.get_by_role("button", name="纠正").first.click()
        page.wait_for_timeout(250)
        page.get_by_label("标题", exact=True).fill("与林总谈年度合作合同（真实纠正）")
        page.get_by_role("button", name="保存修改").click()
        page.wait_for_timeout(900)
        check("title edit persisted", "（真实纠正）" in body(page))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(700)
        check("title edit survives reload", "（真实纠正）" in body(page))

        # Todo completion writes to the server and survives reload.
        todo = page.locator(".todo-row").filter(has_text="催法务发合同修订版").locator(".todo-check")
        before = todo.get_attribute("aria-pressed")
        todo.click()
        page.wait_for_timeout(800)
        check("todo completes on server", page.locator(".todo-row").filter(has_text="催法务发合同修订版").locator(".todo-check").get_attribute("aria-pressed") != before)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(700)

        # Delete the waiting matter; it must not return after reload.
        page.goto(BASE + "/matters", wait_until="networkidle")
        page.get_by_role("link", name=re.compile("供应商续约")).click()
        page.wait_for_timeout(700)
        page.get_by_role("button", name="删除").click()
        page.wait_for_timeout(250)
        page.get_by_role("button", name="彻底删除").click()
        page.wait_for_url("**/matters", timeout=10000)
        page.wait_for_timeout(700)
        check("deleted matter leaves the list", "供应商续约" not in body(page))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(600)
        check("deleted matter is not restored", "供应商续约" not in body(page))

        # Lab preview remains isolated and browser-local.
        page.goto(BASE + "/me/lab", wait_until="networkidle")
        page.wait_for_timeout(300)
        page.get_by_role("button", name=re.compile("打开事项预览")).click()
        page.wait_for_url("**/me/lab/plan", timeout=10000)
        page.wait_for_timeout(700)
        check("preview stays in the lab", "概念预览" in body(page) and "与林总谈年度合作合同" in body(page))
        check("preview is not the formal matters route", page.url.endswith("/me/lab/plan"))
        page.get_by_role("link", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(400)
        page.get_by_role("button", name="纠正").first.click()
        page.wait_for_timeout(250)
        page.get_by_label("事项标题").fill("A 账号的本机预览标题")
        page.get_by_role("button", name="保存到本机预览").click()
        page.wait_for_timeout(400)
        check("preview edit is local", "A 账号的本机预览标题" in body(page))

        # Account switch must not show account A's matters for B.
        page.goto(BASE + "/me", wait_until="networkidle")
        page.get_by_role("button", name="退出登录").click()
        page.wait_for_timeout(250)
        page.locator('[role="alertdialog"]').get_by_role("button", name="退出登录").click()
        page.wait_for_url("**/today", timeout=10000)
        login(page, FIXTURE["email_b"])
        page.goto(BASE + "/matters", wait_until="networkidle")
        page.wait_for_timeout(700)
        check("account B has no account A matters", "与林总谈年度合作合同" not in body(page) and "真实纠正" not in body(page))
        page.goto(BASE + "/me/lab/plan", wait_until="networkidle")
        page.wait_for_timeout(700)
        check("account B does not inherit A preview edits", "A 账号的本机预览标题" not in body(page))

        check("no page errors", not errors, errors)
        page.set_viewport_size({"width": 360, "height": 780})
        page.goto(BASE + "/today", wait_until="networkidle")
        page.wait_for_timeout(500)
        metrics = page.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
        check("no horizontal overflow at 360", metrics["scroll"] <= metrics["client"], metrics)
        page.screenshot(path=str(SHOTS / "real-today-360.png"), full_page=True)
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} real matter checks passed")
    if failed:
        print("FAILED:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
