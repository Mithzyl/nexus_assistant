"""Small browser check for the latest server option and cross-matter contract."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("E2E_BASE_URL", "http://localhost:3312")
FIXTURE = json.loads(Path(os.environ["E2E_FIXTURE"]).read_text())
DATABASE = Path(FIXTURE["database"])
SHOTS = Path(os.environ.get("E2E_SHOT_DIR", ".tmp/e2e-alignment/shots"))
SHOTS.mkdir(parents=True, exist_ok=True)

results: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, extra: object = "") -> None:
    ok = bool(condition)
    results.append((name, ok, str(extra)))
    print(("PASS" if ok else "FAIL"), name, str(extra)[:320], flush=True)


def query_one(sql: str, args: tuple = ()):
    with sqlite3.connect(DATABASE) as connection:
        return connection.execute(sql, args).fetchone()


def execute(sql: str, args: tuple = ()) -> None:
    with sqlite3.connect(DATABASE) as connection:
        connection.execute(sql, args)
        connection.commit()


def login(page) -> None:
    page.goto(BASE + "/login", wait_until="networkidle")
    page.fill("#login-email", FIXTURE["email_a"])
    page.fill("#login-password", FIXTURE["password"])
    page.locator("form").get_by_role("button", name="登录").click()
    page.wait_for_url("**/today", timeout=15000)
    page.wait_for_timeout(700)


def open_pending_draft(page):
    page.goto(BASE + "/ask", wait_until="networkidle")
    page.wait_for_timeout(800)
    page.locator(".btn--secondary", has_text="与林总谈年度合作合同").first.click()
    panel = page.locator(".sheet-panel")
    panel.wait_for(state="visible")
    return panel


def main() -> int:
    matter_id = FIXTURE["matters"]["lin"]
    meeting_id = FIXTURE["schedules"]["meeting"]
    dentist_id = FIXTURE["schedules"]["dentist"]

    draft_start_raw = query_one(
        "SELECT json_extract(payload_json, '$.schedule.starts_at') "
        "FROM matter_drafts WHERE draft_id=?",
        (FIXTURE["draft"],),
    )[0]
    draft_start = datetime.fromisoformat(draft_start_raw.replace("Z", "+00:00"))
    meeting_start = draft_start + timedelta(minutes=65)
    meeting_end = meeting_start + timedelta(minutes=60)
    next_schedule_id = str(uuid4())
    next_start = draft_start + timedelta(minutes=35)
    next_end = next_start + timedelta(minutes=30)
    execute(
        "UPDATE schedules SET starts_at=?, ends_at=?, source='web_native', "
        "sync_status='local_only', matter_id=?, updated_at=? WHERE schedule_id=?",
        (
            meeting_start.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" "),
            meeting_end.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" "),
            matter_id,
            datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
            meeting_id,
        ),
    )
    execute(
        "UPDATE schedules SET source='ios_eventkit', sync_status='ios_synced', "
        "data_source_id=NULL, updated_at=? WHERE schedule_id=?",
        (
            datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
            dentist_id,
        ),
    )
    execute(
        "INSERT INTO schedules "
        "(schedule_id,user_id,title,starts_at,ends_at,location,status,source_run_id,"
        "source,sync_status,source_external_id,source_scope_key,source_updated_at,"
        "source_version,matter_id,data_source_id,latitude,longitude,deleted_at,"
        "created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            next_schedule_id,
            FIXTURE["user_a_id"],
            "后续会议",
            next_start.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" "),
            next_end.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" "),
            None,
            "scheduled",
            None,
            "web_native",
            "local_only",
            None,
            None,
            None,
            None,
            matter_id,
            None,
            None,
            None,
            None,
            datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
            datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" "),
        ),
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 390, "height": 844})
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        login(page)

        panel = open_pending_draft(page)
        option_b = panel.get_by_role("radio", name=re.compile("调整相邻安排"))
        expect(option_b).to_be_disabled()
        check("readonly option B is disabled", option_b.is_disabled())
        check(
            "readonly option shows a server reason",
            "不可执行" in option_b.inner_text()
            and "只读" in option_b.inner_text(),
            option_b.inner_text(),
        )
        panel.get_by_role("button", name="关闭，不提交").click()

        other_matter_id = str(uuid4())
        now = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
        execute(
            "INSERT INTO matters "
            "(matter_id,user_id,title,goal,status,version,next_step,"
            "last_progress_at,archived_at,deleted_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                other_matter_id,
                FIXTURE["user_a_id"],
                "另一个事项",
                "",
                "active",
                1,
                None,
                None,
                None,
                None,
                now,
                now,
            ),
        )
        execute(
            "UPDATE schedules SET source='web_native', sync_status='local_only', "
            "matter_id=?, updated_at=? WHERE schedule_id=?",
            (other_matter_id, now, dentist_id),
        )

        panel = open_pending_draft(page)
        option_b = panel.get_by_role("radio", name=re.compile("调整相邻安排"))
        expect(option_b).to_be_enabled(timeout=10000)
        check("cross-matter option B is executable", option_b.is_enabled())
        check(
            "cross-matter impact names its matter and before/after",
            "另一个事项" in option_b.inner_text()
            and "将从" in option_b.inner_text()
            and "调整为" in option_b.inner_text(),
            option_b.inner_text(),
        )

        option_b.click()
        expect(panel.locator(".notice--green")).to_be_visible(timeout=10000)
        b_impact = panel.locator(".notice--green").inner_text()
        check(
            "B returns a complete selected draft",
            "另一个事项" in b_impact and "所选方案会改变" in b_impact,
            b_impact,
        )
        panel.get_by_role("radio", name=re.compile("保持时间")).click()
        page.wait_for_timeout(900)
        c_impact = panel.locator(".notice--green").inner_text()
        check(
            "switching B to C drops B impact",
            "另一个事项" not in c_impact and "本次安排不变" in c_impact,
            c_impact,
        )
        panel.get_by_role("radio", name=re.compile("调整相邻安排")).click()
        page.wait_for_timeout(900)
        acknowledge = panel.get_by_role("checkbox", name=re.compile("我已了解"))
        if acknowledge.count() > 0 and acknowledge.is_enabled():
            acknowledge.check()
        panel.get_by_role("button", name="确认更新").click()
        panel.wait_for(state="detached", timeout=15000)

        page.goto(f"{BASE}/matters/{other_matter_id}", wait_until="networkidle")
        page.wait_for_timeout(800)
        affected_history = page.locator("#changes").inner_text()
        check(
            "affected matter sees source change after reread",
            "来源事项" in affected_history and "与林总谈年度合作合同" in affected_history,
            affected_history,
        )
        check(
            "affected matter does not offer direct revert",
            page.locator("#changes").get_by_role("button", name="撤回").count() == 0,
        )
        page.get_by_role("button", name=re.compile("到「与林总谈年度合作合同」")).click()
        page.wait_for_url(f"**/matters/{matter_id}*", timeout=10000)
        page.wait_for_timeout(800)
        page.locator("#changes").get_by_role("button", name="撤回").first.click()
        page.locator('[role="alertdialog"]').get_by_role("button", name="确认撤回").click()
        page.wait_for_timeout(1200)

        restored = query_one(
            "SELECT matter_id, starts_at, ends_at FROM schedules WHERE schedule_id=?",
            (dentist_id,),
        )
        check(
            "cross-matter revert restores the other matter schedule",
            restored[0] == other_matter_id,
            restored,
        )
        check("no page errors", not errors, errors)
        page.screenshot(path=str(SHOTS / "latest-option-alignment.png"), full_page=True)
        browser.close()

    failed = [name for name, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} latest option checks passed")
    if failed:
        print("FAILED:", failed)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
