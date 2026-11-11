"""Standalone business operations shared by ordinary clients and OpenAPI-driven AI calls."""
from fastapi import APIRouter, Header, Query, Request
from assistant_api.core.deps import CurrentUser, DatabaseSession, CommuteRuntimeDependency, SettingsDependency, NexusClient
from assistant_api.schemas import matters as schemas
from assistant_api.services import record_commands

router = APIRouter(prefix='/api/v1', tags=['records'])


@router.post('/todos', operation_id='create_todo', response_model=schemas.TodoOperationRead, response_model_exclude_none=True, status_code=201,
             summary='直接新增待办并保存，日程不是前提',
             description='新增待办的首选入口，一次调用保存待办并返回真实todo和matter，不需要先创建空事项、草稿或再次确认。title必填，截止和提醒时间可省略。matter_id省略时后端在同一正式事务中创建所属事项；指定原matter_id时同时提供expected_matter_version。不会创建日程。schedule_reminder仅用户明确要求相对某条日程提醒时提供，必须指定真实schedule_id及offset_seconds，并使用该日程所属matter_id和当前版本；与绝对reminder_at二选一。同一请求复用idempotency_key。 AI按本次任务选择已检索记忆时传memory_defaults，包含entry_id/version/source_quote/value/reason，可附references记录计算所依据的真实对象和版本；省略同字段普通参数，null也不能重复。修改时只对明确提交的记忆选择重新应用，其他字段保留。来源内部保存，不新增前端入口、不默认输出记忆原文；必要解释由AI简述，确需时可用卡片。',
             openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['新增待办', '创建待办', '添加待办', '待办保存']}})
async def create_todo(payload: schemas.TodoCreateRequest, request: Request, db: DatabaseSession, user: CurrentUser,
                      runtime: CommuteRuntimeDependency, settings: SettingsDependency, client: NexusClient):
    """Save one todo and server-verified memory attribution atomically, without calendar creation."""
    return await record_commands.create_record(db, runtime, settings, user, 'todo', payload, client=client,
        execution_run_id=request.scope.get('assistant_execution_run_id'))


@router.get('/todos', operation_id='list_todos', response_model=schemas.TodoListRead, summary='查找原待办及标识',
            description='按query标题字面条件和可选matter_id查询当前用户待办。多个同名结果时以用户已知条件唯一选择或询问，不按数组位置猜选。修改前GET原todo取得matter.version。',
            openapi_extra={'x-assistant': {'effect': 'read', 'keywords': ['查询待办', '查找待办']}})
async def list_todos(db: DatabaseSession, user: CurrentUser, matter_id: str | None = None,
                     query: str | None = Query(None, max_length=255), limit: int = Query(30, ge=1, le=50)):
    """Search actual todo records independently of calendar records."""
    return await record_commands.list_records(db, user, 'todo', matter_id=matter_id, query=query, limit=limit)


@router.get('/todos/{todo_id}', operation_id='get_todo', response_model=schemas.TodoOperationRead, response_model_exclude_none=True,
            summary='读取原待办及所属事项当前版本', description='返回实际todo、matter.version和该待办提醒。后续PATCH只提供指定修改字段，expected_matter_version引用/matter/version；原todo_id保持不变。',
            openapi_extra={'x-assistant': {'effect': 'read', 'next': [{'method': 'PATCH', 'path': '/api/v1/todos/{todo_id}', 'purpose': '修改时使用实际matter.version，只传用户要改的字段。'}]}})
async def get_todo(todo_id: str, db: DatabaseSession, user: CurrentUser):
    """Read an owned todo and its current parent version."""
    return await record_commands.read_record(db, user, 'todo', todo_id)


@router.patch('/todos/{todo_id}', operation_id='update_todo', response_model=schemas.TodoOperationRead, response_model_exclude_none=True,
              summary='直接修改或完成原待办并保存', description='先读取原todo和matter.version，传expected_matter_version及idempotency_key。只传要修改字段，未传保留；status=done完成、open恢复。due_at/reminder_at=null清除相应时间；绝对提醒和schedule_reminder互斥。不会新增待办、创建或修改日程，也不改变事项标题。结果直接包含实际保存的todo。 AI按本次任务选择已检索记忆时传memory_defaults，包含entry_id/version/source_quote/value/reason，可附references记录计算所依据的真实对象和版本；省略同字段普通参数，null也不能重复。修改时只对明确提交的记忆选择重新应用，其他字段保留。来源内部保存，不新增前端入口、不默认输出记忆原文；必要解释由AI简述，确需时可用卡片。',
              openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['修改待办', '完成待办', '恢复待办']}})
async def update_todo(todo_id: str, payload: schemas.TodoPatchRequest, request: Request, db: DatabaseSession, user: CurrentUser,
                      runtime: CommuteRuntimeDependency, settings: SettingsDependency, client: NexusClient):
    """Apply specified todo fields and explicit memory reapplication using the same identity and version checks."""
    return await record_commands.edit_record(db, runtime, settings, user, 'todo', todo_id, payload, client=client,
        execution_run_id=request.scope.get('assistant_execution_run_id'))


