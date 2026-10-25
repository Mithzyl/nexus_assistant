"""Create an isolated, synthetic PA database for the mobile-web browser tests.

The shared backend is never started or touched. This script builds a fresh
SQLite file, a local account, and deterministic commute/chat records that the
browser checks can read. It writes a JSON fixture consumed by web_flow_check.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services" / "assistant-api" / "src"))

from sqlalchemy import select  # noqa: E402

from assistant_api.commute.timeutil import (  # noqa: E402
    check_window_for,
    local_today,
    target_arrival_for,
)
from assistant_api.core.security import hash_password  # noqa: E402
from assistant_api.infrastructure.database import create_engine_and_factory  # noqa: E402
from assistant_api.models import (  # noqa: E402
    Base,
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    CommutePlan,
    CommuteWeatherSnapshot,
    NexusRun,
    User,
    UserAsk,
)

TZ = "Asia/Shanghai"
PASSWORD = "e2e-password-2026"
EMAIL_A = "web-e2e-a@example.com"
EMAIL_B = "web-e2e-b@example.com"
PROMPT = (
    "请作为通勤分析器，只输出严格 JSON。输入：\n"
    '{"task":"commute_analysis","origin":{"latitude":31.2304,'
    '"longitude":121.4737},"destination":{"latitude":31.2019,"longitude":121.606}}'
)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def fixed_now() -> datetime:
    raw = datetime.now(ZoneInfo(TZ)) + timedelta(days=1)
    local = raw.replace(hour=8, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def weather_summary(temperature: float, probability: float) -> dict:
    return {
        "forecast_start": iso(NOW - timedelta(hours=1)),
        "forecast_end": iso(NOW + timedelta(hours=4)),
        "temperature_c": temperature,
        "precipitation_probability_max": probability,
        "precipitation_probability_known": True,
        "rain_probability_threshold": 0.4,
        "rain_risk": probability >= 0.4,
        "coverage": "full",
        "locations": [
            {"role": "origin", "coverage": "full", "max_probability": probability},
            {"role": "destination", "coverage": "full", "max_probability": probability},
        ],
    }


def sources(weather_fetched: datetime) -> list[dict]:
    return [
        {
            "kind": "weather",
            "provider": "mock",
            "fetched_at": iso(weather_fetched),
            "valid_from": iso(weather_fetched - timedelta(hours=1)),
            "valid_to": iso(weather_fetched + timedelta(hours=4)),
            "freshness": "fresh",
            "attribution": {"text": "开发/测试天气数据", "url": "https://example.invalid/weather"},
            "request_depart_at": None,
            "target_arrival_at": None,
            "note": "mock 天气，不是真实预报。",
        },
        {
            "kind": "route",
            "provider": "mock",
            "fetched_at": iso(weather_fetched),
            "valid_from": None,
            "valid_to": None,
            "freshness": "fresh",
            "attribution": {"text": "开发/测试路线数据", "url": "https://example.invalid/route"},
            "request_depart_at": None,
            "target_arrival_at": None,
            "note": "mock 路线，不是真实路况。",
        },
    ]


def ai_analysis(*, available: bool = True, reason: str | None = None) -> dict:
    base = {
        "analysis_version": 1,
        "route_source": "server_provider" if available else "none",
        "used_duration_seconds": 1800 if available else None,
        "duration_source": "duration_seconds" if available else "none",
        "map_duration_seconds": 1800 if available else None,
        "base_buffer_seconds": 600 if available else None,
        "additional_buffer_seconds": 300 if available else None,
        "extra_buffer_seconds": 900 if available else None,
        "recommended_departure_at": iso(TARGET - timedelta(minutes=35)) if available else None,
        "basis": ["服务器路线 mock 约 30 分钟", "计划固定缓冲 10 分钟"] if available else [],
        "missing_information": [],
        "reason": reason,
    }
    if available:
        return {"status": "available", **base}
    return {"status": "unavailable", **base}


def advisory_payload(row: CommuteAdvisory) -> dict:
    plan_revision = row.plan_revision
    matches_current_plan = plan_revision == 2
    expired = row.expires_at <= NOW
    return {
        "id": row.advisory_id,
        "plan_id": row.plan_id,
        "plan_revision": row.plan_revision,
        "local_service_date": row.local_service_date.isoformat(),
        "generated_at": iso(row.generated_at),
        "expires_at": iso(row.expires_at),
        "quality": row.quality,
        "title": row.title,
        "summary": row.summary,
        "target_arrival_at": iso(row.target_arrival_at),
        "recommended_departure_at": iso(row.recommended_departure_at),
        "departure_status": row.departure_status,
        "eta_seconds": row.eta_seconds,
        "buffer_seconds": row.buffer_seconds,
        "weather_extra_buffer_seconds": row.weather_extra_buffer_seconds,
        "weather": row.weather_json,
        "sources": row.sources_json,
        "reason_codes": row.reason_codes_json,
        "explanation_source": row.explanation_source,
        "ai_analysis": row.ai_analysis_json,
        "matches_current_plan": matches_current_plan,
        "is_current_service_date": row.local_service_date == DAY,
        "is_expired": expired,
        "current_plan_revision": 2,
    }


async def seed(database: str, fixture_path: Path) -> None:
    engine, factory = create_engine_and_factory(f"sqlite+aiosqlite:///{database}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    day = DAY
    async with factory() as session:
        user_a = User(
            user_id=str(uuid4()),
            email=EMAIL_A,
            display_name="E2E 用户 A",
            password_verifier=hash_password(PASSWORD),
            status="active",
        )
        user_b = User(
            user_id=str(uuid4()),
            email=EMAIL_B,
            display_name="E2E 用户 B",
            password_verifier=hash_password(PASSWORD),
            status="active",
        )
        session.add_all([user_a, user_b])

        plans: dict[str, CommutePlan] = {}
        for key, name in [
            ("advice", "E2E 已有建议"),
            ("progress", "E2E 处理中"),
            ("unconfigured", "E2E 路线未配置"),
            ("create", "E2E 新分析"),
            ("unknown", "E2E 结果未知"),
            ("linked", "E2E 关联建议"),
            ("revision", "E2E 计划已修改"),
        ]:
            plan = CommutePlan(
                plan_id=str(uuid4()),
                user_id=user_a.user_id,
                name=name,
                enabled=True,
                timezone=TZ,
                weekdays_json=[1, 2, 3, 4, 5, 6, 7],
                arrival_local_time="09:00",
                check_local_time="07:30",
                transport_mode="driving",
                buffer_minutes=10,
                rain_probability_threshold=0.4,
                rain_extra_buffer_minutes=10,
                notify_enabled=True,
                origin_label="E2E 起点",
                origin_latitude=31.2304,
                origin_longitude=121.4737,
                destination_label="E2E 终点",
                destination_latitude=31.2019,
                destination_longitude=121.606,
                revision=2,
            )
            session.add(plan)
            plans[key] = plan
        await session.flush()

        instances: dict[str, CommuteInstance] = {}
        for key, plan in plans.items():
            instance = CommuteInstance(
                instance_id=str(uuid4()),
                user_id=user_a.user_id,
                plan_id=plan.plan_id,
                plan_revision=2,
                local_service_date=day,
                timezone=TZ,
                target_arrival_at=TARGET,
                check_window_start_at=CHECK_WINDOW,
                status="active",
            )
            session.add(instance)
            instances[key] = instance
        await session.flush()

        checks: dict[str, CommuteCheck] = {}

        def add_check(
            key: str,
            plan_key: str,
            *,
            status: str,
            phase: str | None,
            plan_revision: int = 2,
            advisory_id: str | None = None,
            error_code: str | None = None,
        ) -> CommuteCheck:
            created = NOW - timedelta(minutes=5)
            finished = NOW - timedelta(minutes=4) if status in {
                "succeeded", "degraded", "failed", "cancelled", "unknown"
            } else None
            check = CommuteCheck(
                check_id=str(uuid4()),
                user_id=user_a.user_id,
                plan_id=plans[plan_key].plan_id,
                instance_id=instances[plan_key].instance_id,
                plan_revision=plan_revision,
                trigger="manual",
                analysis_mode="full",
                phase=phase,
                phase_updated_at=NOW - timedelta(minutes=1),
                slot_index=None,
                idempotency_key=f"e2e:{key}:{uuid4()}",
                request_hash="e2e",
                status=status,
                attempt_count=0,
                next_attempt_at=None,
                lease_owner=None,
                lease_expires_at=None,
                finished_at=finished,
                advisory_id=advisory_id,
                advisory_created=bool(advisory_id),
                created_at=created,
                updated_at=NOW - timedelta(minutes=1),
            )
            session.add(check)
            checks[key] = check
            return check

        def add_weather(plan_key: str, check: CommuteCheck, temp: float, prob: float) -> None:
            session.add(
                CommuteWeatherSnapshot(
                    snapshot_id=str(uuid4()),
                    user_id=user_a.user_id,
                    check_id=check.check_id,
                    provider="mock",
                    fetched_at=NOW,
                    valid_from=NOW - timedelta(hours=1),
                    valid_to=NOW + timedelta(hours=4),
                    quality="fresh",
                    freshness="fresh",
                    summary_json=weather_summary(temp, prob),
                )
            )

        def add_advisory(
            key: str,
            plan_key: str,
            check: CommuteCheck,
            *,
            title: str,
            plan_revision: int = 2,
            generated: datetime,
            expires: datetime,
            weather_value: dict,
            ai: dict,
            quality: str = "fresh",
            reasons: list[dict] | None = None,
        ) -> CommuteAdvisory:
            row = CommuteAdvisory(
                advisory_id=str(uuid4()),
                user_id=user_a.user_id,
                plan_id=plans[plan_key].plan_id,
                check_id=check.check_id,
                plan_revision=plan_revision,
                local_service_date=day,
                generated_at=generated,
                expires_at=expires,
                quality=quality,
                title=title,
                summary=f"{title}：mock 路线约 30 分钟，建议按服务器时间出发。",
                target_arrival_at=TARGET,
                recommended_departure_at=TARGET - timedelta(minutes=35),
                departure_status="upcoming",
                eta_seconds=1800,
                buffer_seconds=600,
                weather_extra_buffer_seconds=600,
                weather_json=weather_value,
                sources_json=sources(NOW),
                reason_codes_json=reasons
                or [{"code": "ROUTE_SERVER_PROVIDER"}, {"code": "WEATHER_FRESH"}],
                explanation_source="template",
                ai_analysis_json=ai,
            )
            session.add(row)
            checks[key].advisory_id = row.advisory_id
            checks[key].advisory_created = True
            return row

        # Advice plan: current + expired + old-revision history.
        current_check = add_check("advice_current", "advice", status="succeeded", phase="done")
        add_weather("advice", current_check, 18.0, 0.6)
        current_advice = add_advisory(
            "advice_current",
            "advice",
            current_check,
            title="今天 09:00 的通勤建议",
            generated=NOW - timedelta(minutes=5),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(21.0, 0.2),
            ai=ai_analysis(),
        )

        expired_check = add_check("advice_expired", "advice", status="succeeded", phase="done")
        expired_advice = add_advisory(
            "advice_expired",
            "advice",
            expired_check,
            title="已经过期的旧建议",
            generated=NOW - timedelta(hours=3),
            expires=NOW - timedelta(hours=1),
            weather_value=weather_summary(20.0, 0.1),
            ai=ai_analysis(),
        )

        old_revision_check = add_check(
            "advice_old_revision", "advice", status="succeeded", phase="done", plan_revision=1
        )
        old_revision_advice = add_advisory(
            "advice_old_revision",
            "advice",
            old_revision_check,
            title="修改前的计划建议",
            plan_revision=1,
            generated=NOW - timedelta(hours=4),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(19.0, 0.3),
            ai=ai_analysis(),
        )

        legacy_check = add_check("advice_legacy", "advice", status="succeeded", phase="done")
        legacy_ai = ai_analysis()
        legacy_ai["base_buffer_seconds"] = None
        legacy_ai["additional_buffer_seconds"] = None
        legacy_ai["extra_buffer_seconds"] = 1200
        legacy_advice = add_advisory(
            "advice_legacy",
            "advice",
            legacy_check,
            title="缺少拆分字段的旧建议",
            generated=NOW - timedelta(hours=6),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(20.0, 0.2),
            ai=legacy_ai,
        )

        # Progress plan: a running check with a real server phase.
        running_check = add_check("progress", "progress", status="running", phase="fetching_weather")
        add_weather("progress", running_check, 17.0, 0.5)

        # Route-unconfigured plan.
        unconfigured_check = add_check(
            "unconfigured", "unconfigured", status="degraded", phase="done",
            error_code="provider_not_configured",
        )
        unconfigured_advice = add_advisory(
            "unconfigured",
            "unconfigured",
            unconfigured_check,
            title="路线未配置时的历史建议",
            generated=NOW - timedelta(hours=2),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(22.0, 0.2),
            ai=ai_analysis(available=False, reason="route_provider_unconfigured"),
            quality="degraded",
            reasons=[{"code": "ROUTE_UNAVAILABLE"}],
        )

        # Unknown-result plan.
        unknown_check = add_check("unknown", "unknown", status="unknown", phase=None)

        # Linked-result plan: latest is newer than the check's own advisory.
        linked_check = add_check("linked", "linked", status="succeeded", phase="done")
        linked_advice = add_advisory(
            "linked",
            "linked",
            linked_check,
            title="本次检查关联建议",
            generated=NOW - timedelta(minutes=8),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(21.0, 0.2),
            ai=ai_analysis(),
        )
        linked_latest_check = add_check(
            "linked_latest", "linked", status="succeeded", phase="done"
        )
        add_advisory(
            "linked_latest",
            "linked",
            linked_latest_check,
            title="另一条最新建议",
            generated=NOW - timedelta(minutes=2),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(22.0, 0.3),
            ai=ai_analysis(),
        )

        # A plan whose pending local operation belongs to a previous revision.
        revision_check = add_check(
            "revision", "revision", status="succeeded", phase="done", plan_revision=1
        )
        revision_advice = add_advisory(
            "revision",
            "revision",
            revision_check,
            title="计划修改前的建议",
            plan_revision=1,
            generated=NOW - timedelta(hours=5),
            expires=NOW + timedelta(hours=2),
            weather_value=weather_summary(20.0, 0.2),
            ai=ai_analysis(),
        )

        # A create-once plan with no pending local record.
        plans["create"].revision = 2

        # Chat history: 21 chats so the oldest falls outside /asks?limit=20.
        chat_runs: list[NexusRun] = []
        for index in range(21):
            created = NOW - timedelta(hours=22 - index)
            question = (
                "很久以前的问题（不在最近二十条）"
                if index == 0
                else f"E2E 聊天问题 {index:02d}"
            )
            run = NexusRun(
                local_run_id=str(uuid4()),
                user_id=user_a.user_id,
                status="succeeded",
                idempotency_key=f"e2e-chat-{index}-{uuid4()}",
                last_event_sequence=2,
                request_text=question,
                result_json={
                    "workflow_status": "succeeded",
                    "final_text": f"这是对“{question}”的服务器回答。",
                },
                error_code=None,
                completed_at=created + timedelta(minutes=1),
                purpose="chat",
                business_ref=None,
                input_version=1,
                trigger="chat",
                created_at=created,
                updated_at=created + timedelta(minutes=1),
            )
            session.add(run)
            session.add(
                UserAsk(
                    ask_id=str(uuid4()),
                    user_id=user_a.user_id,
                    local_run_id=run.local_run_id,
                    request_text=question,
                    created_at=created,
                    updated_at=created,
                )
            )
            chat_runs.append(run)

        long_question = "长文本问题：" + "这是一段很长的提问内容，" * 80
        long_created = NOW - timedelta(minutes=10)
        long_run = NexusRun(
            local_run_id=str(uuid4()),
            user_id=user_a.user_id,
            status="succeeded",
            idempotency_key=f"e2e-chat-long-{uuid4()}",
            last_event_sequence=1,
            request_text=long_question,
            result_json={
                "workflow_status": "succeeded",
                "final_text": "长回答：" + "服务器返回的长文本不应撑破手机页面。" * 120,
            },
            completed_at=long_created + timedelta(minutes=1),
            purpose="chat",
            input_version=1,
            trigger="chat",
            created_at=long_created,
            updated_at=long_created + timedelta(minutes=1),
        )
        session.add(long_run)
        session.add(
            UserAsk(
                ask_id=str(uuid4()),
                user_id=user_a.user_id,
                local_run_id=long_run.local_run_id,
                request_text=long_question,
                created_at=long_created,
                updated_at=long_created,
            )
        )

        failed_created = NOW - timedelta(minutes=5)
        failed_run = NexusRun(
            local_run_id=str(uuid4()),
            user_id=user_a.user_id,
            status="failed",
            idempotency_key=f"e2e-chat-failed-{uuid4()}",
            last_event_sequence=0,
            request_text="失败的聊天问题",
            result_json=None,
            error_code="nexus_unavailable",
            completed_at=failed_created + timedelta(minutes=1),
            purpose="chat",
            input_version=1,
            trigger="chat",
            created_at=failed_created,
            updated_at=failed_created + timedelta(minutes=1),
        )
        session.add(failed_run)
        session.add(
            UserAsk(
                ask_id=str(uuid4()),
                user_id=user_a.user_id,
                local_run_id=failed_run.local_run_id,
                request_text="失败的聊天问题",
                created_at=failed_created,
                updated_at=failed_created,
            )
        )

        internal_run = NexusRun(
            local_run_id=str(uuid4()),
            user_id=user_a.user_id,
            status="failed",
            idempotency_key=f"commute-ai:e2e:{uuid4()}",
            last_event_sequence=0,
            request_text=PROMPT,
            result_json=None,
            error_code="nexus_unavailable",
            completed_at=NOW - timedelta(hours=1),
            purpose="commute_internal",
            business_ref=checks["advice_current"].check_id,
            input_version=1,
            trigger="manual",
            created_at=NOW - timedelta(hours=1),
            updated_at=NOW - timedelta(hours=1),
        )
        session.add(internal_run)

        user_b_run = NexusRun(
            local_run_id=str(uuid4()),
            user_id=user_b.user_id,
            status="succeeded",
            idempotency_key=f"e2e-chat-b-{uuid4()}",
            last_event_sequence=1,
            request_text="B 的专属问题",
            result_json={"workflow_status": "succeeded", "final_text": "B 的服务器回答。"},
            completed_at=NOW - timedelta(minutes=20),
            purpose="chat",
            input_version=1,
            trigger="chat",
            created_at=NOW - timedelta(minutes=20),
            updated_at=NOW - timedelta(minutes=20),
        )
        session.add(user_b_run)
        session.add(
            UserAsk(
                ask_id=str(uuid4()),
                user_id=user_b.user_id,
                local_run_id=user_b_run.local_run_id,
                request_text="B 的专属问题",
                created_at=NOW - timedelta(minutes=20),
                updated_at=NOW - timedelta(minutes=20),
            )
        )

        await session.commit()

        def check_fixture(check: CommuteCheck) -> dict:
            return {
                "check_id": check.check_id,
                "status": check.status,
                "phase": check.phase,
                "plan_revision": check.plan_revision,
                "created_at": iso(check.created_at),
                "finished_at": iso(check.finished_at),
            }

        fixture = {
            "database": str(database),
            "now": iso(NOW),
            "day": day.isoformat(),
            "email_a": EMAIL_A,
            "email_b": EMAIL_B,
            "password": PASSWORD,
            "user_a_id": user_a.user_id,
            "user_b_id": user_b.user_id,
            "plans": {key: plan.plan_id for key, plan in plans.items()},
            "checks": {key: check_fixture(check) for key, check in checks.items()},
            "runs": {
                "internal": internal_run.local_run_id,
                "long": long_run.local_run_id,
                "oldest_chat": chat_runs[0].local_run_id,
                "unknown": str(uuid4()),
            },
            "questions": {
                "oldest_chat": "很久以前的问题（不在最近二十条）",
                "long": long_question,
                "failed": "失败的聊天问题",
                "internal": PROMPT.splitlines()[0],
                "unknown": "未确认的本机问题",
                "unsent": "尚未发送的本机问题",
                "b": "B 的专属问题",
            },
            "advisories": {
                "current": advisory_payload(current_advice),
                "expired": advisory_payload(expired_advice),
                "old_revision": advisory_payload(old_revision_advice),
                "legacy": advisory_payload(legacy_advice),
                "revision": advisory_payload(revision_advice),
                "unconfigured": advisory_payload(unconfigured_advice),
                "linked": advisory_payload(linked_advice),
            },
            "titles": {
                "current": current_advice.title,
                "expired": expired_advice.title,
                "old_revision": old_revision_advice.title,
                "legacy": legacy_advice.title,
                "revision": revision_advice.title,
                "unconfigured": unconfigured_advice.title,
                "linked": linked_advice.title,
            },
        }
        fixture_path.write_text(json.dumps(fixture, ensure_ascii=False, indent=2))

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--fixture", required=True)
    args = parser.parse_args()
    NOW = fixed_now()
    DAY = local_today(NOW, TZ)
    TARGET = target_arrival_for(DAY, "09:00", TZ)
    CHECK_WINDOW = check_window_for(DAY, "07:30", TZ)
    asyncio.run(seed(args.database, Path(args.fixture)))
