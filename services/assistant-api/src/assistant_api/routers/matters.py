"""Matter, draft, today, reminder and estimate endpoints.

Route order matters: literal paths (`/matters/intake`, `/matters/drafts/...`) are
declared before parameterised ones. Every route scopes by the authenticated user.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import select

from assistant_api.commute.runtime import CommuteRuntime
from assistant_api.core.deps import (
    CommuteRuntimeDependency,
    CurrentUser,
    DatabaseSession,
    NexusClient,
    SettingsDependency,
)
from assistant_api.schemas import matters as schemas
from assistant_api.routers.places import require_body_parameters
from assistant_api.core.errors import InvalidRequestError, NotFoundError
from assistant_api.models import ScheduleEvent, NexusRun
from assistant_api.services import operation_intake, run_service
from assistant_api.services.intake_failures import public_failure
from assistant_api.services import commute_estimate, commute_places, matters, schedule_routes, commute_bundle

router = APIRouter(prefix="/api/v1", tags=["matters"])


class EmptyBody(BaseModel):
    pass


def _intake_response(
    *,
    result: dict,
    response: Response,
) -> dict:
    """Map a durable intake result to the existing public HTTP envelope."""
    outcome = result["outcome"]
    if outcome in ("answer", "needs_clarification"):
        response.status_code = 200
    elif outcome == "draft":
        response.status_code = 201
    elif outcome == "failed":
        response.status_code = 502
    elif outcome == "unknown":
        response.status_code = 504
    elif outcome == "processing":
        response.status_code = 202
    elif outcome == "cancelled":
        response.status_code = 200
    return {
        "source": result.get("source", "chat"),
        "matter_id": result.get("matter_id"),
        "follow_up_id": result.get("follow_up_id"),
        "follow_up_execution_id": result.get("follow_up_execution_id"),
        "error": result.get("error"),
        "status": result.get("status"),
        "workflow_execution_id": result.get("workflow_execution_id"),
        "draft": result.get("draft"),
        "replayed": bool(result.get("replayed")),
        "intent": result.get("intent", "matter"),
        "outcome": outcome,
        "message": (
            "已取得本次处理结果。"
            if outcome == "answer"
            else "还有一点信息需要你补充。"
            if outcome == "needs_clarification"
            else None
        ),
        "answer": result.get("answer"),
        "clarify": result.get("clarify"),
        "pending_context": result.get("pending_context") or {},
        "local_run_id": result["local_run_id"],
        "parent_run_id": result.get("parent_run_id"),
    }


@router.get("/matters/intake", response_model=list[schemas.IntakeResultRead])
async def list_intake_results(
    db: DatabaseSession,
    user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    """List the caller's unified input operations, including unfinished runs."""

    return await matters.list_intake_results(db, user, limit=limit)


