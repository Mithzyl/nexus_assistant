"""Private originals and deterministic parses for uploaded Markdown files."""
from sqlalchemy import ForeignKey, JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from assistant_api.models.base import Base, TimestampMixin, new_id


class MarkdownAttachment(TimestampMixin, Base):
    """Store the original file and the complete parse under its authenticated owner."""

    __tablename__ = "markdown_attachments"
    attachment_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id", ondelete="CASCADE"), index=True)
    matter_id: Mapped[str | None] = mapped_column(ForeignKey("matters.matter_id", ondelete="SET NULL"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    byte_size: Mapped[int]
    original: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    document: Mapped[dict] = mapped_column(JSON)
