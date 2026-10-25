"""Shared AI task service: chat/internal separation and Nexus-stub paths.

These tests exercise a *working* Nexus (the in-memory stub) for the commute
analysis path as well: creating the run, polling to a terminal state, and saving
`check.ai_run_id` at creation time. The previous suite only covered the
`nexus=None` unavailable branch.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from assistant_api.commute.services import ai as commute_ai
from assistant_api.commute.timeutil import ensure_aware
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    CommutePlan,
    NexusRun,
    UserAsk,
)
from assistant_api.nexus_client import NexusPilotClient
from assistant_api.services import ai_tasks

from conftest import auth
from tests.commute_flow import create_plan, execute_one_check


def ai_answer(target: datetime, *, duration: int = 1800, extra: int = 600) -> str:
    """Build a model answer that satisfies the commute arithmetic checks."""

    departure = ensure_aware(target).astimezone(UTC) - timedelta(
        seconds=duration + extra
    )
    return json.dumps(
        {
            "title": "按当前路况出发",
            "summary": "按当前路况，建议按时出发。",
            "used_duration_seconds": duration,
            "duration_source": "duration_seconds",
            "extra_buffer_seconds": extra,
            "recommended_departure_at": departure.isoformat().replace("+00:00", "Z"),
            "basis": ["地图 ETA"],
            "missing_information": [],
        },
        ensure_ascii=False,
    )


@pytest_asyncio.fixture
async def ai_harness(commute_harness):
    """Commute harness plus a Nexus client wired to the same in-memory fake."""

    client = NexusPilotClient(
        base_url="http://nexus.test",
        application_id="personal_assistant",
        application_token="test-application-token-32-characters",
        transport=httpx.MockTransport(commute_harness.fake.handler),
    )
    commute_harness.runtime.nexus = client
    try:
        yield commute_harness
    finally:
        await client.aclose()


async def manual_check(harness, plan_id: str, key: str) -> str:
    response = await harness.client.post(
        f"/api/v1/commute-plans/{plan_id}/checks",
        headers=auth(harness.token_a) | {"Idempotency-Key": key},
    )
    assert response.status_code == 202, response.text
    return response.json()["check_id"]


async def load_state(harness, check_id: str):
    """Return (check, plan, instance, linked run) from fresh sessions."""

    async with harness.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        assert check is not None
        plan = await db.get(CommutePlan, check.plan_id)
        instance = await db.get(CommuteInstance, check.instance_id)
        run = await db.get(NexusRun, check.ai_run_id) if check.ai_run_id else None
        assert plan is not None and instance is not None
        return check, plan, instance, run


def run_creates(harness) -> list[dict]:
    return [
        call
        for call in harness.fake.calls
        if call["method"] == "POST" and call["path"] == "/api/v1/app/runs"
    ]


@pytest.mark.asyncio
async def test_chat_question_creates_one_association_and_an_answer(ai_harness):
    h = ai_harness
    h.fake.final_text = "通勤提示词应只包含事实。"
    text = "怎样写通勤分析提示词"
    response = await h.client.post(
        "/api/v1/asks",
        json={"text": text, "idempotency_key": "chat-task-0001"},
        headers=auth(h.token_a),
    )
    assert response.status_code == 202, response.text
    local_run_id = response.json()["local_run_id"]

    async with h.app.state.session_factory() as db:
        run = await db.get(NexusRun, local_run_id)
        ask = await db.scalar(
            select(UserAsk).where(UserAsk.local_run_id == local_run_id)
        )
    assert run is not None and ask is not None
    assert run.purpose == ai_tasks.CHAT
    assert run.trigger == ai_tasks.TRIGGER_CHAT
    assert run.input_version == 1
    assert run.business_ref == ask.ask_id
    assert ask.user_id == h.user_a
    assert ask.request_text == text

    history = await h.client.get("/api/v1/asks", headers=auth(h.token_a))
    assert history.status_code == 200
    items = history.json()
    assert [item["local_run_id"] for item in items] == [local_run_id]
    assert items[0]["request_text"] == text

    detail = await h.client.get(
        f"/api/v1/asks/{local_run_id}", headers=auth(h.token_a)
    )
    assert detail.status_code == 200
    assert detail.json()["answer"] == h.fake.final_text

    replay = await h.client.post(
        "/api/v1/asks",
        json={"text": text, "idempotency_key": "chat-task-0001"},
        headers=auth(h.token_a),
    )
    assert replay.status_code == 202
    assert replay.json()["local_run_id"] == local_run_id
    assert replay.json()["replayed"] is True
    assert len(run_creates(h)) == 1


@pytest.mark.asyncio
async def test_commute_task_succeeds_but_never_enters_chat_history(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    check_id = await manual_check(h, plan["id"], "commute-task-0001")
    _check, _plan, instance, _ = await load_state(h, check_id)
    h.fake.final_text = ai_answer(instance.target_arrival_at)

    check = await execute_one_check(h)
    assert check is not None and check.check_id == check_id
    assert check.status in {"succeeded", "degraded"}

    check, plan_row, _instance, run = await load_state(h, check_id)
    assert run is not None, "the link must exist even before any result is read"
    assert check.ai_run_id == run.local_run_id
    assert run.purpose == ai_tasks.COMMUTE_INTERNAL
    assert run.trigger == ai_tasks.TRIGGER_MANUAL
    assert run.business_ref == check.check_id
    assert run.idempotency_key == f"commute:{check.check_id}:v1"
    assert run.nexus_run_id and run.workflow_execution_id

    async with h.app.state.session_factory() as db:
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
    assert advisory is not None
    assert advisory.ai_analysis_json["status"] == "available"
    assert advisory.ai_analysis_json["used_duration_seconds"] == 1800

    history = await h.client.get("/api/v1/asks", headers=auth(h.token_a))
    assert history.status_code == 200
    assert history.json() == []

    detail = await h.client.get(
        f"/api/v1/asks/{run.local_run_id}", headers=auth(h.token_a)
    )
    events = await h.client.get(
        f"/api/v1/asks/{run.local_run_id}/events", headers=auth(h.token_a)
    )
    cancel = await h.client.post(
        f"/api/v1/asks/{run.local_run_id}/cancel", headers=auth(h.token_a)
    )
    assert detail.status_code == 404
    assert events.status_code == 404
    assert cancel.status_code == 404
    assert not any(
        call["path"].endswith("/cancel") for call in h.fake.calls
    )

    verdict = await h.client.post(
        "/api/v1/asks/verify",
        json={"local_run_ids": [run.local_run_id]},
        headers=auth(h.token_a),
    )
    assert verdict.status_code == 200
    assert verdict.json()["results"] == [
        {"local_run_id": run.local_run_id, "classification": "not_chat"}
    ]


@pytest.mark.asyncio
async def test_failed_and_unknown_tasks_keep_the_check_link(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)

    h.fake.fail_run_create_status = 503
    failed_id = await manual_check(h, plan["id"], "commute-task-failed")
    await execute_one_check(h)
    check, _plan, _instance, run = await load_state(h, failed_id)
    assert check.ai_run_id is not None
    assert run is not None and run.status == "failed"
    assert run.error_code == "nexus_unavailable"
    assert run.purpose == ai_tasks.COMMUTE_INTERNAL
    async with h.app.state.session_factory() as db:
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
    assert advisory.ai_analysis_json["reason"] == "ai_upstream_error"

    h.fake.fail_run_create_status = None
    h.fake.raise_timeout_on_run_create = True
    unknown_id = await manual_check(h, plan["id"], "commute-task-unknown")
    await execute_one_check(h)
    check, _plan, _instance, run = await load_state(h, unknown_id)
    assert check.ai_run_id is not None
    assert run is not None and run.status == "unknown"
    assert run.error_code == "nexus_timeout"
    async with h.app.state.session_factory() as db:
        advisory = await db.get(CommuteAdvisory, check.advisory_id)
    assert advisory.ai_analysis_json["status"] == "unknown"
    assert advisory.ai_analysis_json["reason"] == "ai_upstream_timeout"


@pytest.mark.asyncio
async def test_retry_resumes_the_saved_run_without_a_second_upstream_create(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    check_id = await manual_check(h, plan["id"], "commute-task-retry")
    _check, _plan, instance, _ = await load_state(h, check_id)
    h.fake.final_text = ai_answer(instance.target_arrival_at)
    await execute_one_check(h)

    check, plan_row, instance, run = await load_state(h, check_id)
    first_run_id = run.local_run_id
    assert len(run_creates(h)) == 1

    # Simulate a restart that lost the local terminal projection but kept the
    # identifiers; a retry must reconcile the same run, never create a new one.
    async with h.app.state.session_factory() as db:
        stored = await db.get(NexusRun, first_run_id)
        stored.status = "running"
        stored.result_json = None
        stored.completed_at = None
        await db.commit()

    async with h.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        plan_row = await db.get(CommutePlan, check.plan_id)
        result = await commute_ai.analyze(
            db,
            h.runtime,
            check=check,
            plan=plan_row,
            snapshot=check.route_snapshot_json,
            weather={},
            target=instance.target_arrival_at,
        )
    assert result["status"] == "available"
    assert len(run_creates(h)) == 1
    async with h.app.state.session_factory() as db:
        stored = await db.get(NexusRun, first_run_id)
        assert check.ai_run_id == first_run_id
    assert stored is not None and stored.status == "succeeded"


@pytest.mark.asyncio
async def test_changed_input_does_not_submit_a_second_analysis(ai_harness):
    h = ai_harness
    plan = await create_plan(h, token := h.token_a)
    assert token
    check_id = await manual_check(h, plan["id"], "commute-task-input")
    _check, _plan, instance, _ = await load_state(h, check_id)
    h.fake.final_text = ai_answer(instance.target_arrival_at)
    await execute_one_check(h)
    assert len(run_creates(h)) == 1

    async with h.app.state.session_factory() as db:
        check = await db.get(CommuteCheck, check_id)
        plan_row = await db.get(CommutePlan, check.plan_id)
        original_run_id = check.ai_run_id
        changed_snapshot = dict(check.route_snapshot_json or {})
        changed_snapshot["duration_seconds"] = 2400
        changed_snapshot["queried_at"] = datetime.now(UTC).isoformat()
        result = await commute_ai.analyze(
            db,
            h.runtime,
            check=check,
            plan=plan_row,
            snapshot=changed_snapshot,
            weather={"rain_risk": True},
            target=instance.target_arrival_at,
        )

    assert result["status"] in {"available", "rejected"}
    assert len(run_creates(h)) == 1, "changed input must not create a second run"
    async with h.app.state.session_factory() as db:
        stored = await db.get(NexusRun, original_run_id)
        checks = (
            await db.scalars(
                select(NexusRun).where(
                    NexusRun.purpose == ai_tasks.COMMUTE_INTERNAL,
                    NexusRun.business_ref == check_id,
                )
            )
        ).all()
    assert stored is not None
    assert len(checks) == 1


@pytest.mark.asyncio
async def test_verify_and_history_are_account_scoped(ai_harness):
    h = ai_harness
    plan = await create_plan(h, h.token_a)
    check_id = await manual_check(h, plan["id"], "commute-task-scope")
    _check, _plan, instance, _ = await load_state(h, check_id)
    h.fake.final_text = ai_answer(instance.target_arrival_at)
    await execute_one_check(h)
    check, *_ = await load_state(h, check_id)
    internal_id = check.ai_run_id

    chat = await h.client.post(
        "/api/v1/asks",
        json={"text": "普通问题", "idempotency_key": "chat-task-scope"},
        headers=auth(h.token_a),
    )
    chat_id = chat.json()["local_run_id"]

    mine = await h.client.post(
        "/api/v1/asks/verify",
        json={"local_run_ids": [internal_id, chat_id, "does-not-exist"]},
        headers=auth(h.token_a),
    )
    assert mine.status_code == 200
    assert {item["local_run_id"]: item["classification"] for item in mine.json()["results"]} == {
        internal_id: "not_chat",
        chat_id: "chat",
        "does-not-exist": "unknown",
    }

    theirs = await h.client.post(
        "/api/v1/asks/verify",
        json={"local_run_ids": [internal_id, chat_id]},
        headers=auth(h.token_b),
    )
    assert theirs.status_code == 200
    assert all(
        item["classification"] == "unknown" for item in theirs.json()["results"]
    )
    other_history = await h.client.get("/api/v1/asks", headers=auth(h.token_b))
    assert other_history.json() == []
    other_detail = await h.client.get(
        f"/api/v1/asks/{chat_id}", headers=auth(h.token_b)
    )
    assert other_detail.status_code == 404

@pytest.mark.asyncio
async def test_failed_chat_still_counts_as_chat_for_verify(harness):
    """A question is a chat because it was asked, not because the model answered."""

    h = harness
    h.fake.fail_run_create_status = 503
    response = await h.client.post(
        "/api/v1/asks",
        json={"text": "失败的问题也算聊天", "idempotency_key": "chat-fail-0001"},
        headers=auth(h.token_a),
    )
    assert response.status_code == 503

    async with h.app.state.session_factory() as db:
        run = await db.scalar(
            select(NexusRun).where(NexusRun.idempotency_key == "chat-fail-0001")
        )
        ask = await db.scalar(
            select(UserAsk).where(UserAsk.local_run_id == run.local_run_id)
        )
    assert run is not None and run.status == "failed"
    assert run.error_code == "nexus_unavailable"
    assert ask is not None and ask.user_id == h.user_a
    assert ask.request_text == "失败的问题也算聊天"

    verify = await h.client.post(
        "/api/v1/asks/verify",
        json={"local_run_ids": [run.local_run_id]},
        headers=auth(h.token_a),
    )
    assert verify.status_code == 200
    assert verify.json()["results"] == [
        {"local_run_id": run.local_run_id, "classification": "chat"}
    ]

    history = await h.client.get("/api/v1/asks", headers=auth(h.token_a))
    assert any(
        item["local_run_id"] == run.local_run_id and item["status"] == "failed"
        for item in history.json()
    )
    detail = await h.client.get(
        f"/api/v1/asks/{run.local_run_id}", headers=auth(h.token_a)
    )
    assert detail.status_code == 200
    assert detail.json()["status"] == "failed"
    assert detail.json()["error_code"] == "nexus_unavailable"
    assert detail.json()["answer"] is None

    other = await h.client.post(
        "/api/v1/asks/verify",
        json={"local_run_ids": [run.local_run_id]},
        headers=auth(h.token_b),
    )
    assert other.json()["results"][0]["classification"] == "unknown"
    other_detail = await h.client.get(
        f"/api/v1/asks/{run.local_run_id}", headers=auth(h.token_b)
    )
    assert other_detail.status_code == 404
