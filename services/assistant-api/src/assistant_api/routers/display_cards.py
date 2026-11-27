"""Read owned progress and let the current Agent prepare its presentations."""
from fastapi import APIRouter, Request

from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.core.errors import ConflictError, NotFoundError
from assistant_api.models import NexusRun
from assistant_api.schemas.display_cards import MultiRouteMapInput, ProgressCardInput, TimelineRouteInput
from assistant_api.services import display_cards, matters

router = APIRouter(prefix="/api/v1", tags=["display-cards"])


@router.get("/matters/{matter_id}/progress-card", operation_id="get_matter_progress_card",
            summary="读取事项的最新进度卡和版本",
            description="没有主卡时返回version=0。先读取这项，再提出进度更新。",
            openapi_extra={"x-assistant": {"effect": "read"}})
async def get_matter_progress_card(matter_id: str, request: Request, db: DatabaseSession, user: CurrentUser) -> dict:
    """Return the owned current card, limiting model calls to text and identifiers."""
    await matters._get_matter(db, user, matter_id)
    card = await display_cards.current_progress(db, user.user_id, matter_id)
    if not card:
        return {"version": 0, "card": None}
    value = display_cards.published_card(card)
    if request.scope.get("assistant_execution_run_id"):
        payload = dict(value["payload"])
        embedded = payload.get("embedded_card")
        payload["embedded_card"] = {"card_id": embedded["card_id"]} if embedded else None
        value["payload"] = payload
    return {"version": card.version, "card": value}


@router.post("/matters/{matter_id}/progress-card/proposal", operation_id="propose_matter_progress_card",
             summary="为当前事项整理已完成、正在关注、新变化和待决定内容",
             description="只准备展示内容，不保存、不修改业务记录。expected_version取自进度卡查询。"
                         "当本次处理正常回答结束后，由服务端再次核对和保存。"
                         "需要展示已保存主卡时，终答cards含一个只填show_progress_card=true的对象。回答不要声称提案已保存。",
             openapi_extra={"x-assistant": {"effect": "read"}})
async def propose_matter_progress_card(matter_id: str, payload: ProgressCardInput, request: Request,
                                       db: DatabaseSession, user: CurrentUser) -> dict:
    """Validate a scoped observation without writing, including in evaluate mode."""
    run_id = request.scope.get("assistant_execution_run_id")
    run = await db.get(NexusRun, run_id) if run_id else None
    flow = ((run.context_json or {}).get("api_flow") or {}) if run else {}
    if not run or run.user_id != user.user_id or flow.get("matter_id") != matter_id:
        raise NotFoundError("card_matter_unavailable", "请在该事项中整理进展。")
    matter = await matters._get_matter(db, user, matter_id)
    if matter.status not in ("active", "waiting"):
        raise ConflictError("card_matter_unavailable", "该事项已结束。")
    current = await display_cards.current_progress(db, user.user_id, matter_id)
    if payload.expected_version != (current.version if current else 0):
        raise ConflictError("card_version_changed", "进度已更新，请重新读取后再整理。")
    if flow.get("trigger_source") == "follow_up" and payload.expected_version != flow.get("progress_start_version"):
        raise ConflictError("card_version_changed", "本次检查开始后已有更新，这次检查不会覆盖它。")
    await display_cards.progress_content(db, user.user_id, matter_id, payload)
    return {"matter_id": matter_id, "prepared": True, "applied": False}


@router.post("/display-cards/timeline-route", operation_id="save_timeline_route_card",
             summary="保存分段路线或按步骤排列的时间轴卡片",
             description="测算路线从通勤当天查询或刷新结果取route_ref_id和calculated_at，后者传route_calculated_at。"
                         "路线节点由服务端生成。工作步骤可提供nodes。不提供坐标和外部图片地址。"
                         "需展示时，在终答cards中填只含返回card_id的对象。",
             openapi_extra={"x-assistant": {"effect": "write"}})
async def save_timeline_route_card(payload: TimelineRouteInput, request: Request,
                                   db: DatabaseSession, user: CurrentUser) -> dict:
    """Store the time line and tell the model its reference without sending geometry."""
    run_id = request.scope.get("assistant_execution_run_id")
    run = await db.get(NexusRun, run_id) if run_id else None
    card = await display_cards.save_timeline(db, user, run, payload, request.headers.get("idempotency-key"))
    return {"card_id": card.card_id, "title": card.content_json["title"], "version": card.version}


@router.post("/display-cards/multi-route-map", operation_id="save_multi_route_map_card",
             summary="按日期或分组保存多条真实路线，切换地图与地点顺序",
             description="每组从通勤当天结果或日程路线读取route_ref_id和calculated_at，"
                         "后者传route_calculated_at。也可给已有日程编号schedule_ids，此时只标记地点，不连出路线。"
                         "仅提供编号与分组名称。终答cards可引用保存后返回的card_id。",
             openapi_extra={"x-assistant": {"effect": "write"}})
async def save_multi_route_map_card(payload: MultiRouteMapInput, request: Request,
                                    db: DatabaseSession, user: CurrentUser) -> dict:
    """Persist owned route groups and return a compact reference to Nexus."""
    run_id = request.scope.get("assistant_execution_run_id")
    run = await db.get(NexusRun, run_id) if run_id else None
    card = await display_cards.save_multi_route_map(db, user, run, payload, request.headers.get("idempotency-key"))
    return {"card_id": card.card_id, "title": card.content_json["title"], "version": card.version}
