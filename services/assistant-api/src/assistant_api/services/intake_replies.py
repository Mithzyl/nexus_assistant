"""Manage user clarification independently of Nexus execution status."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from assistant_api.core.errors import NotFoundError
from assistant_api.models import MatterDraft, NexusRun
from assistant_api.services import matter_plan

REPLY_TTL = timedelta(minutes=30)
IMPLICIT_REPLY_WINDOW = timedelta(minutes=10)


def utc(value: datetime) -> datetime:
    """Interpret SQLite's timezone-free stored timestamps as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def reply_metadata(row: NexusRun, result: dict, now: datetime | None = None) -> dict:
    """Expose current reply state, including expiry of existing clarification records."""
    if (row.status != 'succeeded' or result.get('source') == 'follow_up'
            or ((row.context_json or {}).get('unified_intake_result') or {}).get('outcome') == 'needs_confirmation'
            or result.get('outcome') != 'needs_clarification' or not result.get('clarify')):
        return {}
    expires = utc(row.reply_expires_at or (utc(row.completed_at or row.created_at) + REPLY_TTL))
    state = row.reply_state or 'awaiting_reply'
    if state == 'awaiting_reply' and expires <= (now or datetime.now(UTC)):
        state = 'expired'
    return {'reply_state': state, 'reply_expires_at': expires.isoformat(),
            'continuation_run_id': row.local_run_id, 'clarification_summary': result['clarify'][:240]}


async def update_reply_state(db, user_id: str, run_id: str, state: str) -> bool:
    """Close one owned clarification without reopening one already closed by another request."""
    changed = await db.execute(update(NexusRun).where(
        NexusRun.local_run_id == run_id, NexusRun.user_id == user_id,
        NexusRun.purpose == matter_plan.PURPOSE,
        (NexusRun.reply_state.is_(None) | (NexusRun.reply_state == 'awaiting_reply')),
    ).values(reply_state=state).execution_options(synchronize_session=False))

    return changed.rowcount == 1


async def retire_capture_preview(db, row: NexusRun, *, replacement_draft_id: str | None = None) -> None:
    """Close only a superseded unconfirmed capture preview, leaving its history intact."""
    flow = (row.context_json or {}).get('api_flow') or {}
    previous = (flow.get('capture_conflict') or {}).get('draft_id')
    if previous and previous != replacement_draft_id:
        await db.execute(update(MatterDraft).where(
            MatterDraft.draft_id == previous, MatterDraft.user_id == row.user_id,
            MatterDraft.status == 'preview',
        ).values(status='cancelled', cancelled_at=datetime.now(UTC))
           .execution_options(synchronize_session=False))


async def dismiss_reply(db, user, run_id: str, *, commit: bool = True) -> None:
    """Persist cancellation of an owned clarification and leave its text in history."""
    from assistant_api.services.operation_intake import response_for

    row = await db.scalar(select(NexusRun).where(
        NexusRun.local_run_id == run_id, NexusRun.user_id == user.user_id,
        NexusRun.purpose == matter_plan.PURPOSE,
    ))
    if row is None or not response_for(row).get('reply_state'):
        raise NotFoundError('intake_reply_not_found', '找不到可取消的追问。')
    if await update_reply_state(db, user.user_id, run_id, 'cancelled'):
        await retire_capture_preview(db, row)
    if commit:
        await db.commit()


async def record_terminal_reply(db, row: NexusRun, final_text: str) -> None:
    """Project the validated final reply and close the server-selected parent in the same commit."""
    from assistant_api.services.api_operations import OperationDecision
    from assistant_api.services.operation_intake import response_for

    result = response_for(row)
    if result.get('source') != 'chat' or result.get('outcome') not in {'answer', 'needs_clarification'}:
        return
    if result.get('reply_state') == 'awaiting_reply' and row.reply_expires_at is None:
        await db.flush()
        await db.execute(update(NexusRun).where(
            NexusRun.local_run_id == row.local_run_id, NexusRun.reply_state.is_(None),
        ).values(reply_state='awaiting_reply', reply_expires_at=utc(row.completed_at) + REPLY_TTL)
           .execution_options(synchronize_session=False))

    context = row.context_json
    pending = context.get('api_flow', {}).get('pending_reply')
    parent_id = context.get('parent_run_id')
    if parent_id or pending:
        from assistant_api.services.operation_intake import get_conversation_run

        decision = OperationDecision.model_validate_json(final_text)
        if decision.pending_reply_disposition == 'new_topic':
            candidate = parent_id or pending['source_run_id']
            parent = await get_conversation_run(db, row.user_id, candidate)
            if parent and response_for(parent).get('reply_state') in {'awaiting_reply', 'expired'}:
                if await update_reply_state(db, row.user_id, candidate, 'cancelled'):
                    await retire_capture_preview(db, parent)
            row.context_json = {**context, 'parent_run_id': None}
            return
        if pending and decision.pending_reply_disposition == 'answered':
            parent_id = pending['source_run_id']
            row.context_json = {**context, 'parent_run_id': parent_id}

    if parent_id:
        # Never close an ordinary historical answer or an automatic follow-up as a user clarification.
        from assistant_api.services.operation_intake import get_conversation_run

        parent = await get_conversation_run(db, row.user_id, parent_id)
        if parent and response_for(parent).get('reply_state') in {'awaiting_reply', 'expired'}:
            if await update_reply_state(db, row.user_id, parent_id, 'resolved'):
                replacement = ((context.get('api_flow') or {}).get('capture_conflict') or {}).get('draft_id')
                await retire_capture_preview(db, parent, replacement_draft_id=replacement)
