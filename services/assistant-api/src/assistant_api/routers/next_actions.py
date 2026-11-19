"""Expose Assistant-owned availability and recommendation operations to Web and Nexus."""
from fastapi import APIRouter, Request

from assistant_api.core.deps import CurrentUser, DatabaseSession, SettingsDependency
from assistant_api.schemas import next_actions as schemas
from assistant_api.services import next_actions

router = APIRouter(prefix='/api/v1', tags=['next-actions'])


@router.get('/availability', operation_id='get_availability', response_model=schemas.AvailabilityRead,
    summary='读取当前账号已保存的每周作息可用窗口',
    description='返回时区、星期和本地时间窗口及version；未配置为version=0、windows=[]，不要猜作息。修改前先读完整旧值。',
    openapi_extra={'x-assistant': {'keywords':['作息窗口','可用时间','工作时间']}})
async def get_availability(db: DatabaseSession, user: CurrentUser, settings: SettingsDependency):
    """Return weekly business availability, without automatically adopting any memory."""
    return await next_actions.read_availability(db,user,settings.matter_default_timezone)


@router.put('/availability', operation_id='save_availability', response_model=schemas.AvailabilityRead,
    summary='按用户明确要求保存每周作息或休息窗口',
    description='先 GET /api/v1/availability，使用其version作为expected_version；timezone为IANA时区。完整替换，保留不变窗口；windows=[]明确清除。星期一=1，周日=7；end小于start表示跨午夜。unavailable从available扣除。仅在用户要保存长期安排时写入。如果AI选用已检索且无临时/事项限制的长期记忆，提交memory_defaults.windows及真实出处、版本、值和理由，不传普通windows；Assistant校验并内部保存来源。读取推荐不需要先配置。',
    openapi_extra={'x-assistant': {'effect':'write','keywords':['设置作息','保存工作时间','每周可用时段','休息窗口']}})
async def put_availability(payload: schemas.AvailabilityWrite, request: Request, db: DatabaseSession, user: CurrentUser):
    """Save a version-checked business window definition using the actual task identity."""
    return await next_actions.save_availability(db,user,payload,execution_run_id=request.scope.get('assistant_execution_run_id'))


@router.post('/next-actions/query', operation_id='query_next_actions', response_model=schemas.NextActionRead,
    summary='查看今天现在最值得处理的待办或日程',
    description='无需请求参数。按服务器时间、已保存作息、当前账号待办和日程排序，不调用模型或地图、不改待办或日程。primary是首选，alternatives为备选；primary=null表示当前没有可推荐动作，结合data_status说明数据是否缺失。到已验证保存路线的出发时刻时，state=departure_due 优先提示；departure_at、destination、route_status 表示已保存依据，不会推断当前地点、实时路况或路程。按原record_id打开记录；待办完成或改期用原待办PATCH，日程改期用原日程PATCH；先读取对应记录获取当前matter.version。暂缓、忽略、恢复才用command_next_action。完成任何操作后重新查询。',
    openapi_extra={'x-assistant': {'effect':'read','keywords':['下一步','行动推荐','接下来先做什么','今天推荐']}})
async def query_next_actions(db: DatabaseSession, user: CurrentUser, settings: SettingsDependency):
    """Return one recommendation and limited alternatives from local business data."""
    return await next_actions.query(db,user,settings.matter_default_timezone)


@router.post('/next-actions/commands', operation_id='command_next_action', response_model=schemas.NextActionReceipt,
    summary='暂缓、忽略或恢复本人的下一步建议',
    description='先查询下一步；传recommendation_id、version作为expected_recommendation_version、真实kind和record_id及idempotency_key。snooze需要带时区的until，dismiss仅今天隐藏，resume用suppressed的对象恢复。仅保存推荐反馈，不改变截止、提醒或原日程。过期或业务已变化时重新查询；成功后也重新查询。不能通过此接口完成待办或改期。',
    openapi_extra={'x-assistant': {'effect':'write','keywords':['稍后推荐','暂缓下一步','忽略行动','恢复推荐']}})
async def command_next_action(payload: schemas.NextActionCommand, db: DatabaseSession, user: CurrentUser,
                              settings: SettingsDependency):
    """Apply only explicit recommendation feedback after verifying the owned live snapshot."""
    return await next_actions.command(db,user,payload,settings.matter_default_timezone)
