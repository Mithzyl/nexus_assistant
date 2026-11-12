"""Own-account memory management and separate, narrowly scoped business tools."""
from typing import Annotated
from fastapi import APIRouter, Path, Query, Request

from assistant_api.core.deps import CurrentUser, DatabaseSession, MemoryStoreDependency, SettingsDependency
from assistant_api.schemas.memory import AppMemoryData, MemoryCommand, BusinessRecallRequest, BusinessFactRequest, AgentMemoryCommand
from assistant_api.services import memory_service, business_memory

router = APIRouter(prefix='/api/v1/memory', tags=['memory'])
business_router = APIRouter(prefix='/api/v1/business-memory', tags=['business-memory'])


@router.get('')
async def list_memory(db: DatabaseSession, user: CurrentUser, client: MemoryStoreDependency,
                      local_run_id: str | None = Query(default=None, min_length=36, max_length=36),
                      offset: int = Query(default=0, ge=0, le=10000),
                      limit: int = Query(default=20, ge=1, le=50)):
    """Manage owned current memory without requiring any prior conversation."""
    await memory_service.validate_optional_context(db, user.user_id, local_run_id)
    page = business_memory.memory_page(await client.list_application_memory(user.user_id, offset=offset, limit=limit))
    return {**page, 'items': [{**item, 'business': business_memory.business_projection(item)} for item in page['items']]}


@router.get('/settings')
async def memory_settings(db: DatabaseSession, user: CurrentUser, client: MemoryStoreDependency,
                          local_run_id: str | None = Query(default=None, min_length=36, max_length=36)):
    """Return this authenticated application's account settings, independent of Runs."""
    await memory_service.validate_optional_context(db, user.user_id, local_run_id)
    return await client.application_memory_settings(user.user_id)


@router.get('/history/{entry_id}')
async def memory_history(entry_id: Annotated[str, Path(min_length=36, max_length=36)], user: CurrentUser, client: MemoryStoreDependency,
                         offset: int = Query(default=0, ge=0, le=10000),
                         limit: int = Query(default=20, ge=1, le=50)):
    """Read immutable Assistant-owned revisions after local account authorization."""
    return await client.list_application_memory(user.user_id, history_entry_id=entry_id, offset=offset, limit=limit)


@router.post('/commands', openapi_extra={'x-assistant':{'enabled':False}})
async def memory_command(payload: MemoryCommand, request: Request, db: DatabaseSession, user: CurrentUser, client: MemoryStoreDependency):
    """Execute owner management locally; model callbacks cannot grant confirmation or change settings."""
    from assistant_api.core.errors import ForbiddenError
    # Direct management has user credentials; the AI uses the narrower business command.
    if request.scope.get('assistant_execution_run_id'):
        raise ForbiddenError('memory_owner_required','请使用受限的业务记忆接口。')
    await memory_service.validate_optional_context(db, user.user_id, payload.local_run_id)
    command = payload.model_dump(mode='json', exclude_none=True, exclude={'local_run_id'})
    return await client.mutate_application_memory(user.user_id, command)


@business_router.post('/query', operation_id='recall_business_memory',
    summary='检索任意领域的个人记忆原文和版本来源',
    description='只读本账号有效且允许使用的记忆，不按业务分类排除。purpose为开放任务说明，category/tags仅排序提示。query支持自然文字，queries可提供多种关键词表述；当前为字面和词片段检索，不是语义搜索。mode=browse按原文浏览，空query同样可浏览未分类记忆。无匹配不代表没有记忆；coverage为partial或有next_offset时继续分页，禁止宣称已查全。最多扫描200条、返回10条；扫描偏移限制会明确标记。候选/关闭不作为事实，不创建日程或调用模型。',
    openapi_extra={'x-assistant': {'effect': 'read', 'keywords': ['记忆', '查询记忆', '个人记忆', '检索原文', '读取习惯', '默认项', '常用地点', '作息', '出行偏好', '提醒偏好', '办事经验']}})
