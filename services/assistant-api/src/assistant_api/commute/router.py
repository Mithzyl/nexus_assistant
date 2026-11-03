"""/api/v1 commute routes. Identity always comes from the session, never a body."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Header, Path, Query, Request, Response, status
from sqlalchemy import delete, select

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.commute.schemas import (
    CheckCreateRequest,
    AdvisoryActionInput,
    AdvisoryActionRead,
    AdvisoryRead,
    CheckCreated,
    CheckRead,
    CommutePlanInput,
    CommutePlanRead,
    PlanWeatherRead,
    PushDeviceInput,
    PushDeviceRead,
    RuntimeRead,
)
from assistant_api.commute.heartbeat import runtime_snapshot
from assistant_api.commute.serializers import (
    advisory_read,
    check_read,
    plan_read,
    plan_weather_read,
)
from assistant_api.commute.services import actions as action_service
from assistant_api.commute.services import checks as check_service
from assistant_api.commute.services import plans as plan_service
from assistant_api.commute.services.scheduling import latest_advisory
from assistant_api.commute.timeutil import ensure_aware, local_today
from assistant_api.core.deps import (
    CommuteRuntimeDependency,
    CurrentUser,
    DatabaseSession,
    SettingsDependency,
    bearer_token,
)
from assistant_api.core.errors import ConflictError, ForbiddenError, NotFoundError
from assistant_api.core.security import digest_token
from assistant_api.models import (
    CommuteAdvisory,
    CommuteCheck,
    CommuteInstance,
    CommutePlan,
    CommuteWeatherSnapshot,
    PushDevice,
    UserSession,
)

from assistant_api.schemas.matters import CommuteDayRead, CommuteDayState, CommutePlanUpdateRead
from assistant_api.commute.schemas import CommuteDayQuery, CommuteDayChange, CommuteDayRefresh

router = APIRouter(prefix="/api/v1", tags=["commute"])

IdempotencyKey = Header(alias="Idempotency-Key", min_length=8, max_length=128)
IfMatch = Header(default=None, alias="If-Match")


def _etag(revision: int) -> str:
    return f'"{revision}"'


@router.get("/commute/runtime", response_model=RuntimeRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '运行诊断由设置页面读取，不是业务操作。'}}
)
async def get_commute_runtime(
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
) -> RuntimeRead:
    now = ensure_aware(runtime.now())
    snapshot = await runtime_snapshot(
        db,
        user_id=user.user_id,
        now=now,
        stale_seconds=settings.commute_heartbeat_stale_seconds,
    )
    snapshot["server_time"] = now
    route_available = bool(getattr(runtime.eta, "available", False))
    snapshot["route_provider_available"] = route_available
    snapshot["auto_analysis_enabled"] = settings.commute_auto_ai_enabled
    snapshot["auto_analysis_mode"] = (
        "full"
        if settings.commute_auto_ai_enabled and route_available
        else "weather_only"
    )
    return RuntimeRead.model_validate(snapshot)


@router.get("/commute-plans", response_model=list[CommutePlanRead],
    summary='查询已保存通勤计划',
    description='返回每条计划的id（用于路径plan_id）、revision及周期配置。按用户已知名称唯一选定原计划；修改前继续GET该计划当前配置。',
    openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'GET', 'path': '/api/v1/commute-plans/{plan_id}', 'purpose': '用唯一匹配原计划的id读取当前完整配置与revision，随后修改同一计划。'}]}}
)
async def list_commute_plans(
    db: DatabaseSession, user: CurrentUser
) -> list[CommutePlanRead]:
    """List owned plans and publish the continuation for editing the same plan."""
    return [plan_read(plan) for plan in await plan_service.list_plans(db, user.user_id)]


@router.post(
    "/commute-plans",
    response_model=CommutePlanRead,
    status_code=status.HTTP_201_CREATED,
    summary='创建周期通勤计划',
    description='仅保存周期配置，不建立事项或首日日程。用户要求完整通勤安排时使用matters/commute-drafts→commute-quote→confirm。此接口需要真实坐标、时区、星期及到达检查时间；缺失信息询问。单独创建的配置不满足generate-day的matter_id/first_schedule_id前提。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def create_commute_plan(
    payload: CommutePlanInput,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
) -> CommutePlanRead:
    '仅保存周期配置，不建立事项或首日日程。用户要求完整通勤安排时使用matters/commute-drafts→commute-quote→confirm。此接口需要真实坐标、时区、星期及到达检查时间；缺失信息询问。单独创建的配置不满足generate-day的matter_id/first_schedule_id前提。'
    plan = await plan_service.create_plan(db, user.user_id, payload)
    response.headers["ETag"] = _etag(plan.revision)
    return plan_read(plan)


@router.get("/commute-plans/{plan_id}", response_model=CommutePlanRead,
    summary='读取通勤计划当前配置和修订号',
    description='返回完整配置与revision；更新用If-Match头携带该revision的字符串值，不猜修订号。',
    openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'PUT', 'path': '/api/v1/commute-plans/{plan_id}', 'purpose': '长期修改时复制原配置的请求字段并携带实际revision。'}, {'method':'POST','path':'/api/v1/commute-plans/{plan_id}/days/query','purpose':'只改某一天或更新明天通勤时，先核对该日状态，不用普通日程PATCH。'}]}}
)
async def get_commute_plan(
    plan_id: str,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
) -> CommutePlanRead:
    '返回完整配置与revision；更新用If-Match头携带该revision的字符串值，不猜修订号。'
    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    response.headers["ETag"] = _etag(plan.revision)
    return plan_read(plan)


@router.put("/commute-plans/{plan_id}", response_model=CommutePlanUpdateRead,
    summary='完整替换原通勤计划配置',
    description='长期修改：先GET原计划，复制请求Schema接受的原配置并覆盖用户指定字段；If-Match为实际revision。保持plan_id，对已关联事项和首日日程的完整通勤安排，保存配置后立即重测下一次有效未来通勤，next_day返回真实结果；当天原时间已过时不修改历史，选择后续有效日期。明确单日修改请用days接口。next_day.applied=false时仅周期配置已保存，不声称已得到新出发时间。未来自动在前晚22点及当天早晨更新。',
    openapi_extra={'x-assistant': {'effect': 'write', 'required_headers': ['If-Match']}}
)
async def update_commute_plan(
    plan_id: str,
    payload: CommutePlanInput,
    response: Response,
    db: DatabaseSession,
    user: CurrentUser,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    if_match: str | None = IfMatch,
) -> CommutePlanUpdateRead:
    """Save recurring configuration and immediately remeasure its next eligible future day."""
    plan = await plan_service.update_plan(
        db,
        user_id=user.user_id,
        plan_id=plan_id,
        payload=payload,
        if_match=if_match,
    )
    response.headers["ETag"] = _etag(plan.revision)
    from assistant_api.services.commute_day_commands import remeasure_next_day
    next_day=await remeasure_next_day(db,runtime,settings,user,plan)
    return CommutePlanUpdateRead(**plan_read(plan).model_dump(),next_day=next_day)


@router.delete("/commute-plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT,
    summary='删除原通勤计划并停止后续处理',
    description='定位当前用户原plan_id；删除后取消关联未处理工作，不是只取消某一天。成功204没有JSON正文。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def delete_commute_plan(
    plan_id: str, db: DatabaseSession, user: CurrentUser
) -> Response:
    '定位当前用户原plan_id；删除后取消关联未处理工作，不是只取消某一天。成功204没有JSON正文。'
    await plan_service.delete_plan(db, user_id=user.user_id, plan_id=plan_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/commute-plans/{plan_id}/checks",
    response_model=CheckCreated,
    status_code=status.HTTP_202_ACCEPTED,
    summary='为已有通勤计划启动一次路线天气检查',
    description='需要真实plan_id及Idempotency-Key。可省略客户端route_snapshot；不要伪造地图快照。202只表示检查已入队，必须GET checks/{check_id}读取状态，完成后再读advisory。',
    openapi_extra={'x-assistant': {'effect': 'write', 'next': [{'method': 'GET', 'path': '/api/v1/checks/{check_id}', 'purpose': '查询检查实际状态。'}]}}
)
async def create_check(
    plan_id: str,
    response: Response,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = IdempotencyKey,
    payload: CheckCreateRequest | None = Body(default=None),
) -> CheckCreated:
    '需要真实plan_id及Idempotency-Key。可省略客户端route_snapshot；不要伪造地图快照。202只表示检查已入队，必须GET checks/{check_id}读取状态，完成后再读advisory。'
    created, replayed = await check_service.create_manual_check(
        db,
        runtime,
        user_id=user.user_id,
        plan_id=plan_id,
        idempotency_key=idempotency_key,
        request=payload,
    )
    response.headers["X-Idempotent-Replay"] = "true" if replayed else "false"
    return created


@router.get("/checks/{check_id}", response_model=CheckRead,
    summary='查询通勤检查是否完成',
    description='queued/running表示仍在处理；成功后用真实advisory_id读取建议，不把202入队当作最终结果。',
    openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'GET', 'path': '/api/v1/advisories/{advisory_id}', 'purpose': '检查成功且有advisory_id后读取建议。'}]}}
)
async def get_check(
    check_id: str, db: DatabaseSession, user: CurrentUser
) -> CheckRead:
    'queued/running表示仍在处理；成功后用真实advisory_id读取建议，不把202入队当作最终结果。'
    check = await check_service.get_check(db, user_id=user.user_id, check_id=check_id)
    return check_read(check)


@router.get("/commute-plans/{plan_id}/weather", response_model=PlanWeatherRead,
    summary='查询计划指定日期已保存天气',
    description='按local_service_date读取真实天气快照；404表示当日没有记录，不能借用其他日期。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_plan_weather(
    plan_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
    local_service_date: date | None = Query(default=None),
) -> PlanWeatherRead:
    """Return the latest stored weather for one plan and one service date.

    The authoritative link is `commute_weather_snapshots.check_id`; the check
    carries the plan revision and its instance carries the service date.
    `commute_checks.weather_snapshot_id` is never written and is not consulted.
    A missing snapshot is reported as such instead of borrowing another day or a
    different plan location.
    """

    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    now = ensure_aware(runtime.now())
    day = local_service_date or local_today(now, plan.timezone)
    row = (
        await db.execute(
            select(CommuteWeatherSnapshot, CommuteCheck)
            .join(CommuteCheck, CommuteCheck.check_id == CommuteWeatherSnapshot.check_id)
            .join(CommuteInstance, CommuteInstance.instance_id == CommuteCheck.instance_id)
            .where(
                CommuteWeatherSnapshot.user_id == user.user_id,
                CommuteCheck.plan_id == plan.plan_id,
                CommuteInstance.local_service_date == day,
                CommuteWeatherSnapshot.quality != "unavailable",
            )
            .order_by(
                CommuteWeatherSnapshot.fetched_at.desc(),
                CommuteWeatherSnapshot.created_at.desc(),
            )
            .limit(1)
        )
    ).first()
    if row is None:
        raise NotFoundError("NO_WEATHER", "该计划当天没有可用的天气记录。")
    snapshot, check = row
    return plan_weather_read(
        snapshot,
        check,
        plan,
        local_service_date=day,
        now=now,
        stale_after_seconds=settings.commute_weather_freshness_seconds,
    )


@router.get("/commute-plans/{plan_id}/advisories/latest", response_model=AdvisoryRead,
    summary='查询计划最新通勤建议',
    description='检查响应中的适用性和计划修订号，旧建议不代表当前计划的结果；读取真实建议内容。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_latest_advisory(
    plan_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> AdvisoryRead:
    """The most recently saved advisory, with read-time applicability flags.

    "Latest" keeps its existing meaning; it is not silently redefined as "the
    advisory that is currently valid". A client decides how to label a record
    whose plan revision, service date or expiry no longer matches.
    """

    plan = await plan_service.get_plan(db, user.user_id, plan_id)
    advisory = await latest_advisory(db, user_id=user.user_id, plan_id=plan_id)
    if advisory is None:
        raise NotFoundError("NO_ADVISORY", "暂无通勤建议。")
    return advisory_read(advisory, plan=plan, now=ensure_aware(runtime.now()))


@router.get("/advisories/{advisory_id}", response_model=AdvisoryRead,
    summary='读取一条通勤建议',
    description='使用真实advisory_id，按返回适用性展示内容；不执行任何确认动作。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_advisory(
    advisory_id: str,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> AdvisoryRead:
    '使用真实advisory_id，按返回适用性展示内容；不执行任何确认动作。'
    advisory = await db.scalar(
        select(CommuteAdvisory).where(
            CommuteAdvisory.advisory_id == advisory_id,
            CommuteAdvisory.user_id == user.user_id,
        )
    )
    if advisory is None:
        raise NotFoundError("advisory_not_found", "通勤建议不存在。")
    plan = await db.scalar(
        select(CommutePlan).where(
            CommutePlan.plan_id == advisory.plan_id,
            CommutePlan.user_id == user.user_id,
        )
    )
    return advisory_read(
        advisory, plan=plan, now=ensure_aware(runtime.now())
    )


@router.put("/push-devices/{installation_id}", response_model=PushDeviceRead,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '设备凭据由客户端注册，不交给AI。'}}
)
async def register_push_device(
    installation_id: Annotated[str, Path(min_length=8, max_length=128)],
    payload: PushDeviceInput,
    request: Request,
    db: DatabaseSession,
    settings: SettingsDependency,
    user: CurrentUser,
) -> PushDeviceRead:
    """Bind this installation's current APNs token to the session's user.

    The client never chooses topic or environment. A binding held by a *live*
    session of another account is not stolen; the App must log out first, which
    deletes that session's device rows.
    """

    now = datetime.now(UTC)
    token = bearer_token(request) or ""
    session = await db.scalar(
        select(UserSession).where(
            UserSession.token_digest == digest_token(token),
            UserSession.user_id == user.user_id,
            UserSession.revoked_at.is_(None),
        )
    )
    # Reject a second live account binding for the same install unless the old
    # session is already revoked/expired. This prevents notification crossover.
    others = list(
        (
            await db.scalars(
                select(PushDevice).where(
                    PushDevice.installation_id == installation_id,
                    PushDevice.user_id != user.user_id,
                )
            )
        ).all()
    )
    for other in others:
        other_session = (
            await db.scalar(
                select(UserSession).where(UserSession.session_id == other.session_id)
            )
            if other.session_id
            else None
        )
        live = (
            other_session is not None
            and other_session.revoked_at is None
            and ensure_aware(other_session.expires_at) > now
        )
        if live:
            raise ConflictError(
                "installation_bound_other_user",
                "该安装实例仍绑定其他账号，请先退出旧账号。",
            )
        await db.delete(other)

    device = await db.scalar(
        select(PushDevice).where(
            PushDevice.user_id == user.user_id,
            PushDevice.installation_id == installation_id,
        )
    )
    token_value = payload.apns_token.lower()
    topic = settings.commute_apns_topic or "com.example.assistant"
    if device is None:
        device = PushDevice(
            user_id=user.user_id,
            session_id=session.session_id if session else None,
            installation_id=installation_id,
            apns_token=token_value,
            notification_permission=payload.notification_permission,
            environment=settings.commute_apns_environment,
            topic=topic,
            token_updated_at=now,
            last_registered_at=now,
        )
        db.add(device)
    else:
        if device.apns_token != token_value:
            device.token_updated_at = now
        device.apns_token = token_value
        device.notification_permission = payload.notification_permission
        device.environment = settings.commute_apns_environment
        device.topic = topic
        device.session_id = session.session_id if session else device.session_id
        device.last_registered_at = now
    await db.commit()
    await db.refresh(device)
    return PushDeviceRead(
        installation_id=device.installation_id,
        notification_permission=device.notification_permission,
        environment=device.environment,
        updated_at=device.updated_at,
    )


@router.delete("/push-devices/{installation_id}", status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '设备注册由客户端管理。'}}
)
async def unregister_push_device(
    installation_id: Annotated[str, Path(min_length=8, max_length=128)],
    db: DatabaseSession,
    user: CurrentUser,
) -> Response:
    await db.execute(
        delete(PushDevice).where(
            PushDevice.user_id == user.user_id,
            PushDevice.installation_id == installation_id,
        )
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/advisories/{advisory_id}/actions", response_model=AdvisoryActionRead,
    summary='记录用户对通勤建议的已读或操作',
    description='根据真实advisory_id及用户明确要求记录action；不是修改通勤计划接口。',
    openapi_extra={'x-assistant': {'enabled': False, 'reason': '客户端设备事件需要真实installation_id，由客户端记录，不作为AI业务调用。'}}
)
async def advisory_action(
    advisory_id: str,
    payload: AdvisoryActionInput,
    response: Response,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = IdempotencyKey,
) -> AdvisoryActionRead:
    '根据真实advisory_id及用户明确要求记录action；不是修改通勤计划接口。'
    record, replayed = await action_service.record_action(
        db,
        user_id=user.user_id,
        advisory_id=advisory_id,
        payload=payload,
        idempotency_key=idempotency_key,
        now=ensure_aware(runtime.now()),
    )
    response.headers["X-Idempotent-Replay"] = "true" if replayed else "false"
    return AdvisoryActionRead(**{**record, "replayed": replayed})


@router.post("/commute-plans/{plan_id}/generate-day", response_model=CommuteDayRead,
    summary='为已有周期计划生成指定日程',
    description='需要已由组合草稿保存且仍启用的计划（真实matter_id和first_schedule_id），及明确service_date。日期不能早于今天或effective_from，必须在weekdays内且未错过到达时间；复用服务端当天生成逻辑，不另建普通日程副本。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def generate_commute_day(plan_id: str, db: DatabaseSession, runtime: CommuteRuntimeDependency, user: CurrentUser, service_date: date = Body(embed=True)):
    '需要已由组合草稿保存且仍启用的计划（真实matter_id和first_schedule_id），及明确service_date。日期不能早于今天或effective_from，必须在weekdays内且未错过到达时间；复用服务端当天生成逻辑，不另建普通日程副本。'
    from assistant_api.services.commute_lifecycle import generate_day
    return await generate_day(db,runtime,user,plan_id,service_date)


@router.post('/commute-plans/{plan_id}/days/query', operation_id='get_commute_day',
    response_model=CommuteDayState,
    summary='读取指定日期通勤有效时间、原日程和是否已过期',
    description='这是只读查询，POST仅用于接收JSON查询条件，不保存数据。service_date放body，路径只填plan_id。plan_id必须来自通勤计划列表的id或事项详情的commute_plan_id，不能使用matter_id代替。先读取本地日期对应的当天安排。status=past表示原到达时间已过；用户未明确指定新的未来时间时，ask_user询问日期及时间，不能自动改成明天或回写历史。upcoming可更新或刷新。version是事项版本，revision是计划版本。用户已明确给出新的未来时间可以改当天。',
    openapi_extra={'x-assistant':{'effect':'read','keywords':['修改通勤','通勤时间已过','明天通勤','通勤往后挪'], 'next':[
        {'method':'PATCH','path':'/api/v1/commute-plans/{plan_id}/days','purpose':'用户明确指定新到达时间后，一次完成重测和原日程更新，不修改长期配置。'},
        {'method':'POST','path':'/api/v1/commute-plans/{plan_id}/days/refresh','purpose':'现在立即获取该日期通勤测算，适用于前晚22点或当天更新。'}]}})
async def get_commute_day(plan_id:str,payload:CommuteDayQuery,db:DatabaseSession,runtime:CommuteRuntimeDependency,user:CurrentUser):
    """Read an owned day selected by JSON criteria without provider calls or business writes."""
    from assistant_api.services.commute_day_commands import read_day
    return await read_day(db,runtime,user,plan_id,payload.service_date)


@router.patch('/commute-plans/{plan_id}/days',operation_id='update_commute_day',response_model=CommuteDayState,
    summary='修改某一天通勤到达时间，立即重测并保存原日程',
    description='service_date与修改字段都放JSON body，路径只填plan_id。先get_commute_day取得version和revision，分别作为expected_matter_version、expected_plan_revision。只提交用户给出的arrival_local_time，后端计算车程、余量、出发及结束时间，原日程ID不变。一次调用已正式保存，不再确认。过去时间409，路线失败不修改正式记录；不把修改失败当新增。只有原来没有该日期日程时建立该日唯一实例。长期时间、其他日期和待办不变，后续检查使用当天覆盖时间。',
    openapi_extra={'x-assistant':{'effect':'write','keywords':['修改通勤','调整通勤到达时间','单日通勤']}})
async def update_commute_day(plan_id:str,payload:CommuteDayChange,db:DatabaseSession,runtime:CommuteRuntimeDependency,settings:SettingsDependency,user:CurrentUser):
    """Execute the shared measured day command for Web and AI using one formal save."""
    from assistant_api.services.commute_day_commands import change_day
    return await change_day(db,runtime,settings,user,plan_id,payload.service_date,payload)


@router.post('/commute-plans/{plan_id}/days/refresh',operation_id='refresh_commute_day',response_model=CommuteDayState,
    summary='立即获取指定日期通勤时间并保存，支持前晚22点更新',
    description='service_date与idempotency_key都放JSON body，路径只填plan_id。先读取get_commute_day；仅upcoming调用。重新请求路线并保存同一日程和路线，不更改到达目标、长期配置或待办。无需等当天早晨，前晚22点可测算明天；明确日期，不能默认为今天。返回真实出发时间。已有同一天安排保持ID，重复键复用结果；status=past时应询问用户或后台安静结束。',
    openapi_extra={'x-assistant':{'effect':'write','keywords':['明天通勤','前晚22点','更新通勤时间','获取通勤时间']}})
async def refresh_commute_day(plan_id:str,payload:CommuteDayRefresh,db:DatabaseSession,runtime:CommuteRuntimeDependency,settings:SettingsDependency,user:CurrentUser):
    """Remeasure and save an effective future day without changing its requested arrival."""
    from assistant_api.services.commute_day_commands import change_day
    return await change_day(db,runtime,settings,user,plan_id,payload.service_date,payload,refresh=True)
