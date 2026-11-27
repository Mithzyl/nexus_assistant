"""Store an account's image originals, bounded thumbnails and provenance together."""
from sqlalchemy import ForeignKey, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class ImageAttachment(TimestampMixin, Base):
    """An owned image, optionally filed under one matter by its owner."""

    __tablename__ = "image_attachments"

    attachment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.matter_id", ondelete="SET NULL"), index=True)
    media_type: Mapped[str] = mapped_column(String(16))
    width: Mapped[int]
    height: Mapped[int]
    byte_size: Mapped[int]
    caption: Mapped[str] = mapped_column(String(300), default="")
    original: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    thumbnail: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
