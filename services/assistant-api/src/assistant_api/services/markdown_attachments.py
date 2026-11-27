"""Parse Markdown into inert text, headings and task items for Assistant."""
import re
import unicodedata
from pathlib import PurePosixPath
from typing import Literal

from markdown_it import MarkdownIt
from sqlalchemy import select
from sqlalchemy.orm import undefer

from assistant_api.core.errors import InvalidRequestError, NotFoundError
from assistant_api.models import MarkdownAttachment

MAX_MARKDOWN_BYTES = 200 * 1024
MAX_MARKDOWN_COUNT = 50
EXCERPT_CHARS = 1600
STRUCTURE_CHARS = 1600
PAGE_CHARS = 2400
Section = Literal["headings", "tasks", "text"]
PARSER = MarkdownIt("commonmark")
TASK_MARKER = re.compile(r"^\[([ xX])\][ \t\n]+")
INVALID_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def parse_markdown(filename: str, raw: bytes) -> dict:
    """Validate the file and extract complete structure without rendering any HTML."""
    if (not filename or len(filename) > 255 or "/" in filename or "\\" in filename
            or any(unicodedata.category(char).startswith("C") for char in filename)
            or PurePosixPath(filename).suffix.lower() not in {".md", ".markdown"}):
        raise InvalidRequestError("markdown_format_invalid", "请选择以 .md 或 .markdown 结尾的文件。")
    if len(raw) > MAX_MARKDOWN_BYTES:
        raise InvalidRequestError("markdown_too_large", "这个文件太大了，请缩短后再上传。")
    try:
        original = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvalidRequestError("markdown_encoding_invalid", "读不了这个文件，请另存为纯文本后再上传。") from exc
    if INVALID_CHARACTERS.search(original):
        raise InvalidRequestError("markdown_encoding_invalid", "读不了这个文件，请另存为纯文本后再上传。")
    if not original.strip():
        raise InvalidRequestError("markdown_empty", "这个文件是空的，请换一个。")

    tokens = PARSER.parse(original)
    headings, tasks = [], []
    open_items: list[tuple[int, bool]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            headings.append({"level": int(token.tag[1:]), "text": tokens[index + 1].content.strip()})
        elif token.type == "list_item_open":
            open_items.append((token.level, False))
            if tokens[index + 1].type != "paragraph_open":
                open_items[-1] = (token.level, True)
        elif token.type == "list_item_close":
            open_items.pop()
        elif token.type == "paragraph_open" and open_items:
            level, seen_paragraph = open_items[-1]
            if seen_paragraph or token.level != level + 1:
                continue
            open_items[-1] = (level, True)
            content = tokens[index + 1].content
            if match := TASK_MARKER.match(content):
                tasks.append({"checked": match[1].lower() == "x", "text": content[match.end():],
                              "heading": len(headings) or None})
    title = next((item["text"] for item in headings if item["level"] == 1 and item["text"]),
                 PurePosixPath(filename).stem)
    return {"title": title, "headings": headings, "tasks": tasks, "text_length": len(original)}


def _task_order(item: dict) -> bool:
    """Offer unfinished tasks first and preserve order within each group."""
    return item["checked"]


def _structure_page(items: list[dict], position: int, limit: int) -> dict:
    """Page structured text by character position, including unusually long individual entries."""
    page = []
    left = limit
    offset = 0
    for index, item in enumerate(items, start=1):
        text = item["text"]
        cost = len(text) + 96
        if position >= offset + cost:
            offset += cost
            continue
        start = max(0, position - offset)
        if left <= 96:
            break
        end = min(len(text), start + left - 96)
        page.append({**item, "number": index, "text": text[start:end],
                     **({"text_offset": start, "text_length": len(text)} if start or end < len(text) else {})})
        left -= end - start + 96
        if end < len(text):
            return {"items": page, "next_position": offset + end, "count": len(items)}
        offset += cost
        position = offset
    return {"items": page, "next_position": offset if len(page) and page[-1]["number"] < len(items) else None,
            "count": len(items)}


def markdown_page(document: dict, original: str, section: Section, position: int, *, limit: int = PAGE_CHARS) -> dict:
    """Return a bounded page without discarding the rest of the saved document."""
    if section == "text":
        end = position + limit
        return {"text": original[position:end], "next_position": end if end < len(original) else None,
                "count": len(original)}
    items = document["headings"] if section == "headings" else sorted(document["tasks"], key=_task_order)
    return _structure_page(items, position, limit)


def project_markdown(document: dict, original: str) -> dict:
    """Prioritize headings and unfinished tasks ahead of the excerpt in the first message."""
    headings = markdown_page(document, original, "headings", 0, limit=STRUCTURE_CHARS)
    tasks = markdown_page(document, original, "tasks", 0, limit=STRUCTURE_CHARS)
    excerpt = markdown_page(document, original, "text", 0, limit=EXCERPT_CHARS)
    complete_tasks = sum(item["checked"] for item in document["tasks"])
    return {"title": document["title"][:255], "headings": headings["items"], "tasks": tasks["items"],
            "heading_count": headings["count"], "task_count": tasks["count"],
            "unfinished_task_count": tasks["count"] - complete_tasks, "text_length": document["text_length"],
            "excerpt": excerpt["text"],
            "next_positions": {name: page["next_position"] for name, page in
                              (("headings", headings), ("tasks", tasks), ("text", excerpt))},
            "is_truncated": any(page["next_position"] is not None for page in (headings, tasks, excerpt))}


async def owned_markdown(db, user_id: str, attachment_id: str, *, load_original: bool = False) -> MarkdownAttachment:
    """Select only the authenticated user's attachment, even when an ID exists for someone else."""
    query = select(MarkdownAttachment).where(MarkdownAttachment.user_id == user_id,
                                              MarkdownAttachment.attachment_id == attachment_id)
    if load_original:
        query = query.options(undefer(MarkdownAttachment.original))
    row = await db.scalar(query)
    if row is None:
        raise NotFoundError("attachment_not_found", "找不到这个文件。")
    return row


def public_markdown(row: MarkdownAttachment) -> dict:
    """Expose display fields without exposing the original or the complete parse in lists."""
    return {"kind": "markdown", "attachment_id": row.attachment_id, "filename": row.filename,
            "title": row.document["title"][:255], "byte_size": row.byte_size,
            "completed_count": sum(task["checked"] for task in row.document["tasks"]),
            "unfinished_count": sum(not task["checked"] for task in row.document["tasks"])}
