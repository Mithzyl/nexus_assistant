"""Exercise selected route facts, immutable conversation cards and owned progress."""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from assistant_api.commute.providers.amap import AmapProvider
from assistant_api.models import CommutePlan, DisplayCard, FollowUpExecution, NexusRun
from assistant_api.services import bark_notifications, follow_ups, operation_intake
from assistant_api.services.api_operations import OperationCatalog
from conftest import auth, tool_auth
from tests.commute_flow import create_plan


def call(operation_id, body=None, **path_parameters):
    """Describe one fixed model tool request."""
    return {"decision": "call", "call": {"operation_id": operation_id, "body": body,
                                        "path_parameters": path_parameters}}


def answer(text="已整理。", cards=()):
    """Describe one fixed terminal model response."""
    return {"decision": "answer", "answer": text, "cards": list(cards)}


def resolve(*operations):
    """Ask for only the selected public operation definitions."""
    return call("resolve_operation_definitions", {"uris": [f"assistant://operations/{op}" for op in operations]})


def fixed_agent(h, steps):
    """Replay model decisions through the real caller_processed callback in one run."""
    replies = []
    finals = {}

    async def handle(request):
        """Validate proposed operations through the real App-created run."""
        if request.url.path.endswith("/result"):
            workflow = request.url.path.split("/")[-2]
            if workflow not in finals:
                remote = f"run_remote_{h.fake.run_seq}"
                for index, step in enumerate(steps):
                    decision = step(replies) if callable(step) else step
                    key = f"{workflow}-display-card-{index}"
                    response = await h.client.post("/internal/nexus/agent-responses", headers=tool_auth(h.settings), json={
                        "protocol_version": "1", "application_id": "personal_assistant",
                        "external_user_id": h.user_a, "run_id": remote, "workflow_id": workflow,
                        "response_id": key, "execution_id": key, "content": json.dumps(decision, ensure_ascii=False),
                    })
                    assert response.status_code == 200, response.text
                    replies.append(response.json())
                assert replies[-1]["disposition"] == "complete", replies
                finals[workflow] = json.dumps(decision, ensure_ascii=False)
            h.fake.final_text = finals[workflow]
        return await h.fake.handler(request)

    h.app.state.nexus_client._client._transport = httpx.MockTransport(handle)
    return replies


async def intake(h, text, key, matter_id=None):
    """Use the normal authenticated chat endpoint or a selected matter's endpoint."""
    path = f"/matters/{matter_id}/intake" if matter_id else "/matters/intake"
    response = await h.client.post("/api/v1" + path, headers=auth(h.token_a),
                                   json={"text": text, "idempotency_key": key})
    assert response.status_code == 200, response.text
    return response.json()


def amap_response(request):
    """Return one real loop, an alternative line, and two connected walking legs."""
    assert request.url.path == "/v3/direction/transit/integrated"
    return httpx.Response(200, json={"status": "1", "route": {"transits": [{
        "duration": "1980", "distance": "8400", "segments": [{
            "walking": {"distance": "616", "duration": "480", "steps": [{"polyline": "109.40,24.30;109.41,24.31"}]},
            "bus": {"buslines": [
                {"id": "loop-clockwise", "name": "4路（顺时针）", "duration": "1200", "distance": "7500",
                 "departure_stop": {"id": "north", "name": "中山路站"},
                 "arrival_stop": {"id": "south", "name": "中山路站"},
                 "polyline": "109.41,24.31;109.43,24.32;109.411,24.311"},
                {"id": "alternative", "name": "12路", "duration": "90",
                 "arrival_stop": {"name": "错误站"}, "polyline": "109.41,24.31;109.42,24.32"},
            ]},
        }, {
            "walking": {"distance": "284", "duration": "300", "steps": [{"polyline": "109.411,24.311;109.412,24.312"}]},
        }],
    }]}})