@router.post('/schedules', operation_id='create_schedule', response_model=schemas.ScheduleOperationRead, response_model_exclude_none=True, status_code=201,
             summary='直接新增日程并保存，待办可不添加', description='直接保存普通日程，不自动创建待办。title必填，通常传starts_at；AI按本次意图选择记忆时在memory_defaults按字段给entry_id/version/source_quote/value/reason，不需要用户配置用途或角色。参数来源由AI按当前输入选择；同一字段的明确值（包括null）与memory_defaults互斥，两处同时提供返回input_source_conflict且未写入。采用记忆时省略对应普通字段；未知值省略。用习惯时间时还需具体preference_date；不确定用ask_user的question/choices/cards。reminder_at与reminder_offset_seconds互斥，reminder_at=null禁止本次默认提醒。use_preferences=false本次禁用记忆。matter_id可选，指定时提供expected_matter_version。返回真实schedule/matter/提醒及preference_application来源，application_status=applied才已保存，scope=creation表示创建历史；重复请求复用原结果。组合草稿与长期通勤计划也支持本次记忆选择，来源内部保存，不默认展示原文。',
             openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['新增日程', '添加日程', '创建日程', '新建会议']}})
async def create_schedule(payload: schemas.ScheduleCreateRequest, request: Request, db: DatabaseSession, user: CurrentUser,
                          runtime: CommuteRuntimeDependency, settings: SettingsDependency, client: NexusClient):
    """Save a calendar record with server-bound task identity and verified memory attribution."""
    return await record_commands.create_record(db,runtime,settings,user,'schedule',payload,client=client,
        execution_run_id=request.scope.get('assistant_execution_run_id'))


@router.get('/schedules', operation_id='list_schedules', response_model=schemas.ScheduleListRead, summary='查找原日程及所属事项',
            description='按query标题字面条件及可选matter_id查询实际日程；使用时间地点等用户已知条件唯一选择原schedule_id，多个匹配时询问。修改前GET原日程读取当前matter.version。',
            openapi_extra={'x-assistant': {'effect': 'read', 'keywords': ['查找日程', '查询日程', '查找会议']}})
async def list_schedules(db: DatabaseSession, user: CurrentUser, matter_id: str | None = None,
                         query: str | None = Query(None, max_length=255), limit: int = Query(30, ge=1, le=50)):
    """Search existing schedules without creating a matter or a todo."""
    return await record_commands.list_records(db, user, 'schedule', matter_id=matter_id, query=query, limit=limit)


@router.get('/schedules/{schedule_id}', operation_id='get_schedule', response_model=schemas.ScheduleOperationRead, response_model_exclude_none=True,
            summary='读取原日程及事项当前版本', description='返回真实schedule和matter.version。修改用PATCH，取消未来日程用DELETE；两者均传当前事项版本。设备导入日程不能在应用内改写。',
            openapi_extra={'x-assistant': {'effect': 'read', 'next': [
                {'method': 'PATCH', 'path': '/api/v1/schedules/{schedule_id}', 'purpose': '只修改用户指定的原日程字段。'},
                {'method': 'DELETE', 'path': '/api/v1/schedules/{schedule_id}', 'purpose': '仅取消指定未来日程，不删除事项或待办。'}]}})
async def get_schedule(schedule_id: str, db: DatabaseSession, user: CurrentUser):
    """Read an owned schedule and its parent version for a subsequent explicit command."""
    return await record_commands.read_record(db, user, 'schedule', schedule_id)


@router.patch('/schedules/{schedule_id}', operation_id='update_schedule', response_model=schemas.ScheduleOperationRead, response_model_exclude_none=True,
              summary='直接修改原日程时间地点或标题并保存', description='先GET原日程，传expected_matter_version和idempotency_key，只提供修改字段。后端保留未传字段及同一schedule_id；不新增日程，不自动修改待办标题、截止时间和绝对提醒。只有明确schedule_reminder绑定的提醒随日程时间重算。reminder_at=null取消日程自己的提醒；明确相对提醒用reminder_offset_seconds，修改时间时后端保持该相对间隔。 AI按本次任务选择已检索记忆时传memory_defaults，包含entry_id/version/source_quote/value/reason，可附references记录计算所依据的真实对象和版本；省略同字段普通参数，null也不能重复。修改时只对明确提交的记忆选择重新应用，其他字段保留。来源内部保存，不新增前端入口、不默认输出记忆原文；必要解释由AI简述，确需时可用卡片。',
              openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['修改日程', '补充原日程', '修改会议', '调整日程时间']}})
async def update_schedule(schedule_id: str, payload: schemas.SchedulePatchRequest, request: Request, db: DatabaseSession, user: CurrentUser,
                          runtime: CommuteRuntimeDependency, settings: SettingsDependency, client: NexusClient):
    """Update the same calendar record, retaining explicit reapplication sources and unrelated fields."""
    return await record_commands.edit_record(db, runtime, settings, user, 'schedule', schedule_id, payload, client=client,
        execution_run_id=request.scope.get('assistant_execution_run_id'))


@router.delete('/schedules/{schedule_id}', operation_id='cancel_schedule', response_model=schemas.ScheduleOperationRead, response_model_exclude_none=True,
               summary='取消指定未来日程，保留事项和待办', description='先GET原日程并提供当前expected_matter_version查询参数及Idempotency-Key。沿用既有未来日程取消规则；只取消此日程及自身或明确依赖的提醒，不完成、不删除相关待办，不取消同事项的独立待办提醒。',
               openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['取消日程', '取消会议']}})
async def cancel_schedule(schedule_id: str, db: DatabaseSession, user: CurrentUser,
                          runtime: CommuteRuntimeDependency, settings: SettingsDependency,
                          expected_matter_version: int = Query(ge=1),
                          idempotency_key: str = Header(alias='Idempotency-Key', min_length=8, max_length=128)):
    """Cancel one future schedule through the existing atomic cancellation transaction."""
    payload = schemas.SchedulePatchRequest(idempotency_key=idempotency_key, expected_matter_version=expected_matter_version)
    return await record_commands.edit_record(db, runtime, settings, user, 'schedule', schedule_id, payload, cancel=True)
