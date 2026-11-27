"""Expose authenticated image upload, metadata and same-origin previews."""
from datetime import datetime
from typing import Literal

from anyio import to_thread
from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select, update

from assistant_api.core.deps import CurrentUser, DatabaseSession
from assistant_api.core.errors import AssistantError, ConflictError, NotFoundError
from assistant_api.models import ImageAttachment, User
from assistant_api.models.base import as_utc
from assistant_api.services import image_attachments, matters

router = APIRouter(
    prefix="/api/v1/image-attachments", tags=["image-attachments"],
    responses={404: {"description": "图片不存在或当前账号无权读取"}},
)


class AttachmentRead(BaseModel):
    """Return safe metadata and owned preview paths without file bytes."""

    attachment_id: str
    matter_id: str | None
    media_type: str
    width: int
    height: int
    byte_size: int
    caption: str
    created_at: datetime
    thumbnail_path: str
    original_path: str


class AttachmentPatch(BaseModel):
    """Edit the owner's caption or explicitly change the associated matter."""

    model_config = ConfigDict(extra="forbid")
    caption: str | None = Field(default=None, max_length=300)
    matter_id: str | None = Field(default=None, min_length=1, max_length=36)


def attachment_read(row: ImageAttachment) -> AttachmentRead:
    """Build preview paths from the generated identifier."""
    path = f"/image-attachments/{row.attachment_id}"
    return AttachmentRead(
        attachment_id=row.attachment_id, matter_id=row.matter_id, media_type=row.media_type,
        width=row.width, height=row.height, byte_size=row.byte_size, caption=row.caption,
        created_at=as_utc(row.created_at), thumbnail_path=f"{path}/thumbnail", original_path=f"{path}/original",
    )


@router.post("", response_model=AttachmentRead, status_code=201)
async def upload_image(request: Request, db: DatabaseSession, user: CurrentUser,
                       matter_id: str | None = Header(default=None, alias="X-Matter-Id", max_length=36)) -> AttachmentRead:
    """Stream a bounded image body, decode off the event loop and save atomically."""
    if request.headers.get("content-type", "").partition(";")[0].strip() != "application/octet-stream":
        raise AssistantError("image_body_required", "请选择图片后再上传。", 415)
    if matter_id:
        await matters._get_matter(db, user, matter_id)
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > image_attachments.MAX_UPLOAD_BYTES:
            raise AssistantError("image_too_large", "图片不能超过 8 兆。", 413)
        raw.extend(chunk)
    original = bytes(raw)
    image = await to_thread.run_sync(image_attachments.prepare_image, original,
                                     limiter=image_attachments.THUMBNAIL_LIMITER)
    # Serialize uploads for this account while checking quota and adding the file.
    await db.execute(update(User).where(User.user_id == user.user_id)
                     .values(updated_at=User.updated_at))
    count, size = (await db.execute(select(func.count(), func.coalesce(func.sum(ImageAttachment.byte_size), 0))
                   .where(ImageAttachment.user_id == user.user_id))).one()
    if count >= image_attachments.MAX_ATTACHMENT_COUNT or size + len(original) > image_attachments.MAX_ACCOUNT_BYTES:
        raise ConflictError("image_storage_full", "图片空间已满，请删除几张不需要的图片后再试。")
    row = ImageAttachment(user_id=user.user_id, matter_id=matter_id or None, media_type=image.media_type, width=image.width,
                          height=image.height, byte_size=len(original), original=original, thumbnail=image.thumbnail)
    db.add(row)
    await db.commit()
    return attachment_read(row)


@router.get("", response_model=list[AttachmentRead])
async def list_images(db: DatabaseSession, user: CurrentUser, matter_id: str | None = None) -> list[AttachmentRead]:
    """List image metadata for the current account or one owned matter."""
    query = select(ImageAttachment).where(ImageAttachment.user_id == user.user_id)
    if matter_id:
        await matters._get_matter(db, user, matter_id)
        query = query.where(ImageAttachment.matter_id == matter_id)
    rows = await db.scalars(query.order_by(ImageAttachment.created_at.desc(), ImageAttachment.attachment_id))
    return [attachment_read(row) for row in rows]


@router.patch("/{attachment_id}", response_model=AttachmentRead)
async def update_image(attachment_id: str, payload: AttachmentPatch,
                       db: DatabaseSession, user: CurrentUser) -> AttachmentRead:
    """Save the owner's description or association without changing image content."""
    row = await image_attachments.owned_attachment(db, user.user_id, attachment_id)
    if "matter_id" in payload.model_fields_set:
        if payload.matter_id:
            await matters._get_matter(db, user, payload.matter_id)
        row.matter_id = payload.matter_id
    if "caption" in payload.model_fields_set:
        row.caption = (payload.caption or "").strip()
    await db.commit()
    return attachment_read(row)


@router.get("/{attachment_id}/{variant}")
async def preview_image(attachment_id: str, variant: Literal["thumbnail", "original"],
                        db: DatabaseSession, user: CurrentUser) -> Response:
    """Return only the requested owned bytes with private, non-executable headers."""
    content = ImageAttachment.thumbnail if variant == "thumbnail" else ImageAttachment.original
    row = (await db.execute(select(content, ImageAttachment.media_type).where(
        ImageAttachment.user_id == user.user_id, ImageAttachment.attachment_id == attachment_id))).one_or_none()
    if row is None:
        raise NotFoundError("attachment_unavailable", "找不到这张图片。")
    return Response(row[0], media_type="image/png" if variant == "thumbnail" else row[1], headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": "inline",
        "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; sandbox",
        "Cross-Origin-Resource-Policy": "same-origin",
        "X-Content-Type-Options": "nosniff",
    })


@router.delete("/{attachment_id}", status_code=204)
async def delete_image(attachment_id: str, db: DatabaseSession, user: CurrentUser) -> Response:
    """Remove only the owner's image, including both stored variants."""
    removed = await db.execute(delete(ImageAttachment).where(
        ImageAttachment.user_id == user.user_id, ImageAttachment.attachment_id == attachment_id))
    if removed.rowcount != 1:
        raise NotFoundError("attachment_unavailable", "找不到这张图片。")
    await db.commit()
    return Response(status_code=204)
