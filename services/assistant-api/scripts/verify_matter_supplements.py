"""Verify one supplement-to-arrangement flow against real local Nexus and isolated SQLite.

Run from services/assistant-api with existing dependencies:
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/verify_matter_supplements.py \
    --env-file /absolute/path/to/.env --output-dir /private/tmp/new-directory
Only synthetic records enter the new database. Real model calls are limited to
one supplement analysis and one bounded arrangement-change conversation. No
production database, map, weather or notification request is used.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import func, select

from assistant_api.core.config import Settings
from assistant_api.main import create_app
from assistant_api.models import MatterTodo, NexusRun, ScheduleEvent, User
from assistant_api.models.supplement import MatterSupplement, SupplementText
from assistant_api.services import supplements
from assistant_api.services.execution_context import dispatch_local
from migrate_sqlite import upgrade


async def checked(client, method, path, body=None, expected=200):
    """Exercise the real authenticated route and retain explicit failure evidence."""
    response = await client.request(method, path, json=body)
    assert response.status_code == expected, (method, path, response.status_code, response.text[:1000])
    return response.json()


async def verify(args):
    """Run one related scenario, verifying stored rows and model input rather than reply prose."""
    args.output_dir.mkdir(parents=True, exist_ok=False)
    settings = Settings(
        _env_file=args.env_file, environment="test",
        database_url=f"sqlite+aiosqlite:///{args.output_dir / 'assistant.db'}",
        allow_schema_create=False, commute_provider_mode="mock",
        commute_auto_ai_enabled=False, commute_auto_weather_enabled=False,
        matter_ai_timeout_seconds=180,
    )
    app = create_app(settings)
    report = {"status": "running", "started_at": datetime.now(UTC).isoformat(), "checks": []}
    try:
        await upgrade(settings.database_url)
        report["checks"].append("在独立数据库执行现有增量迁移入口，创建补充记录和来源文本表")
        async with app.router.lifespan_context(app):
            async with app.state.session_factory() as db:
                user = User(email=f"{uuid4()}@supplement-verification.invalid", display_name="补充记录隔离验证",
                            password_verifier="disabled-verification-login", status="active")
                other = User(email=f"{uuid4()}@supplement-verification.invalid", display_name="其他隔离账号",
                             password_verifier="disabled-verification-login", status="active")
                db.add_all([user, other]); await db.commit()
                user_id, other_id = user.user_id, other.user_id
            transport = httpx.ASGITransport(app=partial(dispatch_local, app, user_id=user_id))
            async with httpx.AsyncClient(transport=transport, base_url="http://isolated-assistant", timeout=600) as client:
                schedule = await checked(client, "POST", "/api/v1/schedules", {
                    "title": "套餐核对会", "starts_at": "2030-07-01T15:00:00+08:00",
                    "location": "会议室B", "idempotency_key": str(uuid4()),
                }, 201)
                mid = schedule["matter"]["matter_id"]
                todo = await checked(client, "POST", "/api/v1/todos", {
                    "title": "核对套餐清单", "matter_id": mid,
                    "expected_matter_version": schedule["matter"]["version"], "idempotency_key": str(uuid4()),
                }, 201)
                text = "销售品指套餐，例如三千兆升级包会带一个5G升级包组合出账。请补充一条后续核对建议，只做说明，不改变日程或待办。"
                body = {"text": text, "idempotency_key": str(uuid4())}
                path = f"/api/v1/matters/{mid}/supplements"
                saved = await checked(client, "POST", path, body, 201)
                sid = saved["supplement_id"]
                assert saved["saved"] and saved["ai_status"] == "queued"
                assert saved["texts"][0]["text"] == text and saved["texts"][0]["origin"] == "user"
                assert saved["texts"][0]["author_user_id"] == user_id
                again = await checked(client, "POST", path, body, 201)
                assert again["supplement_id"] == sid and again["replayed"]
                forged = await client.post(path, json={**body, "origin": "assistant"})
                assert forged.status_code == 422
                conflict = await client.post(path, json={**body, "text": "不同的输入"})
                assert conflict.status_code == 409
                foreign = httpx.ASGITransport(app=partial(dispatch_local, app, user_id=other_id))
                async with httpx.AsyncClient(transport=foreign, base_url="http://isolated-assistant") as denied:
                    assert (await denied.get(f"{path}/{sid}")).status_code == 404
                    assert (await denied.post(path, json=body)).status_code == 404
                report["checks"].append("用户原文先保存；重复提交不重复添加；来源字段不可伪造；账号权限隔离")
                print("用户补充已保存，开始唯一一次补充内容的真实模型调用。", flush=True)
                assert await supplements.worker_tick(app, worker_id="supplement-verification")
                analyzed = await checked(client, "GET", f"{path}/{sid}")
                assert analyzed["ai_status"] in {"succeeded", "no_supplement"}, analyzed
                generated = [item for item in analyzed["texts"] if item["origin"] == "assistant"]
                assert generated, "The explicit request for a suggestion must produce a persisted AI text"
                assert all(item["author_user_id"] is None and item["source_run_id"] for item in generated)
                assert all(not item["accepted_at"] for item in generated)
                detail = await checked(client, "GET", f"/api/v1/matters/{mid}")
                assert len(detail["schedules"]) == 1 and len(detail["todos"]) == 1
                assert detail["schedules"][0]["title"] == "套餐核对会"
                assert detail["todos"][0]["title"] == "核对套餐清单"
                report["checks"].append("真实模型生成内容单独保存；AI 整理未改变日程或待办")
                accepted = await checked(client, "POST", f"{path}/{sid}/texts/{generated[0]['text_id']}/accept", {})
                adopted = next(item for item in accepted["texts"] if item["text_id"] == generated[0]["text_id"])
                assert adopted["origin"] == "assistant" and adopted["accepted_by_user_id"] == user_id
                correction = await checked(client, "POST", path, {
                    "text": "补充说明：这里的组合出账不是两个互不相关的独立产品。",
                    "parent_text_id": generated[0]["text_id"], "idempotency_key": str(uuid4()),
                }, 201)
                assert correction["texts"][0]["origin"] == "user"
                assert correction["texts"][0]["parent_text_id"] == generated[0]["text_id"]
                report["checks"].append("采纳保留 AI 来源；用户修订另存原文并关联原建议")
                request_text = ("修改当前事项已有安排：将2030年7月1日15点、会议室B的套餐核对会改名为套餐规则核对会，"
                                "时间地点不变；将已有待办核对套餐清单改名为确认组合出账清单，不标记完成，"
                                "不增加其他日程或待办，也不执行补充记录里的AI建议。")
                print("开始唯一一次安排变更的真实模型会话，核对日程和待办的原记录修改。", flush=True)
                changed = await checked(client, "POST", f"/api/v1/matters/{mid}/intake", {
                    "text": request_text, "idempotency_key": str(uuid4()),
                })
                assert changed["outcome"] == "answer", changed
                async with app.state.session_factory() as db:
                    actual_schedule = await db.get(ScheduleEvent, schedule["schedule"]["schedule_id"])
                    actual_todo = await db.get(MatterTodo, todo["todo"]["todo_id"])
                    assert actual_schedule.title == "套餐规则核对会" and actual_schedule.location == "会议室B"
                    assert actual_todo.title == "确认组合出账清单" and actual_todo.status == "open"
                    assert await db.scalar(select(func.count()).select_from(ScheduleEvent).where(ScheduleEvent.matter_id == mid)) == 1
                    assert await db.scalar(select(func.count()).select_from(MatterTodo).where(MatterTodo.matter_id == mid)) == 1
                    root = await db.get(NexusRun, changed["local_run_id"])
                    context = root.context_json
                    assert root.user_id == user_id and context["actor"]["user_id"] == user_id
                    assert context["current_message"] == {"origin": "user", "text": request_text}
                    records = context["supplement_context"]
                    assert any(item["origin"] == "user" and item["text"] == text for item in records)
                    assert any(item["origin"] == "assistant" and item["accepted_by_user_id"] == user_id for item in records)
                    assert any(item["origin"] == "user" and item["parent_text_id"] == generated[0]["text_id"] for item in records)
                    assert len(context["api_flow"]["receipts"]) >= 2
                    assert context["unified_intake_result"]["answer"] == changed["answer"]
                    rows = list(await db.scalars(select(NexusRun).where(NexusRun.user_id == user_id)))
                    report["model_runs"] = [{"local_run_id": row.local_run_id, "purpose": row.purpose,
                                             "status": row.status, "workflow_execution_id": row.workflow_execution_id} for row in rows]
                    report["supplement_id"] = sid
                    report["arrangement_run_id"] = root.local_run_id
                report["checks"].append("真实 AI 通过既有接口修改原日程和原待办；调用用户、原文、AI 来源及实际执行结果均持久化")
                # Verify failure preservation locally without another model request.
                async with app.state.session_factory() as db:
                    pending = await db.get(MatterSupplement, correction["supplement_id"])
                    pending.ai_status = "running"
                    pending.lease_expires_at = datetime(2000, 1, 1, tzinfo=UTC)
                    await db.commit()
                await supplements.recover_expired(app)
                retained = await checked(client, "GET", f"{path}/{correction['supplement_id']}")
                assert retained["ai_status"] == "unknown" and retained["saved"]
                assert retained["texts"][0]["text"].startswith("补充说明")
                report["checks"].append("处理中断保留用户原文并标为结果未知，不自动重发模型调用")
                report["status"] = "passed"
    except Exception as exc:
        report.update(status="failed", error_type=type(exc).__name__, error=str(exc)[:2000])
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        (args.output_dir / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


def main():
    """Require explicit existing configuration and a new isolated output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--finish-interruption-check", action="store_true",
                        help="Continue only the local interrupted-claim check in an existing verified database; no model calls.")
    args = parser.parse_args()
    asyncio.run(finish_interruption_check(args) if args.finish_interruption_check else verify(args))