async def measured_commute(h):
    """Run the real day refresh with a mocked high-level AMap HTTP response."""

    provider = AmapProvider(api_key="isolated-maps", now=h.clock, transport=httpx.MockTransport(amap_response))
    h.runtime.eta = provider
    h.clock.set(datetime(2026, 10, 25, 22, 30, tzinfo=UTC))
    headers = auth(h.token_a)
    try:
        plan = await create_plan(h, h.token_a, weekdays=[1], arrival_local_time="08:00",
                                 check_local_time="06:30", transport_mode="transit")
        matter = await h.client.post("/api/v1/matters", headers=headers, json={"title": "上班通勤"})
        async with h.app.state.session_factory() as db:
            saved_plan = await db.get(CommutePlan, plan["id"])
            saved_plan.matter_id = matter.json()["matter_id"]
            saved_plan.origin_city = saved_plan.destination_city = "柳州"
            saved_plan.origin_latitude, saved_plan.origin_longitude = 24.30, 109.40
            saved_plan.destination_latitude, saved_plan.destination_longitude = 24.312, 109.412
            await db.commit()
        changed = await h.client.post(f"/api/v1/commute-plans/{plan['id']}/days/refresh", headers=headers,
                                      json={"service_date": "2026-10-26", "idempotency_key": "display-cards-commute"})
        assert changed.status_code == 200, changed.text
        return plan, changed.json()
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_sm_m2_commute_route_uses_one_candidate_and_saves_chat_snapshot(commute_harness):
    """UT-02/03/05 and SM-M2 use the real refresh and the Agent's one callback run."""
    h = commute_harness
    plan, day = await measured_commute(h)
    assert day["applied"] is True
    route = day["route"]
    assert route["transfer_count"] == 0
    walk, ride, final_walk = route["legs"]
    assert (walk["distance_meters"], walk["duration_seconds"], final_walk["duration_seconds"]) == (616, 480, 300)
    assert ride["line_id"] == "loop-clockwise"
    assert (ride["departure_stop_id"], ride["arrival_stop_id"], ride["duration_seconds"]) == ("north", "south", 1200)
    assert route["shape"]["segments"][1]["points"][-1]["longitude"] == 109.411
    assert day["timeline_card"]["blocks"][1]["items"][0]["title"] == "步行 8 分钟到中山路站"
    assert day["timeline_card"]["blocks"][1]["items"][1]["title"] == "乘 4路，在中山路站下车"

    publish = {"title": "上班路线", "route_ref_id": route["route_ref_id"],
               "route_calculated_at": route["calculated_at"]}
    responses = fixed_agent(h, [
        resolve("save_timeline_route_card"),
        call("save_timeline_route_card", {**publish, "polyline_points": [[999, 999]]}),
        call("save_timeline_route_card", publish),
        lambda replies: answer(cards=[{"card_id": replies[-1]["result"]["data"]["card_id"]}]),
    ])
    saved = await intake(h, "用时间轴展示刚查到的通勤", "save-route-card")
    assert responses[1]["execution_status"] == "failed", responses
    card = saved["pending_context"]["cards"][0]
    assert card["card_type"] == "timeline_route"
    assert "shape" not in responses[2]["result"]["data"]
    assert card["payload"]["shape"] == route["shape"]
    async with h.app.state.session_factory() as db:
        row = await db.get(DisplayCard, card["card_id"])
        assert row.source_run_id == saved["local_run_id"]
        run = await db.get(NexusRun, row.source_run_id)
        assert operation_intake.response_for(run)["pending_context"]["cards"] == [card]


@pytest.mark.asyncio
async def test_sm_m1_plain_chat_and_one_schedule_skip_card_definitions(harness):
    """The initial catalog is concise and both ordinary runs avoid card tool calls."""
    h = harness
    replies = fixed_agent(h, [answer("你好。")])
    chat = await intake(h, "你好", "display-card-plain-chat")
    assert chat["pending_context"]["cards"] == []
    assert [reply["disposition"] for reply in replies] == ["complete"]
    replies = fixed_agent(h, [
        call("create_schedule", {"idempotency_key": "schedule-fixed-model", "title": "会面",
                                "starts_at": "2030-03-24T08:00:00+08:00", "ends_at": "2030-03-24T08:30:00+08:00"}),
        answer("已安排。"),
    ])
    saved = await intake(h, "2030年3月24日早上8点安排会面", "display-card-one-schedule")
    assert saved["pending_context"]["cards"] == []
    assert replies[0]["execution_status"] == "succeeded", replies
    assert h.fake.run_seq == 2
    prompts = [entry["body"] for entry in h.fake.calls if entry["path"].endswith("/app/runs")]
    assert all("TimelineRouteInput" not in prompt and "ProgressCardInput" not in prompt for prompt in prompts)


