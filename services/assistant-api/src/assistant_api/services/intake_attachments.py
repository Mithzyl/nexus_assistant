"""Authorize attachment references and label supplied document contents for intake."""
from assistant_api.core.errors import NotFoundError
from assistant_api.services import image_attachments, markdown_attachments as markdown

IMAGE_SAVED = "图片已保存，暂未开启自动识图。"


async def collect_attachments(db, user_id: str, refs: list[dict], matter_id: str | None, *,
                              include_document: bool = False) -> tuple[list[dict], list[dict]]:
    """Check ownership and matter scope; load document contents only before model dispatch."""
    metadata, documents = [], []
    for ref in refs:
        attachment_id = ref["attachment_id"]
        if ref["kind"] == "image":
            row = await image_attachments.owned_attachment(db, user_id, attachment_id)
            public = {"kind": "image", "attachment_id": attachment_id}
        else:
            row = await markdown.owned_markdown(db, user_id, attachment_id, load_original=include_document)
            public = markdown.public_markdown(row)
        if row.matter_id and row.matter_id != matter_id:
            raise NotFoundError("attachment_not_found", "找不到这个附件，或它不属于当前事项。")
        metadata.append(public)
        if include_document and ref["kind"] == "markdown":
            documents.append({"attachment_id": attachment_id, "filename": row.filename,
                              **markdown.project_markdown(row.document, row.original.decode("utf-8-sig"))})
    return metadata, documents


def can_execute_attachment_operation(flow: dict, operation, call) -> bool:
    """Allow files to produce previews and presentation, while only a person can save the proposed records."""
    if not flow.get("attachment_review") or operation.effect == "read":
        return True
    if operation.operation_id == "create_structured_draft":
        selected_matter = flow.get("matter_id")
        return not selected_matter or selected_matter == (call.body or {}).get("matter_id")
    if operation.operation_id in {"save_timeline_route_card", "save_multi_route_map_card"}:
        return True
    if operation.path == "/api/v1/matters/{matter_id}/drafts" and operation.method == "POST":
        selected_matter = flow.get("matter_id")
        return not selected_matter or selected_matter == (call.path_parameters or {}).get("matter_id")
    if operation.path == "/api/v1/matters/drafts/{draft_id}" and operation.method == "PATCH":
        return (call.path_parameters or {}).get("draft_id") in flow.get("attachment_draft_ids", [])
    return False
