"""Browser checks for the local work-plan preview UI.

The backend has no matter/schedule API yet, so this suite verifies the explicit
preview interaction only: grouping, detail blocks, local edits, conflict choice,
archive/delete, PWA shell and the separation from real server data.
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
    print(("PASS" if ok else "FAIL"), name, str(extra)[:240], flush=True)


def text(page) -> str:
    return page.locator("body").inner_text()


def login(page) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#login-email", FIXTURE["email_a"])
    page.fill("#login-password", FIXTURE["password"])
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    page.wait_for_timeout(500)


def no_overflow(page, path: str) -> None:
    page.goto(BASE + path, wait_until="networkidle")
    page.wait_for_timeout(500)
    metrics = page.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
    check(f"no horizontal overflow at 360 {path}", metrics["scroll"] <= metrics["client"], metrics)


def main() -> int:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page)

        # Bottom navigation uses the required labels, not the old names.
        body = text(page)
        check("bottom nav says 事项", "事项" in body)
        check("bottom nav says 问助手", "问助手" in body)

        # PWA shell.
        manifest_response = context.request.get(BASE + "/manifest.webmanifest")
        manifest = manifest_response.json()
        check("PWA manifest is served", manifest_response.ok, manifest_response.status)
        check("PWA starts standalone at /today", manifest.get("display") == "standalone" and manifest.get("start_url") == "/today", manifest)
        icon = context.request.get(BASE + "/icon-192.png")
        check("PWA icon is served", icon.ok and icon.headers.get("content-type", "").startswith("image/png"), icon.status)

        # Lab is the only entry to the old consumer demos.
        page.goto(BASE + "/me/lab", wait_until="networkidle")
        page.wait_for_timeout(400)
        body = text(page)
        check("lab is explicit", "实验室" in body and "概念预览" in body)
        check("old consumer demos live in lab", "退款归属" in body and "愿望清单" in body)
        check("lab preview entry exists", "计划接续预览" in body)
        page.screenshot(path=str(SHOTS / "plan-lab.png"), full_page=True)

        # Start preview and open today before changing the demo data.
        page.get_by_role("button", name=re.compile("打开事项预览")).click()
        page.wait_for_url("**/matters", timeout=10000)
        page.wait_for_timeout(900)
        body = text(page)
        check("matters header is 事项", "需要持续推进的事" in body)
        check("three required groups", all(label in body for label in ("正在推进", "等待外部反馈", "已完成／归档")), body[:300])
        check("work matter is shown", "与林总谈年度合作合同" in body)
        check("old consumer amount is not in the work list", "¥129" not in body and "便携咖啡机" not in body)
        page.screenshot(path=str(SHOTS / "plan-matters.png"), full_page=True)

        page.goto(BASE + "/today", wait_until="networkidle")
        page.wait_for_timeout(700)
        body = text(page)
        check("today has attributable overview", "今天有" in body)
        check("today has preview timeline", "今日时间线" in body and "静安寺洗牙" in body)
        check("today shows a real conflict card", "时间可能来不及" in body and "需要你决定" in body)
        check("today does not claim server save", "概念预览" in body and "本机预览" in body)
        page.screenshot(path=str(SHOTS / "plan-today-conflict.png"), full_page=True)

        page.get_by_role("button", name=re.compile("时间可能来不及")).click()
        page.wait_for_timeout(300)
        check("conflict opens a bottom sheet", "这次安排怎么调整" in text(page))
        page.get_by_role("radio", name=re.compile("面谈改到 15:30")).click()
        page.get_by_role("button", name="按此更新预览安排").click()
        page.wait_for_timeout(500)
        check("confirmed option clears the conflict", "时间可能来不及" not in text(page))

        # Detail, four blocks and local corrections.
        page.goto(BASE + "/matters", wait_until="networkidle")
        page.get_by_role("link", name=re.compile("与林总谈年度合作合同")).first.click()
        page.wait_for_timeout(500)
        body = text(page)
        check("detail has four blocks", all(label in body for label in ("目标与背景", "已确定安排", "待处理清单", "跟进记录")), body[:300])
        check("detail labels preview sync", "应用内预览" in body and "未写入 iPhone" in body)
        check("detail has all bottom actions", all(label in body for label in ("补充进展", "停止跟进", "删除")))
        page.screenshot(path=str(SHOTS / "plan-detail.png"), full_page=True)

        page.get_by_role("button", name="纠正").first.click()
        page.wait_for_timeout(250)
        page.get_by_label("事项标题").fill("与林总谈年度合作合同（已纠正）")
        page.get_by_role("button", name="保存到本机预览").click()
        page.wait_for_timeout(400)
        check("title correction is applied", "（已纠正）" in text(page))

        page.get_by_role("button", name=re.compile("修改 静安寺洗牙")).click()
        page.wait_for_timeout(250)
        page.get_by_label("标题").fill("静安寺洗牙（改）")
        page.get_by_role("button", name="保存到本机预览").click()
        page.wait_for_timeout(400)
        check("schedule correction is applied", "静安寺洗牙（改）" in text(page))
        check("schedule edit is labelled preview", "未写入 iPhone" in text(page))

        page.get_by_role("button", name="补充进展").click()
        page.wait_for_timeout(250)
        page.get_by_label("这次发生了什么").fill("法务确认周四上午发修订版。")
        page.get_by_role("button", name="保存进展到预览").click()
        page.wait_for_timeout(400)
        check("progress is appended", "法务确认周四上午发修订版" in text(page))

        page.get_by_role("button", name=re.compile("催法务发合同修订版")).click()
        page.wait_for_timeout(250)
        check("todo can be checked locally", page.get_by_role("button", name=re.compile("催法务发合同修订版")).get_attribute("aria-pressed") == "true")

        page.reload(wait_until="networkidle")
        page.wait_for_timeout(500)
        check("local preview survives reload", "（已纠正）" in text(page) and "法务确认周四上午" in text(page))

        # Stop following moves to archive without deleting the history.
        page.get_by_role("button", name="停止跟进").click()
        page.wait_for_timeout(250)
        page.get_by_role("button", name="停止跟进并归档").click()
        page.wait_for_url("**/matters", timeout=10000)
        page.wait_for_timeout(500)
        check("stopped matter is archived", "已完成／归档" in text(page) and "与林总谈年度合作合同（已纠正）" in text(page))

        # Delete a different preview matter and ensure reload does not restore it.
        page.get_by_role("link", name=re.compile("供应商续约")).click()
        page.wait_for_timeout(400)
        page.get_by_role("button", name="删除").click()
        page.wait_for_timeout(250)
        page.get_by_role("button", name="彻底删除本机预览").click()
        page.wait_for_url("**/matters", timeout=10000)
        page.wait_for_timeout(500)
        check("deleted matter leaves the list", "供应商续约" not in text(page))
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(400)
        check("deleted matter is not restored on reload", "供应商续约" not in text(page))

        # No false iOS promise in the preview screens.
        body = text(page)
        check("no false iPhone write claim", "已写入你的 iPhone 系统日历" not in body and "已写入 iPhone 系统日历" not in body)
        check("no page errors", not errors, errors)

        page.set_viewport_size({"width": 360, "height": 780})
        no_overflow(page, "/today")
        no_overflow(page, "/matters")
        page.goto(BASE + "/matters", wait_until="networkidle")
        page.get_by_role("link", name=re.compile("与林总谈年度合作合同（已纠正）")).first.click()
        page.wait_for_timeout(400)
        metrics = page.evaluate("() => ({scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth})")
        check("no horizontal overflow at 360 detail", metrics["scroll"] <= metrics["client"], metrics)
        page.screenshot(path=str(SHOTS / "plan-detail-360.png"), full_page=True)

        page.set_viewport_size({"width": 900, "height": 900})
        page.goto(BASE + "/today", wait_until="networkidle")
        page.wait_for_timeout(300)
        wide = page.evaluate("() => ({ app: document.querySelector('.m-app')?.getBoundingClientRect().width, scroll: document.documentElement.scrollWidth, client: document.documentElement.clientWidth })")
        check("wide screen keeps the reading column", bool(wide["app"]) and wide["app"] <= 480 and wide["scroll"] <= wide["client"], wide)

        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} work-plan checks passed")
    if failed:
        print("FAILED:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