@pytest.mark.asyncio
async def test_sm_m4_progress_has_single_versioned_card_and_opted_in_bark(harness):
    """Exercise manual and evaluate-only worker changes, stale updates and private navigation."""
    h = harness
    headers = auth(h.token_a)
    matter = await h.client.post("/api/v1/matters", headers=headers, json={"title": "搬家安排"})
    mid = matter.json()["matter_id"]
    progress_path = f"/api/v1/matters/{mid}/progress-card"
    fixed_agent(h, [
        resolve("save_timeline_route_card", "propose_matter_progress_card"),
        call("save_timeline_route_card", {"title": "搬家步骤", "nodes": [{"title": "整理书箱", "summary": "把书籍和资料分装并在箱外贴上房间名称，搬运时先放到书房，方便按类别拆箱。" * 3}]}),
        lambda replies: call("propose_matter_progress_card", {
            "expected_version": 0, "title": "搬家进展", "progress_state": "watching",
            "watching_items": ["等搬家公司确认"], "embedded_card_id": replies[-1]["result"]["data"]["card_id"],
        }, matter_id=mid),
        lambda replies: answer(cards=[{"card_id": replies[1]["result"]["data"]["card_id"]}, {"show_progress_card": True}]),
    ])
    original = await intake(h, "做搬家步骤卡并整理此事项进展", "manual-matter-progress", mid)
    first = (await h.client.get(progress_path, headers=headers)).json()
    assert first["version"] == 1, original
    assert original["pending_context"]["cards"][1] == first["card"]
    embedded_id = first["card"]["payload"]["embedded_card"]["card_id"]
    assert len(first["card"]["payload"]["embedded_card"]["blocks"][0]["items"][0]["description"]) == 60

    h.settings.bark_web_origin = "https://example.invalid"
    sent = []

    def fake_send(request):
        """Capture the actual Bark request without sending a notification."""
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"code": 200})

    h.app.state.bark_transport = httpx.MockTransport(fake_send)
    assert (await h.client.post("/api/v1/commute/bark/binding", headers=headers,
                                json={"device_key": "isolated-device"})).status_code == 201
    assert (await h.client.patch("/api/v1/commute/bark", headers=headers,
                                 json={"assistant_notifications_enabled": True})).status_code == 200
    now = datetime.now(UTC)
    rule = await h.client.post("/api/v1/follow-ups", headers=headers, json={
        "idempotency_key": "display-card-followup", "matter_id": mid, "title": "检查搬家",
        "instruction": "检查资料并更新搬家进展。", "execution_mode": "evaluate",
        "references": [{"name": "matter", "operation_id": "get_matter_api_v1_matters__matter_id__get",
                        "path_parameters": {"matter_id": mid}}],
        "trigger": {"kind": "time", "at": (now + timedelta(seconds=2)).isoformat()},
    })
    assert rule.status_code == 201, rule.text
    scheduled = now + timedelta(seconds=3)
    assert await follow_ups.scheduler_tick(h.app, now=scheduled) == 1
    replies = fixed_agent(h, [
        resolve("get_matter_progress_card", "propose_matter_progress_card"),
        call("get_matter_progress_card", matter_id=mid),
        call("propose_matter_progress_card", {"expected_version": 1, "title": "搬家进展",
            "progress_state": "awaiting_user", "completed_items": ["打包书籍"],
            "watching_items": ["等车辆确认"], "latest_changes": ["车队可在周五到场"],
            "next_decisions": ["选择上午或下午"], "notify_user": True, "embedded_card_id": embedded_id}, matter_id=mid),
        answer("车队周五可到场。"),
    ])
    assert await follow_ups.worker_tick(h.app, now=scheduled)
    newest = (await h.client.get(progress_path, headers=headers)).json()
    assert replies[2]["result"]["data"]["applied"] is False
    assert newest["version"] == 2 and newest["card"]["card_id"] == first["card"]["card_id"]
    assert newest["card"]["payload"]["embedded_card"]["card_id"] == embedded_id
    assert (await h.client.get(progress_path, headers=auth(h.token_b))).status_code == 404
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(DisplayCard).where(DisplayCard.progress_matter_id == mid)) == 1
        historical = operation_intake.response_for(await db.get(NexusRun, original["local_run_id"]))
        assert historical["pending_context"]["cards"] == original["pending_context"]["cards"]
        execution = await db.scalar(select(FollowUpExecution))
        assert execution.result_json["pending_context"]["progress_card_status"] == "saved"

    assert await bark_notifications.tick(h.app, now=scheduled) == 1
    assert sent[0]["url"] == f"https://example.invalid/matters/{mid}"
    assert await bark_notifications.tick(h.app, now=scheduled) == 0
    responses = fixed_agent(h, [
        resolve("propose_matter_progress_card"),
        call("propose_matter_progress_card", {"expected_version": 1, "title": "旧进展",
            "progress_state": "watching", "watching_items": ["旧数据"]}, matter_id=mid),
        answer("已有更新，请查看最新进度。"),
    ])
    await intake(h, "整理进展", "stale-matter-progress", mid)
    assert responses[1]["error"]["code"] == "card_version_changed"
    assert (await h.client.get(progress_path, headers=headers)).json() == newest


