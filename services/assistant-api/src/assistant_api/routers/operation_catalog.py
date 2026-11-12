"""Resolve the Assistant's first-level operation index without executing its business operations."""
from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field

from assistant_api.core.deps import CurrentUser
from assistant_api.services.api_operations import OperationCatalog

router = APIRouter(prefix='/api/v1/operation-catalog', tags=['operation-catalog'])


class OperationDefinitionsRequest(BaseModel):
    """Select exact local index identifiers; credentials, arbitrary URLs and business parameters are forbidden."""
    model_config = ConfigDict(extra='forbid')
    uris: list[Annotated[str, Field(min_length=1, max_length=240)]] = Field(min_length=1, max_length=8,
        description='从operation_index.items选择完整uri，例如assistant://operations/create_todo。可一次选择本任务需要的多个接口；这一步只读取定义，不执行业务。')


@router.post('/resolve', operation_id='resolve_operation_definitions', response_model=dict,
    summary='按接口索引uri获取完整调用定义，不执行业务',
    description='从初始operation_index选择所需uri，提交uris后返回api_operations及共享components.schemas。'
                '先阅读参数、条件和返回结构，再自行决定call、继续获取其他定义、回答或ask_user。'
                '解析定义不会创建或修改待办/日程/记忆。已加载定义在同一Agent中继续有效，避免重复获取。'
                'uri仅是本地目录标识，不会请求外部地址；下一步业务调用仍用operation_id，不能用uri代替请求地址。',
    openapi_extra={'x-assistant': {'effect': 'read'}})
async def resolve_operation_definitions(payload: OperationDefinitionsRequest, request: Request, user: CurrentUser):
    """Authenticate the caller and return only published descriptions, never dispatching a selected operation."""
    return OperationCatalog(request.app).resolve_definitions(payload.uris)
