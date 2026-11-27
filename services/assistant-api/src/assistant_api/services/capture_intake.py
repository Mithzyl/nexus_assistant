"""Consume accepted captures on the existing worker without starting duplicate Nexus runs."""
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update

from assistant_api.core.errors import AssistantError
from assistant_api.models import NexusRun, User
from assistant_api.nexus_client.errors import NexusError
from assistant_api.services import operation_intake, run_service
from assistant_api.services.execution_context import ExecutionContext

PREPARATION_LEASE = timedelta(minutes=2)
RECONCILE_DELAY = timedelta(minutes=2)


async def begin_dispatch(db, row: NexusRun, claim_expires: datetime) -> bool:
    """Persist network-dispatch intent before launch; a lost worker must never start another run."""
    result = await db.execute(update(NexusRun).where(
        NexusRun.local_run_id == row.local_run_id, NexusRun.status == 'pending',
        NexusRun.capture_lease_expires_at == claim_expires,
        NexusRun.capture_dispatch_started_at.is_(None),
    ).values(capture_dispatch_started_at=datetime.now(UTC)).execution_options(synchronize_session=False))
    await db.commit()
    return result.rowcount == 1


async def worker_tick(app) -> bool:
    """Claim one capture atomically and leave the existing workflow observer running independently."""
    now = datetime.now(UTC)
    async with app.state.session_factory() as db:
        eligible = and_(NexusRun.status == 'pending', NexusRun.capture_dispatch_started_at.is_(None),
            or_(NexusRun.intake_stage == 'queued',
                and_(NexusRun.capture_lease_expires_at <= now, NexusRun.intake_stage == 'understanding')))
        run_id = await db.scalar(select(NexusRun.local_run_id).where(eligible)
                                 .order_by(NexusRun.created_at, NexusRun.local_run_id).limit(1))
        if run_id is None:
            await reconcile_abandoned(app, db, now)
            return False

        expires = now + PREPARATION_LEASE
        claimed = await db.execute(update(NexusRun).where(NexusRun.local_run_id == run_id, eligible)
            .values(intake_stage='understanding', capture_lease_expires_at=expires)
            .execution_options(synchronize_session=False))
        await db.commit()
        if claimed.rowcount != 1:
            return False
        row = await db.get(NexusRun, run_id)
        user = await db.get(User, row.user_id)
        if user is None or user.status != 'active':
            await db.execute(update(NexusRun).where(NexusRun.local_run_id == run_id,
                NexusRun.capture_lease_expires_at == expires, NexusRun.capture_dispatch_started_at.is_(None))
                .values(status='failed', error_code='capture_account_unavailable', completed_at=now))
            await db.commit()
            return True
        try:
            request = ExecutionContext(app, user.user_id)
            saved = row.context_json
            flow = saved['api_flow']
            context = await operation_intake.prepare_context(request, db, user,
                text=saved['text'], idempotency_key=flow['request_key'], entry_mode=flow['entry_mode'],
                ignore_pending_reply=flow['ignore_pending_reply'], exclude_run_id=run_id,
                matter_id=flow.get('matter_id'), continuation_run_id=flow.get('reply_to_run_id'),
                attachment_refs=flow.get('submitted_attachments'))
            context['api_flow'].update(
                ignore_pending_clarification=flow.get('ignore_pending_clarification', False),
                dismissed_reply_run_id=flow.get('dismissed_reply_run_id'))
            prepared = await db.execute(update(NexusRun).where(
                NexusRun.local_run_id == run_id, NexusRun.status == 'pending',
                NexusRun.capture_lease_expires_at == expires, NexusRun.capture_dispatch_started_at.is_(None),
            ).values(context_json=context).execution_options(synchronize_session=False))
            await db.commit()
            if prepared.rowcount == 1:
                await db.refresh(row)
                await operation_intake.execute_intake(request, db, user, row, claim_expires=expires)
        except (AssistantError, NexusError):
            # Known upstream failures are normally saved by the shared launcher.
            # Do not reset any submitted work back to queued.
            await db.refresh(row)
            if row.status == 'pending':
                await db.execute(update(NexusRun).where(NexusRun.local_run_id == run_id,
                    NexusRun.status == 'pending', NexusRun.capture_lease_expires_at == expires,
                    NexusRun.capture_dispatch_started_at.is_(None))
                    .values(status='failed', error_code='capture_preparation_failed', completed_at=datetime.now(UTC)))
                await db.commit()
        return True


async def reconcile_abandoned(app, db, now: datetime) -> None:
    """Recover a known workflow read-only, or mark an interrupted dispatch as unknown."""
    settings = app.state.settings
    cutoff = now - max(RECONCILE_DELAY, timedelta(milliseconds=settings.nexus_business_wall_time_limit_ms))
    row = await db.scalar(select(NexusRun).where(
        or_(NexusRun.status.in_(['pending', 'running']),
            and_(NexusRun.status == 'unknown', NexusRun.workflow_execution_id.is_not(None))),
        NexusRun.capture_dispatch_started_at <= cutoff, NexusRun.capture_lease_expires_at <= now,
    ).order_by(NexusRun.capture_lease_expires_at, NexusRun.local_run_id).limit(1))
    if row is None or run_service.observing_workflow(row.local_run_id):
        return
    user = await db.get(User, row.user_id)
    if row.workflow_execution_id and user and user.status == 'active':
        # Space out subsequent polls and let other stalled captures reconcile.
        row.capture_lease_expires_at = now + RECONCILE_DELAY
        await db.commit()
        await run_service.refresh_run(db, app.state.nexus_client, user=user, row=row)
    elif row.status in {'pending', 'running'}:
        await db.execute(update(NexusRun).where(
            NexusRun.local_run_id == row.local_run_id,
            NexusRun.workflow_execution_id == row.workflow_execution_id,
            NexusRun.status.in_(['pending', 'running']),
        ).values(status='unknown', error_code='capture_dispatch_unknown', completed_at=now,
                 capture_lease_expires_at=None).execution_options(synchronize_session=False))
        await db.commit()
    elif not user or user.status != 'active':
        row.capture_lease_expires_at = None
        await db.commit()


async def pending_replies(db, user_id: str) -> list[dict]:
    """Return the caller's still-actionable captures, including older drafts selected explicitly."""
    rows = await db.scalars(select(NexusRun).where(
        NexusRun.user_id == user_id, NexusRun.purpose == operation_intake.matter_plan.PURPOSE,
        NexusRun.status == 'succeeded', NexusRun.reply_state == 'awaiting_reply',
        or_(NexusRun.context_json['api_flow']['entry_mode'].as_string() == 'async_capture',
            NexusRun.context_json['api_flow']['capture_review_id'].as_string().is_not(None)),
    ).order_by(NexusRun.created_at.desc(), NexusRun.local_run_id.desc()))
    captures = []
    for row in rows:
        result = operation_intake.response_for(row)
        if result.get('reply_state') in {'awaiting_reply', 'expired'}:
            pending = result['pending_context']
            captures.append({'local_run_id': row.local_run_id, 'request_text': row.context_json['text'],
                'question': result['clarify'], 'choices': pending.get('choices', []),
                'draft_id': (pending.get('capture_conflict') or {}).get('draft_id')})
    return captures