async def recall_business_memory(payload: BusinessRecallRequest, request: Request, db: DatabaseSession,
                                  user: CurrentUser, client: MemoryStoreDependency, settings: SettingsDependency):
    """Read owned open-domain facts, reusing only a trusted current-task confirmed-empty snapshot."""
    from assistant_api.services.matters import user_timezone, _get_matter
    from assistant_api.models import NexusRun
    from assistant_api.core.errors import ForbiddenError
    local_id = request.scope.get('assistant_execution_run_id')
    root = None
    if local_id:
        root = await db.get(NexusRun, local_id)
        bound_matter = (root.context_json or {}).get('api_flow', {}).get('matter_id') if root and root.user_id == user.user_id else None
        if bound_matter:
            if payload.matter_id and payload.matter_id != bound_matter:
                raise ForbiddenError('business_memory_scope_mismatch', '不能用其他事项替代本次任务关联。')
            payload = payload.model_copy(update={'matter_id': bound_matter})
    if payload.matter_id:
        await _get_matter(db, user, payload.matter_id)
    if root and root.user_id == user.user_id and root.status in {'pending','running'}:
        from assistant_api.services.task_memory import cached_empty
        cached = cached_empty((root.context_json or {}).get('api_flow',{}))
        if cached is not None:
            return cached
    timezone = await user_timezone(db, user, settings.matter_default_timezone)
    return await business_memory.recall(client, user.user_id, payload, timezone=timezone)


@business_router.post('/facts', operation_id='save_business_memory_fact',
    summary='保存当前用户明确要求记住的任意领域原话',
    description='仅在当前用户表达持久记忆意图时使用，不依赖固定关键词，source_quote必须逐字来自本次输入。不要求category，可选category/tags为开放文字，不懂领域也必须保留原文。只保存原话及来源，不接受AI改写、坐标推断、身份、确认标记或开关。修改已有记忆使用mutate_business_memory；不能保存历史指令、后台推测或共享许可。重复执行复用服务端标识，跨任务去重未查全会明确说明，不因此拒绝保存。',
    openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['记住', '记一下', '保存记忆', '业务记忆', '记录习惯']}})
async def save_business_memory_fact(payload: BusinessFactRequest, request: Request,
                                    db: DatabaseSession, user: CurrentUser, client: MemoryStoreDependency):
    """Save an explicitly requested current quote after server-owned source validation."""
    return await business_memory.save_user_fact(db, client, user, request, payload)


@business_router.post('/commands', operation_id='mutate_business_memory',
    summary='新增、纠正或遗忘当前用户的跨业务记忆',
    description='Assistant本地执行，不写Nexus平台记忆。action=save新增有效记忆，propose仅候选，correct改原条目，forget撤回条目（保留历史）。source_quote逐字引用本次用户原话；text是保存正文，改写标记为AI生成且保留原文。entry_id与expected_entry_version只用于correct/forget，取自实际检索；版本冲突需重读。intent=user_requested表示当前用户明确要求持久记忆操作；incidental只允许propose，不把本次安排自动当永久偏好。默认before_answer立即执行；after_answer仅入队，返回queued、applied=false，最终回答持久化后由Assistant后台执行，不得声称已保存或已删除；本轮后续操作依赖该变更时必须before_answer。记忆默认跨通勤、事项、日程共用，来源matter_id不是使用限制；用户明确限定适用范围时才写app_data.fields.applicability。category/tags是可选开放标签，不需用途开关或角色。模型不可传用户身份、授权或记忆总开关。',
    openapi_extra={'x-assistant':{'effect':'write','keywords':['记忆','记住','以后','习惯','偏好','更新记忆','纠正记忆','删除记忆','忘记','不要记','长期']}})
async def mutate_business_memory(payload: AgentMemoryCommand, request: Request, db: DatabaseSession,
                                 user: CurrentUser, client: MemoryStoreDependency):
    """Dispatch the model's validated intention with server-owned user, task and repeat-request key."""
    from assistant_api.services.memory_repository import agent_command
    from assistant_api.core.errors import ForbiddenError, InvalidRequestError
    local_id=request.scope.get('assistant_execution_run_id')
    if not local_id:
        raise ForbiddenError('memory_task_required','此接口需要当前绑定的助手任务；本人管理使用memory/commands。')
    key=request.headers.get('idempotency-key')
    if not key:
        raise InvalidRequestError('memory_key_required','缺少服务端执行标识。')
    return await agent_command(db,client,user,local_id,payload.model_dump(mode='json',exclude_none=True),key=key)


@router.get('/operations/{operation_id}')
async def memory_operation(operation_id: str, db: DatabaseSession, user: CurrentUser):
    """Expose only the owner's persisted deferred result, without encrypted request contents."""
    from sqlalchemy import select
    from assistant_api.models import MemoryOperation
    from assistant_api.core.errors import NotFoundError
    row=await db.scalar(select(MemoryOperation).where(MemoryOperation.operation_id==operation_id,
        MemoryOperation.user_id==user.user_id))
    if row is None:
        raise NotFoundError('memory_operation_not_found','未找到记忆处理记录。')
    return {'operation_id':row.operation_id,'status':row.status,'result':row.result_json,'error_code':row.error_code}
