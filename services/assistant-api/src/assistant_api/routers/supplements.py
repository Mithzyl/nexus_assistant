"""Owned supplement records; saving does not wait for AI or execute business changes."""

from fastapi import APIRouter, Query
from sqlalchemy import or_, select

from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.core.errors import ConflictError
from assistant_api.models.supplement import MatterSupplement
from assistant_api.schemas.supplements import EmptyRequest, SupplementCreate, SupplementRead
from assistant_api.services import supplements

router = APIRouter(prefix="/api/v1/matters/{matter_id}/supplements", tags=["supplements"])
# These are explicit user submission/adoption endpoints, not tools for the business planner.
USER_ONLY = {"x-assistant": {"enabled": False}}


@router.post("", response_model=SupplementRead, status_code=201,
             operation_id="save_matter_supplement", openapi_extra=USER_ONLY,
             summary="保存用户补充原文并登记异步AI整理")
async def save_supplement(matter_id: str, payload: SupplementCreate,
                          db: DatabaseSession, user: CurrentUser):
    """Persist original text and the AI queue entry before returning; never wait for model output."""
    return await supplements.save(db, user, matter_id, payload)


@router.get("", response_model=list[SupplementRead], operation_id="list_matter_supplements",
            openapi_extra=USER_ONLY, summary="读取用户及AI来源分开的补充记录")
async def list_supplements(matter_id: str, db: DatabaseSession, user: CurrentUser,
                           limit: int = Query(default=20, ge=1, le=50),
                           before_id: str | None = Query(default=None, max_length=36)):
    """Page newest-first by an owned cursor; reads never start or retry a model job."""
    await supplements.require_matter(db, user.user_id, matter_id)
    query = select(MatterSupplement).where(
        MatterSupplement.user_id == user.user_id, MatterSupplement.matter_id == matter_id,
    )
    if before_id:
        before = await supplements.require_supplement(db, user.user_id, matter_id, before_id)
        query = query.where(or_(MatterSupplement.created_at < before.created_at,
                                (MatterSupplement.created_at == before.created_at) &
                                (MatterSupplement.supplement_id < before.supplement_id)))
    rows = list(await db.scalars(query.order_by(
        MatterSupplement.created_at.desc(), MatterSupplement.supplement_id.desc(),
    ).limit(limit)))
    return [await supplements.serialize(db, row) for row in rows]


@router.get("/{supplement_id}", response_model=SupplementRead, operation_id="get_matter_supplement",
            openapi_extra=USER_ONLY, summary="查询已保存补充及AI整理状态")
async def get_supplement(matter_id: str, supplement_id: str,
                         db: DatabaseSession, user: CurrentUser):
    """Return durable text and status after page closure without dispatching any AI work."""
    row = await supplements.require_supplement(db, user.user_id, matter_id, supplement_id)
    return await supplements.serialize(db, row)


@router.post("/{supplement_id}/texts/{text_id}/accept", response_model=SupplementRead,
             operation_id="accept_supplement_text", openapi_extra=USER_ONLY,
             summary="记录用户采纳AI补充，不改变原作者或执行安排")
async def accept_supplement_text(matter_id: str, supplement_id: str, text_id: str,
                                 payload: EmptyRequest, db: DatabaseSession, user: CurrentUser):
    """Assign acceptance to the logged-in user; an accepted suggestion remains AI-authored."""
    return await supplements.accept_text(db, user, matter_id, supplement_id, text_id)


@router.post("/{supplement_id}/retry", response_model=SupplementRead,
             operation_id="retry_supplement_analysis", openapi_extra=USER_ONLY,
             summary="用户明确重试已失败的AI整理，不重复保存原文")
async def retry_supplement_analysis(matter_id: str, supplement_id: str,
                                    payload: EmptyRequest, db: DatabaseSession, user: CurrentUser):
    """Queue one explicit retry only after a definite failure; unknown remote outcomes cannot retry."""
    from sqlalchemy import update
    row = await supplements.require_supplement(db, user.user_id, matter_id, supplement_id)
    if row.ai_status in {"queued", "running"}:
        return await supplements.serialize(db, row, replayed=True)
    if row.ai_status != "failed" or row.analysis_attempt >= 3:
        raise ConflictError("analysis_retry_unavailable", "当前结果不能重试，或已达到三次重试上限。结果未知时不会重复调用模型。")
    changed = await db.execute(update(MatterSupplement).where(
        MatterSupplement.supplement_id == supplement_id, MatterSupplement.ai_status == "failed",
        MatterSupplement.analysis_attempt == row.analysis_attempt,
    ).values(ai_status="queued", analysis_attempt=row.analysis_attempt + 1,
             local_run_id=None, error_code=None))
    await db.commit()
    await db.refresh(row)
    return await supplements.serialize(db, row, replayed=not bool(changed.rowcount))