async def finish_interruption_check(args):
    """Continue a failed local recovery assertion without replaying any successful real-model work."""
    result_path = args.output_dir / "result.json"
    report = json.loads(result_path.read_text())
    assert report["status"] == "failed" and report["error_type"] == "TypeError"
    assert report["model_runs"] and len(report["checks"]) == 5
    settings = Settings(_env_file=args.env_file, environment="test",
                        database_url=f"sqlite+aiosqlite:///{args.output_dir / 'assistant.db'}",
                        allow_schema_create=False, commute_provider_mode="mock",
                        commute_auto_ai_enabled=False, commute_auto_weather_enabled=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as db:
            root = await db.get(MatterSupplement, report["supplement_id"])
            pending = await db.scalar(select(MatterSupplement).where(
                MatterSupplement.matter_id == root.matter_id, MatterSupplement.ai_status == "running",
            ))
            assert pending is not None
            correction_id = pending.supplement_id
            before = await db.scalar(select(func.count()).select_from(NexusRun))
        await supplements.recover_expired(app)
        async with app.state.session_factory() as db:
            pending = await db.get(MatterSupplement, correction_id)
            assert pending.ai_status == "unknown"
            text = await db.scalar(select(SupplementText).where(
                SupplementText.supplement_id == correction_id, SupplementText.origin == "user"))
            assert text.text.startswith("补充说明") and text.parent_text_id
            after = await db.scalar(select(func.count()).select_from(NexusRun))
            assert before == after == len(report["model_runs"])
            for row in report["model_runs"]:
                run = await db.get(NexusRun, row["local_run_id"])
                assert run.status == "succeeded" and run.user_id == root.user_id
    (args.output_dir / "initial-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    report["initial_failure"] = {"type": report.pop("error_type"), "message": report.pop("error")}
    report["checks"].append("处理中断保留用户原文并标为结果未知，不自动重发模型调用")
    report["status"] = "passed"
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["local_continuation_only"] = True
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
