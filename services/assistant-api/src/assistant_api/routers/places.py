"""Authenticated place queries shared by commute forms and schedule editors."""

from fastapi import APIRouter, Depends, Request

from assistant_api.core.deps import CommuteRuntimeDependency, CurrentUser
from assistant_api.core.errors import InvalidRequestError
from assistant_api.schemas.places import (
    PlaceLookupRequest,
    PlaceLookupResponse,
    PlacePointRequest,
    PlacePointResponse,
)
from assistant_api.services import place_queries


def require_body_parameters(request: Request) -> None:
    """Reject URL query parameters for place lookup; addresses and coordinates belong in JSON."""
    if request.url.query:
        raise InvalidRequestError(
            "unexpected_query_parameters", "地点查询参数必须放在 JSON 请求体中，URL 不接受查询参数。",
        )


router = APIRouter(
    prefix="/api/v1/places", tags=["places"], dependencies=[Depends(require_body_parameters)],
)


@router.post(
    "/search", response_model=PlaceLookupResponse, operation_id="search_place_candidates",
    summary="查询地点或地址候选，不创建草稿或业务记录",
    description="需登录。query、city、search_type、limit 仅放 JSON body，URL 不接受查询参数。"
                "不需要 draft_id；多个候选由用户选择，不能把第一条当作已确认地点。"
                "候选不包含草稿 selection_token；日程草稿绑定继续使用原草稿地点接口。",
    openapi_extra={"x-assistant": {"effect": "read", "keywords": ["通勤起点", "通勤终点", "地点搜索", "地址查询"]}},
)
async def search_place_candidates(
    payload: PlaceLookupRequest, runtime: CommuteRuntimeDependency, user: CurrentUser,
) -> dict:
    """Return shared provider candidates without saving a draft, location or plan."""
    return await place_queries.search_places(runtime, **payload.model_dump())


@router.post(
    "/map-pick", response_model=PlacePointResponse, operation_id="lookup_map_point",
    summary="反查用户选定地图坐标的地址，不保存业务记录",
    description="需登录。latitude、longitude、coordinate_system 仅放 JSON body，URL 不接受查询参数。"
                "只支持用户已选的 GCJ-02 坐标，不需要 draft_id，不得猜坐标。"
                "返回坐标附近的地址而非保证精确的 POI；用户确认后再提交相应业务保存接口。",
    openapi_extra={"x-assistant": {"effect": "read", "keywords": ["地图选点", "通勤地点", "坐标反查"]}},
)
async def lookup_map_point(
    payload: PlacePointRequest, runtime: CommuteRuntimeDependency, user: CurrentUser,
) -> dict:
    """Reverse lookup one chosen map point without issuing a draft-bound selection token."""
    return await place_queries.map_pick_place(runtime, **payload.model_dump())
