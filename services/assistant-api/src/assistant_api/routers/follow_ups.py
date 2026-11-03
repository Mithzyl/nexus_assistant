"""Public follow-up operations discovered by the existing Assistant API catalogue."""
from typing import Literal
from fastapi import APIRouter, Request, Query
from sqlalchemy import select
from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.models import FollowUp, FollowUpExecution
from assistant_api.schemas.follow_ups import FollowUpCreate, FollowUpPatch, FollowUpRead, FollowUpExecutionRead
from assistant_api.services import follow_ups

router=APIRouter(prefix='/api/v1/follow-ups',tags=['follow-ups'])


@router.post('',response_model=FollowUpRead,status_code=201,operation_id='create_follow_up',summary='创建事项持续跟进并指定检查时间或变化条件',
    description='先取得真实matter_id与关联记录。references使用已发布只读operation_id、路径参数和唯一筛选，name唯一；trigger按时间或记录变化选择。时间必须有时区；记录字段用JSON Pointer。evaluate只检查提示，execute仅用户明确要求触发后执行操作时使用。stop_when满足即停止，每用户最多100条未结束规则。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def create_follow_up(request: Request, payload: FollowUpCreate, db: DatabaseSession, user: CurrentUser):
    """Start following existing records from the user's stated requirements."""
    return await follow_ups.create_rule(request.app,db,user,payload)


@router.get('',response_model=list[FollowUpRead],operation_id='list_follow_ups',summary='查询事项跟进规则',
    description='按matter_id或state查询真实规则；暂停和恢复时先取当前version。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_follow_ups(db: DatabaseSession, user: CurrentUser, matter_id: str | None = None, state: Literal['enabled','paused','closed'] | None = None, limit: int = Query(20, ge=1, le=100)):
    """List the user's rules, optionally scoped to an existing matter."""
    query=select(FollowUp).where(FollowUp.user_id==user.user_id)
    if matter_id:
        query=query.where(FollowUp.matter_id==matter_id)
    if state:
        query=query.where(FollowUp.state==state)
    rows=list(await db.scalars(query.order_by(FollowUp.created_at.desc()).limit(limit)))
    return [follow_ups.rule_read(row) for row in rows]


@router.get('/{follow_up_id}',response_model=FollowUpRead,operation_id='get_follow_up',summary='读取跟进规则及当前版本',
    description='返回已保存规则、记录引用和下次检查时间；修改时用当前version。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def get_follow_up(follow_up_id: str, db: DatabaseSession, user: CurrentUser):
    """Read the current owned rule without executing it."""
    return follow_ups.rule_read(await follow_ups.owned_rule(db,user.user_id,follow_up_id))


@router.patch('/{follow_up_id}',response_model=FollowUpRead,operation_id='update_follow_up',summary='修改暂停恢复或结束跟进',
    description='传当前expected_version；仅提供要修改字段，非可空字段不要显式传null。paused停止待执行任务，enabled恢复，closed结束；trigger或references变化按新规则计算。',
    openapi_extra={'x-assistant': {'effect': 'write'}}
)
async def update_follow_up(follow_up_id: str, request: Request, payload: FollowUpPatch, db: DatabaseSession, user: CurrentUser):
    """Apply a versioned change to one owned follow-up."""
    return await follow_ups.patch_rule(request.app,db,user,follow_up_id,payload)


@router.get('/{follow_up_id}/executions',response_model=list[FollowUpExecutionRead],operation_id='list_follow_up_executions',summary='查询跟进执行与待补充问题',
    description='返回执行状态及local_run_id；waiting_user代表需要用户补充，不能当作已执行成功。',
    openapi_extra={'x-assistant': {'effect': 'read'}}
)
async def list_follow_up_executions(follow_up_id: str, db: DatabaseSession, user: CurrentUser, limit: int = Query(20, ge=1, le=50)):
    """Read execution references for opening the corresponding conversation turn."""
    await follow_ups.owned_rule(db,user.user_id,follow_up_id)
    rows=list(await db.scalars(select(FollowUpExecution).where(FollowUpExecution.follow_up_id==follow_up_id).order_by(FollowUpExecution.created_at.desc()).limit(limit)))
    return [{'execution_id':r.execution_id,'follow_up_id':r.follow_up_id,'state':r.state,'local_run_id':r.local_run_id,'created_at':r.created_at} for r in rows]
