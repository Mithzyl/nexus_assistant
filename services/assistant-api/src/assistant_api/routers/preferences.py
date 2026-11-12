"""Current-task memory selection and deprecated owner settings share the existing identity."""
from fastapi import APIRouter, Request

from assistant_api.core.deps import CurrentUser, DatabaseSession, NexusClient
from assistant_api.core.errors import ForbiddenError
from assistant_api.schemas.preferences import PreferencePatch, PreferenceRead, PreferenceResolution, PreferenceResolveRequest
from assistant_api.services import preferences

router=APIRouter(prefix='/api/v1/preferences',tags=['preferences'])


@router.get('',response_model=PreferenceRead,deprecated=True,
    description='保留旧设置读取兼容；自动应用记忆不再依赖用途开关和角色绑定。',
    openapi_extra={'x-assistant':{'enabled':False}})
async def get_preferences(db: DatabaseSession, user: CurrentUser):
    """Read retained legacy controls; they no longer decide which memories a new task uses."""
    return await preferences.read(db,user)


@router.patch('',response_model=PreferenceRead,deprecated=True,
    description='仅兼容旧客户端保存，不是新任务的前提，也不控制AI选用记忆；关闭记忆请使用已有memory/settings。',
    openapi_extra={'x-assistant':{'enabled':False}})
async def update_preferences(payload: PreferencePatch, request: Request, db: DatabaseSession,
                             user: CurrentUser, client: NexusClient):
    """Let the account owner configure defaults; execution callbacks cannot enable their own permissions."""
    if request.scope.get('assistant_execution_run_id'):
        raise ForbiddenError('preferences_owner_required','只有用户本人可管理默认用途和角色绑定。')
    return await preferences.patch(db,client,user,payload)


@router.post('/resolve',response_model=PreferenceResolution,operation_id='resolve_business_preferences',
    summary='校验AI为本次业务选择的记忆值及来源',
    description='只读，不要求用户开启用途或绑定角色。AI根据当前输入和已检索记忆判断适用性，在selections按业务字段提交entry_id/version/source_quote/value/reason；values只放本次明确输入；与同字段selections（含关联字段或null）同时提交返回input_source_conflict，不自动选择。后端校验归属、当前版本、原文、限制和字段类型，不代替AI做语义判断；解释值标model_interpretation。选不准时ask_user带question/choices/cards，不猜。返回values/fields及未采用原因，不保存安排。实际写入仍传memory_defaults，服务端再次校验并保存来源。',
    openapi_extra={'x-assistant':{'effect':'read','keywords':['偏好','习惯','默认地点','默认提醒','默认交通方式']}})
async def resolve_preferences(payload: PreferenceResolveRequest, request: Request, db: DatabaseSession,
                              user: CurrentUser, client: NexusClient):
    """Validate proposed source-grounded values for the current bound task without writing business data."""
    return await preferences.resolve(db,client,user,payload,
        execution_run_id=request.scope.get('assistant_execution_run_id'))