@pytest.mark.asyncio
async def test_evaluate_cannot_turn_progress_permission_into_business_write(harness):
    """Run a current evaluate callback with schedule creation already published."""
    from assistant_api.models import FollowUp

    h = harness
    headers = auth(h.token_a)
    matter = (await h.client.post("/api/v1/matters", headers=headers, json={"title": "仅查看进展"})).json()
    mid = matter["matter_id"]
    now = datetime.now(UTC)
    async with h.app.state.session_factory() as db:
        rule = FollowUp(user_id=h.user_a, matter_id=mid, title="观察", instruction="只检查。",
                        idempotency_key="observe-only-test", execution_mode="evaluate", state="enabled",
                        references_json=[], trigger_json={"kind": "time"}, stop_when_json=[], observed_json={})
        db.add(rule)
        await db.flush()
        execution = FollowUpExecution(user_id=h.user_a, follow_up_id=rule.follow_up_id,
            trigger_key="observe-test", rule_version=rule.version, state="running",
            lease_owner="test-observer", lease_expires_at=now + timedelta(minutes=10))
        db.add(execution)
        await db.flush()
        flow = {"text": "只观察", "now": now.isoformat(), "timezone": "Asia/Shanghai", "history": [],
                "receipts": [], "step": 0, "trigger_source": "chat", "matter_id": mid,
                "follow_up_execution_id": execution.execution_id, "follow_up_id": rule.follow_up_id,
                "lease_owner": "test-observer", "request_key": "followup:" + execution.execution_id}
        context = operation_intake.model_context(flow, OperationCatalog(h.app))
        flow["trigger_source"] = "follow_up"
        db.add(NexusRun(user_id=h.user_a, nexus_run_id="observer-test-run", status="running",
            idempotency_key="observe-only-agent", request_text="只观察",
            context_json={**context, "caller_processed": True, "api_flow": flow}))
        await db.commit()
    response = await h.client.post("/internal/nexus/agent-responses", headers=tool_auth(h.settings), json={
        "protocol_version": "1", "application_id": "personal_assistant", "external_user_id": h.user_a,
        "run_id": "observer-test-run", "workflow_id": "observer-workflow",
        "response_id": "observer-response", "execution_id": "observer-call",
        "content": json.dumps(call("create_schedule", {"idempotency_key": "no-write-observation",
            "title": "不得创建", "starts_at": "2030-03-24T08:00:00+08:00"})),
    })
    result = response.json()
    assert result["execution_status"] == "rejected", result
    assert result["error"]["code"] == "approval_required"
    assert (await h.client.get(f"/api/v1/matters/{mid}", headers=headers)).json()["schedules"] == []


@pytest.mark.asyncio
async def test_selected_bus_without_polyline_keeps_stops_and_does_not_draw_alternative():
    """Keep selected stop facts if the only drawing belongs to a different line."""
    def missing_selected_shape(request):
        """Remove only the selected line's geometry from the fixture."""
        raw = amap_response(request).json()
        raw["route"]["transits"][0]["segments"][0]["bus"]["buslines"][0].pop("polyline")
        return httpx.Response(200, json=raw)

    provider = AmapProvider(api_key="isolated-maps", transport=httpx.MockTransport(missing_selected_shape))
    date = datetime(2026, 10, 26, 0, tzinfo=UTC)
    try:
        route = await provider.fetch(origin=(24.30, 109.40), destination=(24.312, 109.412),
            mode="transit", request_depart_at=date, target_arrival_at=date + timedelta(hours=1),
            origin_city="柳州", destination_city="柳州")
    finally:
        await provider.aclose()
    assert route.route_shape_status == "partial"
    assert [segment.kind for segment in route.route_segments] == ["walking", "walking"]
    assert route.transit_legs[1].arrival_stop_id == "south"
    assert route.transit_legs[1].duration_seconds == 1200
    assert route.notification_details["transit_lines"] == ["4路"]