@router.get("/matters/intake/{local_run_id}", response_model=schemas.IntakeResultRead)
async def get_intake_result(
    local_run_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Reload one answer, clarification, draft or unresolved processing state."""

    return await matters.get_intake_result(db, user, local_run_id)


@router.post("/matters/intake/stream")
async def intake_stream(request: Request, payload: schemas.IntakeRequest,
                        client: NexusClient, settings: SettingsDependency,
                        runtime: CommuteRuntimeDependency, user: CurrentUser):
    """Stream only public answer text while the local API planning flow runs."""
    import asyncio
    import json
    from fastapi.responses import StreamingResponse
    from assistant_api.services.intake_stream import sink, answer_prefix
    queue = asyncio.Queue()
    raw = ""
    previous = ""
    def observe(delta):
        """Project only public cumulative answer text from model stream fragments."""
        nonlocal raw, previous
        raw += delta
        text = answer_prefix(raw)
        if text and text != previous:
            previous = text
            queue.put_nowait(("answer", {"text": text}))
    async def execute():
        """Persist API planning and send its public result independently of browser disconnects."""
        token = sink.set(observe)
        try:
            async with request.app.state.session_factory() as db:
                result = await operation_intake.intake(request, db, user,
                    text=payload.text, idempotency_key=payload.idempotency_key,
                    continuation_run_id=payload.continuation_run_id,
                    memory_disclosures=[item.model_dump() for item in payload.memory_disclosures])
                if result["outcome"] in {"failed", "unknown", "cancelled"}:
                    queue.put_nowait(("error", {k: result.get(k) for k in ("error", "status", "outcome", "local_run_id", "workflow_execution_id")}))
                else:
                    queue.put_nowait(("result", _intake_response(result=result, response=Response())))
        except Exception as exc:
            failure = public_failure(None, exc)
            try:
                async with request.app.state.session_factory() as db:
                    row = await db.scalar(select(NexusRun).where(
                        NexusRun.user_id == user.user_id,
                        NexusRun.idempotency_key == f"matter-intake:{payload.idempotency_key}"))
                    if row is not None and row.workflow_execution_id and row.status in {"pending", "running", "unknown"}:
                        row = await run_service.refresh_run(db, client, user=user, row=row)
                    failure = public_failure(row, exc)
            except Exception:
                pass  # Preserve the structured unknown result if reconciliation also fails.
            queue.put_nowait(("error", failure))
        finally:
            sink.reset(token)
            queue.put_nowait(None)
    task = asyncio.create_task(execute())
    # Keep execution alive if the browser disconnects; idempotency prevents redispatch.
    active = getattr(request.app.state, "intake_stream_tasks", set())
    request.app.state.intake_stream_tasks = active
    active.add(task)
    task.add_done_callback(active.discard)
    async def events():
        """Send accepted, cumulative answers and the final persisted public result."""
        yield 'event: accepted\ndata: {}\n\n'
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), 10)
            except TimeoutError:
                yield ': heartbeat\n\n'
                continue
            if item is None:
                break
            kind, data = item
            yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/matters/intake", response_model=schemas.IntakeResponse, status_code=201)
async def intake(
    request: Request,
    payload: schemas.IntakeRequest,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    response: Response,
) -> dict:
    """Execute the user input through the generic API-description flow."""
    result = await operation_intake.intake(
        request, db, user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        continuation_run_id=payload.continuation_run_id,
        memory_disclosures=[item.model_dump() for item in payload.memory_disclosures],
    )
    return _intake_response(result=result, response=response)


@router.post(
    "/matters/{matter_id}/intake",
    response_model=schemas.IntakeResponse,
    status_code=201,
)
async def intake_for_matter(
    matter_id: str,
    request: Request,
    payload: schemas.IntakeRequest,
    db: DatabaseSession,
    client: NexusClient,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    response: Response,
) -> dict:
    """Continue the selected matter through the same generic API-description flow."""
    result = await operation_intake.intake(
        request, db, user,
        text=payload.text,
        idempotency_key=payload.idempotency_key,
        matter_id=matter_id,
        continuation_run_id=payload.continuation_run_id,
        memory_disclosures=[item.model_dump() for item in payload.memory_disclosures],
    )
    return _intake_response(result=result, response=response)


@router.post(
    "/matters/{matter_id}/drafts",
    response_model=schemas.DraftRead,
    status_code=201,
    openapi_extra={'x-assistant': {'effect': 'preview', 'keywords': ['修改日程', '修改待办', '补充原日程', '编辑原记录'], 'next': [{'method': 'PATCH', 'path': '/api/v1/matters/drafts/{draft_id}', 'purpose': '用返回的draft_id、version修改原字段。'}, {'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '修改完成后保存。'}]}},
    summary='读取原日程或待办并创建修改草稿',
    description='先GET事项详情，必须且只能提供schedule_id或todo_id之一。仅编辑原对象，不用于新建；保留返回payload里的原ID和其他字段，再PATCH草稿并调用confirm保存。设备导入日程只读。'
)
async def create_edit_draft(
    matter_id: str,
    payload: schemas.MatterEditRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    '先GET事项详情，必须且只能提供schedule_id或todo_id之一。仅编辑原对象，不用于新建；保留返回payload里的原ID和其他字段，再PATCH草稿并调用confirm保存。设备导入日程只读。'
    return await matters.create_edit_draft(
        db, runtime, settings, user, matter_id, payload
    )


@router.get("/matters/{matter_id}/changes", response_model=list[schemas.ChangeRead],
    summary='查询事项变更记录',
    description='仅查询当前用户事项的变更，撤回需使用可撤回记录的change_id和事项当前version。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_changes(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> list[dict]:
    '仅查询当前用户事项的变更，撤回需使用可撤回记录的change_id和事项当前version。'
    return await matters.list_changes(db, user, matter_id)


@router.post(
    "/matters/commute-drafts", response_model=schemas.DraftRead, status_code=201,
    operation_id="create_commute_draft", summary="创建完整通勤安排的组合草稿并查询真实起终点候选",
    description="新建通勤安排的入口：提供用户起终点名称、城市及到达时间，复用现有组合草稿。返回payload.commute.origin_search与destination_search候选；用用户已知条件唯一选择真实selection_token。缺少日期、星期或余量时询问，不能猜。继续commute-quote测算并选择交通方式，然后confirm一次事务保存事项、首日日程、周期计划、提醒及路线。这里只准备草稿，不新建正式日程。",
    openapi_extra={"x-assistant": {"effect": "preview", "next": [
        {"method": "POST", "path": "/api/v1/matters/drafts/{draft_id}/commute-quote", "purpose": "选择真实地点候选及明确周期字段后测算；普通地点confirm接口不用于组合草稿。"},
    ]}},
)
async def create_commute_draft(payload: commute_bundle.CreateDraftRequest,
                               db: DatabaseSession, settings: SettingsDependency,
                               runtime: CommuteRuntimeDependency, user: CurrentUser) -> dict:
    """Prepare an existing commute bundle with typed input and no additional AI intake call."""
    return await commute_bundle.create_input_draft(db, runtime, settings, user, payload)


@router.post(
    "/matters/drafts", response_model=schemas.DraftRead, status_code=201,
    operation_id="create_structured_draft",
    summary="新增待办、日程或事项的结构化草稿，向已有事项追加新记录",
    description="从零新建时提供title及todos或schedule，不需要先创建空事项；向已有事项追加时先读取matter_id及version，传expected_matter_version。这里只准备草稿，正式记录由后续confirm接口原子保存。新记录不能带原todo_id或schedule_id；修改原记录请走已有对象的编辑草稿入口。没有截止或提醒时间时省略，不猜时间。重复请求使用相同idempotency_key和参数。",
    openapi_extra={"x-assistant": {"effect": "preview", "keywords": ["新增待办", "创建待办", "添加日程", "新建日程", "追加待办", "新建事项"], "next": [
        {"method": "POST", "path": "/api/v1/matters/drafts/{draft_id}/confirm", "purpose": "参数明确时用返回的draft_id、version直接保存，无需用户再次确认。"},
    ]}},
)
async def create_structured_draft(payload: schemas.StructuredDraftRequest,
                                  db: DatabaseSession, settings: SettingsDependency,
                                  runtime: CommuteRuntimeDependency, user: CurrentUser) -> dict:
    """Adapt explicit typed creation parameters to the existing draft/save services."""
    return await matters.create_structured_draft(db, runtime, settings, user, payload)


@router.get("/matters/drafts", response_model=list[schemas.DraftRead],
    summary='查询已有事项草稿',
    description='只列出当前用户草稿，按matter_id或状态筛选；草稿尚未写入正式记录。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_drafts(
    db: DatabaseSession,
    user: CurrentUser,
    draft_status: str | None = Query(default=None, alias="status"),
    matter_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=50),
) -> list[dict]:
    '只列出当前用户草稿，按matter_id或状态筛选；草稿尚未写入正式记录。'
    return await matters.list_drafts(
        db, user, status=draft_status, matter_id=matter_id, limit=limit
    )


@router.get("/matters/drafts/{draft_id}", response_model=schemas.DraftRead,
    summary='读取草稿当前字段版本及冲突方案',
    description='取得当前version、payload及conflicts后再修改或保存；查询可能刷新冲突测算，不代表正式保存。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_draft(
    draft_id: str,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    '取得当前version、payload及conflicts后再修改或保存；查询可能刷新冲突测算，不代表正式保存。'
    return await matters.get_draft(db, runtime, settings, user, draft_id)


@router.post(
    "/matters/drafts/{draft_id}/select-matter", response_model=schemas.DraftRead,
    summary='为候选草稿选择原事项',
    description='仅适用于requires_choice的候选草稿，matter_id必须来自候选；需要草稿当前version。确定目标后继续保存，不用于新记录挂载。',
    openapi_extra={'x-assistant': {'effect': 'preview', 'next': [{'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '保存已选原事项的变更。'}]}}
)
async def select_matter(
    draft_id: str,
    payload: schemas.DraftSelectMatterRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    '仅适用于requires_choice的候选草稿，matter_id必须来自候选；需要草稿当前version。确定目标后继续保存，不用于新记录挂载。'
    return await matters.select_matter(
        db, runtime, settings, user, draft_id, payload
    )


@router.post(
    "/matters/drafts/{draft_id}/apply-option", response_model=schemas.DraftRead,
    summary='将已选冲突方案应用到草稿',
    description='需要草稿当前version及实际conflicts.options中的可执行option_id。方案可能影响相邻日程；信息不足询问具体方案，选择后仍需confirm保存。',
    openapi_extra={'x-assistant': {'effect': 'preview', 'next': [{'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '保存已选方案。'}]}}
)
async def apply_option(
    draft_id: str,
    payload: schemas.DraftOptionRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    '需要草稿当前version及实际conflicts.options中的可执行option_id。方案可能影响相邻日程；信息不足询问具体方案，选择后仍需confirm保存。'
    return await matters.apply_option(
        db, runtime, settings, user, draft_id, payload
    )


@router.patch("/matters/drafts/{draft_id}", response_model=schemas.DraftRead,
    summary='修改草稿字段，保留原日程或待办标识', description='需要草稿当前version。未提供字段保持原值；schedule是完整对象，修改时从原payload/schedule复制并覆盖指定字段；todos是本次拟变更列表，带todo_id更新原待办，不带ID新建。不能用空ID替换已确定的修改目标。schedule=null仅移除草稿中的日程提议，不删除原日程；cancel_schedule_ids只可选当前停止跟进预览列出的候选。修改后使用返回的新version继续confirm保存。',
    openapi_extra={'x-assistant': {'effect': 'preview', 'next': [{'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '使用最新version保存。'}]}})
async def patch_draft(
    draft_id: str,
    payload: schemas.DraftPatchRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    '需要草稿当前version。未提供字段保持原值；schedule是完整对象，修改时从原payload/schedule复制并覆盖指定字段；todos是本次拟变更列表，带todo_id更新原待办，不带ID新建。不能用空ID替换已确定的修改目标。schedule=null仅移除草稿中的日程提议，不删除原日程；cancel_schedule_ids只可选当前停止跟进预览列出的候选。修改后使用返回的新version继续confirm保存。'
    return await matters.patch_draft(db, runtime, settings, user, draft_id, payload)


@router.post(
    "/matters/drafts/{draft_id}/confirm",
    response_model=schemas.ConfirmResponse,
    summary='保存草稿中的事项日程待办和提醒',
    description='使用实际draft_id、当前version和Idempotency-Key，一次事务保存。用户输入明确即执行此保存步骤，不再请求用户确认。preview不是保存成功；applied=false时读取返回conflict再选择方案，不宣称成功。sync_to_ios仅用户要求同步时启用；create_schedule=false只是不保存本次日程提议。返回matter、schedule、reminders及change_id；需要待办完整结果再GET事项详情。',
    openapi_extra={'x-assistant': {'effect': 'write', 'next': [{'method': 'GET', 'path': '/api/v1/matters/{matter_id}', 'purpose': '保存后读取真实待办、日程和提醒用于结果卡片。'}]}}
)
async def confirm_draft(
    request: Request,
    client: NexusClient,
    draft_id: str,
    payload: schemas.ConfirmRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
) -> dict:
    '使用实际draft_id、当前version和Idempotency-Key，一次事务保存。用户输入明确即执行此保存步骤，不再请求用户确认。preview不是保存成功；applied=false时读取返回conflict再选择方案，不宣称成功。sync_to_ios仅用户要求同步时启用；create_schedule=false只是不保存本次日程提议。返回matter、schedule、reminders及change_id；需要待办完整结果再GET事项详情。'
    result = await matters.confirm_draft(
        db, runtime, settings, user, draft_id, payload, idempotency_key
    )
    if result["draft"]["payload"].get("commute"):
        from assistant_api.services import commute_memory
        draft = await matters._get_draft(db, user, draft_id)
        try:
            await commute_memory.start(db, client, settings, request.app.state.session_factory, user, draft)
        except Exception:
            await db.rollback()
            draft = await matters._get_draft(db, user, draft_id)
            draft.result_json = {**(draft.result_json or {}), "memory_error": "记忆提取启动失败；业务已保存。"}
            await db.commit()
    return result


@router.post("/matters/drafts/{draft_id}/commute-quote", response_model=schemas.DraftRead,
    summary='测算通勤组合草稿并选择交通方式',
    description='仅适用于payload含commute的组合草稿；origin_token及destination_token来自本草稿真实地点候选，token绑定账号和草稿。需要当前version、首日、星期、余量及交通方式；测算后返回新version，仍需confirm原子保存。不要用普通日程草稿调用。',
    openapi_extra={'x-assistant': {'effect': 'preview', 'next': [{'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '保存测算后的完整通勤草稿。'}]}}
)
async def quote_commute(draft_id: str, payload: commute_bundle.QuoteRequest, db: DatabaseSession, runtime: CommuteRuntimeDependency, settings: SettingsDependency, user: CurrentUser):
    '仅适用于payload含commute的组合草稿；origin_token及destination_token来自本草稿真实地点候选，token绑定账号和草稿。需要当前version、首日、星期、余量及交通方式；测算后返回新version，仍需confirm原子保存。不要用普通日程草稿调用。'
    return await commute_bundle.quote(db, runtime, settings, user, draft_id, payload)


@router.get("/matters/drafts/{draft_id}/memory-candidate")
async def memory_candidate(draft_id: str, db: DatabaseSession, client: NexusClient, user: CurrentUser):
    from assistant_api.services import commute_memory
    return await commute_memory.candidate(db, client, user, await matters._get_draft(db, user, draft_id))


class ConfirmMemoryBody(BaseModel):
    confirmed_sensitive: Literal[True]


@router.post("/matters/drafts/{draft_id}/memory-candidate/confirm")
async def confirm_memory_candidate(draft_id: str, payload: ConfirmMemoryBody, db: DatabaseSession, client: NexusClient, user: CurrentUser):
    from assistant_api.services import commute_memory
    return await commute_memory.save(db, client, user, await matters._get_draft(db, user, draft_id))


@router.post("/matters/drafts/{draft_id}/cancel", response_model=schemas.DraftRead,
    summary='取消尚未保存的草稿',
    description='取消preview不删除正式记录；已保存的变更需查询changes后用revert接口。',
    openapi_extra={'x-assistant': {'effect': 'preview'}}
)
async def cancel_draft(
    draft_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    '取消preview不删除正式记录；已保存的变更需查询changes后用revert接口。'
    return await matters.cancel_draft(db, user, draft_id)


@router.get("/matters", response_model=schemas.MatterListRead,
    summary='查询事项列表并取得原事项标识',
    description='按group分页，next_cursor用于继续查询。标题相同不代表同一条，目标含糊时查询详情或询问，不按列表位置猜选。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_matters(
    db: DatabaseSession,
    user: CurrentUser,
    group: schemas.MatterGroup = "all",
    limit: int = Query(default=20, ge=1, le=50),
    cursor: str | None = Query(default=None),
) -> dict:
    '按group分页，next_cursor用于继续查询。标题相同不代表同一条，目标含糊时查询详情或询问，不按列表位置猜选。'
    offset = 0
    if cursor:
        try:
            offset = max(0, int(cursor))
        except ValueError:
            offset = 0
    items, next_cursor = await matters.list_matters(
        db, user, group=group, limit=limit, offset=offset
    )
    return {"items": items, "next_cursor": next_cursor}


@router.post("/matters", response_model=schemas.MatterRead, status_code=201,
    summary='创建空事项容器',
    description='只有用户要一个空事项时直接调用。新增待办或日程应使用结构化草稿创建接口并confirm保存，避免只建空事项。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def create_matter(
    payload: schemas.MatterCreate, db: DatabaseSession, user: CurrentUser
) -> dict:
    '只有用户要一个空事项时直接调用。新增待办或日程应使用结构化草稿创建接口并confirm保存，避免只建空事项。'
    return await matters.create_matter(db, user, payload)


@router.get("/matters/{matter_id}", response_model=schemas.MatterDetailRead,
            summary='查询应用内事项详情及所属日程、待办和提醒',
    description='返回当前version及真实日程、待办、提醒和进展。commute_plan_id为该事项唯一有效通勤计划的实际ID，不能用matter_id代替plan_id。单独新增/修改待办日程使用对应直接保存接口；单日通勤修改先get_commute_day再更新并重测；组合记录使用结构化草稿保存。过期日期不改变记录身份。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    '返回当前version及真实日程、待办、提醒和进展。commute_plan_id为该事项唯一有效通勤计划的实际ID，不能用matter_id代替plan_id。单独新增/修改待办日程使用对应直接保存接口；单日通勤修改先get_commute_day再更新并重测；组合记录使用结构化草稿保存。过期日期不改变记录身份。'
    return await matters.get_matter_detail(db, user, matter_id)


@router.patch("/matters/{matter_id}", response_model=schemas.MatterRead,
    summary='修改事项标题目标状态或下一步',
    description='先读取当前version并传expected_version；只修改事项本身，不会同步改所属日程和待办标题。状态这里只支持active或waiting，归档用archive。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def update_matter(
    matter_id: str,
    payload: schemas.MatterUpdate,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    '先读取当前version并传expected_version；只修改事项本身，不会同步改所属日程和待办标题。状态这里只支持active或waiting，归档用archive。'
    return await matters.update_matter(db, user, matter_id, payload)


@router.post("/matters/{matter_id}/archive", response_model=schemas.MatterRead,
    summary='归档事项并按要求停止后续安排',
    description='需要当前expected_version。默认cancel_future_schedules=true会取消可取消的未来日程；保留日程应明确传false。cancel_reminders默认false，只有用户要求不用提醒才传true。不要将过期日程自动解释为已完成。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def archive_matter(
    matter_id: str,
    payload: schemas.MatterVersionRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    '需要当前expected_version。默认cancel_future_schedules=true会取消可取消的未来日程；保留日程应明确传false。cancel_reminders默认false，只有用户要求不用提醒才传true。不要将过期日程自动解释为已完成。'
    return await matters.set_status(
        db,
        user,
        matter_id,
        status="archived",
        expected_version=payload.expected_version,
        cancel_reminders=payload.cancel_reminders,
        cancel_future_schedules=payload.cancel_future_schedules,
    )


@router.post("/matters/{matter_id}/reopen", response_model=schemas.MatterRead,
    summary='恢复归档事项为正在推进',
    description='需要事项当前version，恢复事项状态，不恢复已取消的提醒或日程。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def reopen_matter(
    matter_id: str,
    payload: schemas.MatterVersionRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    '需要事项当前version，恢复事项状态，不恢复已取消的提醒或日程。'
    return await matters.set_status(
        db,
        user,
        matter_id,
        status="active",
        expected_version=payload.expected_version,
        cancel_reminders=False,
    )


@router.delete("/matters/{matter_id}", response_model=schemas.MatterDeleteRead,
    summary='删除事项及其应用内关联记录',
    description='软删除当前用户事项及关联待办、日程和提醒，不可把只删除一条日程的请求当作删除整个事项。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def delete_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    '软删除当前用户事项及关联待办、日程和提醒，不可把只删除一条日程的请求当作删除整个事项。'
    return await matters.delete_matter(db, user, matter_id)


@router.delete(
    "/matters/{matter_id}/initial",
    response_model=schemas.InitialDeleteResponse,
    summary='撤销尚未发生后续变更的初次创建',
    description='仅删除未发生后续编辑的初始创建及当时建立的记录；不适用于普通删除或任意历史撤回。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def initial_delete_matter(
    matter_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Delete one untouched initial create and only records created with it."""

    return await matters.initial_delete_matter(db, user, matter_id)


@router.post("/matters/{matter_id}/todos/{todo_id}/complete", response_model=schemas.TodoRead,
    summary='完成已有待办',
    description='matter_id和todo_id必须属于同一当前用户事项；cancel_reminders默认false，用户要求停止相关提醒时传true。完成待办不代表整件事项归档。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def complete_todo(
    matter_id: str,
    todo_id: str,
    db: DatabaseSession,
    user: CurrentUser,
    cancel_reminders: bool = Query(default=False),
) -> dict:
    'matter_id和todo_id必须属于同一当前用户事项；cancel_reminders默认false，用户要求停止相关提醒时传true。完成待办不代表整件事项归档。'
    return await matters.complete_todo(
        db, user, matter_id, todo_id, done=True, cancel_reminders=cancel_reminders
    )


@router.post("/matters/{matter_id}/todos/{todo_id}/reopen", response_model=schemas.TodoRead,
    summary='将已完成待办恢复为未完成',
    description='定位原matter_id及todo_id；更新同一条待办，不创建新待办。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def reopen_todo(
    matter_id: str, todo_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    '定位原matter_id及todo_id；更新同一条待办，不创建新待办。'
    return await matters.complete_todo(db, user, matter_id, todo_id, done=False)


@router.post(
    "/matters/{matter_id}/changes/{change_id}/revert",
    response_model=schemas.RevertResponse,
    summary='撤回一条仍可撤回的事项变更',
    description='先查询changes确认revertable和change_id，传当前事项expected_version；后续依赖变化可能拒绝撤回，不能直接重新创建旧记录代替。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def revert_change(
    matter_id: str,
    change_id: str,
    payload: schemas.RevertRequest,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    '先查询changes确认revertable和change_id，传当前事项expected_version；后续依赖变化可能拒绝撤回，不能直接重新创建旧记录代替。'
    return await matters.revert_change(
        db, user, matter_id, change_id, expected_version=payload.expected_version
    )


@router.get("/today", response_model=schemas.TodayRead,
    summary='查询指定日期的日程截止待办和提醒',
    description='按day与timezone查询真实记录；未确认的到点提醒可能仍显示。结果不是自动完成证明。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def today(
    db: DatabaseSession,
    settings: SettingsDependency,
    user: CurrentUser,
    day: date | None = Query(default=None, alias="date"),
) -> dict:
    '按day与timezone查询真实记录；未确认的到点提醒可能仍显示。结果不是自动完成证明。'
    timezone = await matters.user_timezone(db, user, settings.matter_default_timezone)
    from zoneinfo import ZoneInfo

    target = day or datetime.now(ZoneInfo(timezone)).date()
    return await matters.today_view(db, user, day=target, timezone=timezone)


@router.get("/reminders", response_model=list[schemas.ReminderRead],
    summary='查询提醒及其当前状态',
    description='按现有筛选参数读取原提醒；不要以页面未列出判断已经删除。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_reminders(
    db: DatabaseSession,
    user: CurrentUser,
    starts_at: datetime | None = Query(default=None, alias="from"),
    ends_at: datetime | None = Query(default=None, alias="to"),
    reminder_status: str | None = Query(default=None, alias="status"),
) -> list[dict]:
    '按现有筛选参数读取原提醒；不要以页面未列出判断已经删除。'
    return await matters.list_reminders(
        db, user, starts_at=starts_at, ends_at=ends_at, status=reminder_status
    )


@router.post(
    "/reminders/{reminder_id}/acknowledge",
    response_model=schemas.ReminderRead,
    summary='标记原提醒已知晓',
    description='仅标记提醒已知晓，不等同于完成关联待办或取消事项。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def acknowledge_reminder(
    reminder_id: str, db: DatabaseSession, user: CurrentUser
) -> dict:
    """Mark a due reminder as seen; repeated calls return the same state."""

    return await matters.acknowledge_reminder(db, user, reminder_id)


@router.post("/commute/places/search", dependencies=[Depends(require_body_parameters)], response_model=schemas.PlaceSearchResponse,
    summary='查询草稿地点候选及坐标',
    description='需要属于当前用户的draft_id；查询名称或地址，结果含真实selection_token，只能绑定到该草稿。多个候选时用用户已知条件唯一选择或询问。不会修改正式日程。',
    openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'POST', 'path': '/api/v1/commute/places/confirm', 'purpose': '为普通日程草稿应用唯一确定的地点。'}]}}
)
async def commute_place_search(
    payload: schemas.PlaceSearchRequest,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    settings: SettingsDependency,
    user: CurrentUser,
) -> dict:
    """Search places or addresses; multiple results remain explicit candidates."""

    await matters.validate_place_draft(db, user, payload.draft_id)
    return await commute_places.search_places(
        runtime,
        secret=settings.tool_callback_token.get_secret_value(),
        user_id=user.user_id,
        draft_id=payload.draft_id,
        query=payload.query,
        city=payload.city,
        search_type=payload.search_type,
        limit=payload.limit,
    )


