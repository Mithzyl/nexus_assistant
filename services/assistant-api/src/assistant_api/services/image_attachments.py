"""Validate supported images and build metadata-free thumbnails."""
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps
from anyio import CapacityLimiter
from sqlalchemy import select

from assistant_api.core.errors import InvalidRequestError, NotFoundError
from assistant_api.models import ImageAttachment

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_ATTACHMENT_COUNT = 50
MAX_ACCOUNT_BYTES = 100 * 1024 * 1024
IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
THUMBNAIL_LIMITER = CapacityLimiter(2)


@dataclass(frozen=True)
class PreparedImage:
    """Decoded image facts and the preview bytes safe to store with the original."""

    media_type: str
    width: int
    height: int
    thumbnail: bytes


def prepare_image(raw: bytes) -> PreparedImage:
    """Decode one bounded still image, verify its file and generate a small preview."""
    try:
        with Image.open(BytesIO(raw), formats=list(IMAGE_FORMATS)) as image:
            width, height = image.size
            if max(width, height) > 6000 or width * height > 16_000_000:
                raise InvalidRequestError("image_dimensions", "图片尺寸过大，请缩小后再上传。")
            if getattr(image, "is_animated", False):
                raise InvalidRequestError("image_animated", "请选择静态图片。")
            media_type = IMAGE_FORMATS[image.format]
            image.verify()
        with Image.open(BytesIO(raw), formats=list(IMAGE_FORMATS)) as image:
            image.load()
            ImageOps.exif_transpose(image, in_place=True)
            width, height = image.size
            preview = image.convert("RGBA")
            preview.thumbnail((480, 480))
            preview.info.clear()
            with BytesIO() as thumbnail:
                preview.save(thumbnail, "PNG")
                return PreparedImage(media_type, width, height, thumbnail.getvalue())
    except Image.DecompressionBombError as exc:
        raise InvalidRequestError("image_dimensions", "图片尺寸过大，请缩小后再上传。") from exc
    except (OSError, ValueError, SyntaxError, OverflowError) as exc:
        raise InvalidRequestError("image_invalid", "图片无法打开，请选择一张有效的图片。") from exc


async def owned_attachment(db, user_id: str, attachment_id: str) -> ImageAttachment:
    """Return the image metadata only if the current account owns it."""
    attachment = await db.scalar(select(ImageAttachment).where(
        ImageAttachment.user_id == user_id, ImageAttachment.attachment_id == attachment_id))
    if attachment is None:
        raise NotFoundError("attachment_unavailable", "找不到这张图片。")
    return attachment
