"""Standalone business operations shared by ordinary clients and OpenAPI-driven AI calls."""
from fastapi import APIRouter, Header, Query
from assistant_api.core.deps import CurrentUser, DatabaseSession, CommuteRuntimeDependency, SettingsDependency
from assistant_api.schemas import matters as schemas
from assistant_api.services import record_commands

router = APIRouter(prefix='/api/v1', tags=['records'])


@router.post('/todos', operation_id='create_todo', response_model=schemas.TodoOperationRead, response_model_exclude_none=True, status_code=201,
             summary='直接新增待办并保存，日程不是前提',
             description='新增待办的首选入口，一次调用保存待办并返回真实todo和matter，不需要先创建空事项、草稿或再次确认。title必填，截止和提醒时间可省略。matter_id省略时后端在同一正式事务中创建所属事项；指定原matter_id时同时提供expected_matter_version。不会创建日程。schedule_reminder仅用户明确要求相对某条日程提醒时提供，必须指定真实schedule_id及offset_seconds，并使用该日程所属matter_id和当前版本；与绝对reminder_at二选一。同一请求复用idempotency_key。',
             openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['新增待办', '创建待办', '添加待办', '待办保存']}})
async def create_todo(payload: schemas.TodoCreateRequest, db: DatabaseSession, user: CurrentUser,
                      runtime: CommuteRuntimeDependency, settings: SettingsDependency):
    """Save one todo through the shared transaction without requiring calendar creation."""
    return await record_commands.create_record(db, runtime, settings, user, 'todo', payload)


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
              summary='直接修改或完成原待办并保存', description='先读取原todo和matter.version，传expected_matter_version及idempotency_key。只传要修改字段，未传保留；status=done完成、open恢复。due_at/reminder_at=null清除相应时间；绝对提醒和schedule_reminder互斥。不会新增待办、创建或修改日程，也不改变事项标题。结果直接包含实际保存的todo。',
              openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['修改待办', '完成待办', '恢复待办']}})
async def update_todo(todo_id: str, payload: schemas.TodoPatchRequest, db: DatabaseSession, user: CurrentUser,
                      runtime: CommuteRuntimeDependency, settings: SettingsDependency):
    """Apply specified fields to the same todo using original record snapshots and version checks."""
    return await record_commands.edit_record(db, runtime, settings, user, 'todo', todo_id, payload)


@router.post('/schedules', operation_id='create_schedule', response_model=schemas.ScheduleOperationRead, response_model_exclude_none=True, status_code=201,
             summary='直接新增日程并保存，待办可不添加', description='新增普通日程的首选入口，title与starts_at必填，ends_at和地点可省略；不会自动创建待办。可选reminder_at为日程自身的绝对提醒；用户明确要求开始前后多久提醒时使用reminder_offset_seconds（提前为负），二者互斥，不创建待办。matter_id省略时后端在同一正式事务中创建所属事项，指定时提供expected_matter_version。一次返回真实schedule、matter和提醒，无需再次确认或GET验证。用户同时要求日程和待办时可用已有结构化组合草稿再保存；通勤安排使用commute-drafts。',
             openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['新增日程', '添加日程', '创建日程', '新建会议']}})
async def create_schedule(payload: schemas.ScheduleCreateRequest, db: DatabaseSession, user: CurrentUser,
                          runtime: CommuteRuntimeDependency, settings: SettingsDependency):
    """Save only a calendar record and its optional own reminder."""
    return await record_commands.create_record(db, runtime, settings, user, 'schedule', payload)


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
              summary='直接修改原日程时间地点或标题并保存', description='先GET原日程，传expected_matter_version和idempotency_key，只提供修改字段。后端保留未传字段及同一schedule_id；不新增日程，不自动修改待办标题、截止时间和绝对提醒。只有明确schedule_reminder绑定的提醒随日程时间重算。reminder_at=null取消日程自己的提醒；明确相对提醒用reminder_offset_seconds，修改时间时后端保持该相对间隔。',
              openapi_extra={'x-assistant': {'effect': 'write', 'keywords': ['修改日程', '补充原日程', '修改会议', '调整日程时间']}})
async def update_schedule(schedule_id: str, payload: schemas.SchedulePatchRequest, db: DatabaseSession, user: CurrentUser,
                          runtime: CommuteRuntimeDependency, settings: SettingsDependency):
    """Update the same calendar record, preserving independent todo fields."""
    return await record_commands.edit_record(db, runtime, settings, user, 'schedule', schedule_id, payload)


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