@router.post("/commute/places/map-pick", dependencies=[Depends(require_body_parameters)], response_model=schemas.MapPickResponse,
    summary='按用户选择坐标取得地点候选',
    description='需要draft_id及用户已选坐标，不能猜坐标；只返回候选及selection_token，不修改正式日程。',
    openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'POST', 'path': '/api/v1/commute/places/confirm', 'purpose': '应用已选地点。'}]}}
)
async def commute_place_map_pick(
    payload: schemas.MapPickRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """Reverse geocode one explicit GCJ-02 point for the current draft."""

    return await matters.create_map_pick(
        db,
        runtime,
        settings,
        user,
        draft_id=payload.draft_id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        coordinate_system=payload.coordinate_system,
    )


@router.post("/commute/places/confirm", dependencies=[Depends(require_body_parameters)], response_model=schemas.DraftRead,
    summary='将地点候选写入草稿',
    description='需要真实draft_id与绑定该账号草稿的selection_token，均放JSON body；不接收expected_version。只更新草稿地点，随后confirm保存正式日程。',
    openapi_extra={'x-assistant': {'effect': 'preview', 'next': [{'method': 'POST', 'path': '/api/v1/matters/drafts/{draft_id}/confirm', 'purpose': '保存草稿。'}]}}
)
async def commute_place_confirm(
    payload: schemas.PlaceSelectionRequest,
    db: DatabaseSession,
    settings: SettingsDependency,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """Apply a signed candidate to a preview and recalculate conflicts."""

    return await matters.apply_place_selection(
        db,
        runtime,
        settings,
        user,
        draft_id=payload.draft_id,
        selection_token=payload.selection_token,
        secret=settings.tool_callback_token.get_secret_value(),
    )


@router.post("/commute/estimate", response_model=schemas.EstimateResponse,
             openapi_extra={'x-assistant': {'effect': 'read'}},
    summary='测算已有出发点到目的地的路线',
    description='origin与origin_schedule_id必须且只能一个；depart_at或arrive_by至少一个。previous_schedule需要origin_schedule_id。出发点未知则询问，不猜地址、坐标或耗时；测算不会创建日程。'
)
async def commute_estimate_endpoint(
    payload: schemas.EstimateRequest,
    db: DatabaseSession,
    runtime: CommuteRuntimeDependency,
    user: CurrentUser,
) -> dict:
    """POST is this implementation's choice (the plan did not specify a method)."""

    origin = payload.origin
    origin_basis = payload.origin_basis
    origin_schedule_fingerprint = None
    if payload.origin_schedule_id:
        previous = await db.scalar(
            select(ScheduleEvent).where(
                ScheduleEvent.schedule_id == payload.origin_schedule_id,
                ScheduleEvent.user_id == user.user_id,
                ScheduleEvent.deleted_at.is_(None),
            )
        )
        if previous is None:
            raise NotFoundError("schedule_not_found", "起点日程不存在。")
        if previous.latitude is None or previous.longitude is None:
            raise InvalidRequestError(
                "origin_coordinates_missing", "上一条日程没有可用坐标，请补充地点。"
            )
        origin = schemas.Coordinate(
            latitude=previous.latitude,
            longitude=previous.longitude,
            coordinate_system=previous.coordinate_system or "unknown",
            city=previous.location_city,
        )
        origin_basis = "previous_schedule"
        origin_schedule_fingerprint = schedule_routes.schedule_fingerprint(previous)
    assert origin is not None

    result = await commute_estimate.estimate(
        runtime,
        origin=(origin.latitude, origin.longitude),
        destination=(payload.destination.latitude, payload.destination.longitude),
        mode=payload.mode,
        depart_at=payload.depart_at,
        arrive_by=payload.arrive_by,
        origin_city=origin.city,
        destination_city=payload.destination.city,
        origin_coordinate_system=origin.coordinate_system,
        destination_coordinate_system=payload.destination.coordinate_system,
        origin_basis=origin_basis,
        origin_schedule_id=payload.origin_schedule_id,
        schedule_id=payload.schedule_id,
    )
    if payload.schedule_id and result["status"] == "available":
        if origin_schedule_fingerprint is not None:
            result["origin_schedule_fingerprint"] = origin_schedule_fingerprint
        stored = await schedule_routes.save_for_schedule(
            db,
            user,
            schedule_id=payload.schedule_id,
            result=result,
        )
        return {**result, **stored}
    return result


@router.get(
    "/commute/routes/{schedule_id}",
    response_model=schemas.ScheduledRouteRead,
    summary='读取日程已保存的路线',
    description='使用原schedule_id读取真实路线；不能将缺失路线当作0分钟或编造结果。普通用户卡片只展示取得的路线内容。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def commute_schedule_route(
    schedule_id: str,
    db: DatabaseSession,
    user: CurrentUser,
) -> dict:
    """Read one saved route without measuring again or mutating the schedule."""

    return await schedule_routes.detail_for_schedule(db, user, schedule_id)


__all__ = ["router"]
