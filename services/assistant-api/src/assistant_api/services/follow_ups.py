"""Follow existing records using the same local OpenAPI calls as Assistant chat."""
from datetime import UTC, datetime, timedelta
from sqlalchemy import select, update
from assistant_api.models import FollowUp, FollowUpExecution, Matter, User
from assistant_api.core.errors import ConflictError, InvalidRequestError, NotFoundError
from assistant_api.services.api_operations import OperationCatalog, execute_call, read_result_pointer


def aware(value):
    """Restore UTC awareness on SQLite timestamps."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def owned_rule(db, user_id, rule_id):
    """Read one rule owned by the executing account."""
    row = await db.scalar(select(FollowUp).where(FollowUp.follow_up_id==rule_id,FollowUp.user_id==user_id))
    if row is None:
        raise NotFoundError('follow_up_not_found','没有找到这条跟进。')
    return row


def rule_read(row):
    """Expose the rule independently from its individual executions."""
    return {'follow_up_id':row.follow_up_id,'matter_id':row.matter_id,'title':row.title,
            'instruction':row.instruction,'execution_mode':row.execution_mode,'state':row.state,'version':row.version,
            'references':row.references_json,'trigger':row.trigger_json,'stop_when':row.stop_when_json,
            'next_trigger_at':row.next_trigger_at,'created_at':row.created_at,'updated_at':row.updated_at}


async def read_references(app, user_id, references):
    """Read owned records through published read effects, including JSON POST queries, without a handler registry."""
    catalog = OperationCatalog(app)
    values = {}
    for reference in references:
        name = reference['name']
        if name in values:
            raise InvalidRequestError('follow_up_reference_name','记录引用名称重复。')
        operation, call = catalog.validate_call({k:reference[k] for k in ('operation_id','path_parameters','query_parameters','body') if k in reference})
        if operation.effect!='read':
            raise InvalidRequestError('follow_up_read_operation','记录引用需要查询接口。')
        receipt = await execute_call(app,'',operation,call,'follow-up-read',execution_user_id=user_id)
        if receipt['status']!='succeeded':
            raise NotFoundError('follow_up_record_not_found','没有找到关联记录。')
        selected = read_result_pointer(receipt['data'],reference.get('result_pointer',''))
        if isinstance(selected,list):
            match=reference.get('match',{})
            rows=[row for row in selected if isinstance(row,dict) and match and all(row.get(k)==v for k,v in match.items())]
            if len(rows)!=1:
                raise InvalidRequestError('follow_up_record_selection','请指定要跟进的那条记录。')
            selected=rows[0]
        if not isinstance(selected,dict):
            raise InvalidRequestError('follow_up_record_type','关联记录需要是一个对象。')
        values[name]=selected
    return values


def reference_value(values, source):
    """Resolve a condition field inside a named current record."""
    if source['reference'] not in values:
        raise InvalidRequestError('follow_up_reference_missing','没有找到关联记录名称。')
    return read_result_pointer(values[source['reference']],source['field'])


def condition_met(values, conditions):
    """Stop when any explicitly supplied completion condition holds."""
    return bool(conditions) and any((reference_value(values,c)==c['value']) if c['operator']=='eq' else (reference_value(values,c)!=c['value']) for c in conditions)


def trigger_time(trigger, values):
    """Compute current due time from an absolute time or actual record field."""
    if trigger['kind']!='time':
        return None
    raw = trigger.get('at') or reference_value(values,trigger['source'])
    if not raw:
        raise InvalidRequestError('follow_up_time_missing','请补充跟进时间。')
    at = raw if isinstance(raw,datetime) else datetime.fromisoformat(raw.replace('Z','+00:00'))
    if at.tzinfo is None:
        raise InvalidRequestError('follow_up_timezone','请提供带时区的跟进时间。')
    return at+timedelta(seconds=trigger.get('offset_seconds',0))


def observed_values(trigger, values):
    """Retain only subscribed field values to compare record changes, not record copies."""
    if trigger['kind']!='record_changed':
        return {}
    return {field:reference_value(values,{'reference':trigger['reference'],'field':field}) for field in trigger['fields']}


async def create_rule(app, db, user, payload):
    """Create a rule once after resolving current owned records through OpenAPI."""
    existing = await db.scalar(select(FollowUp).where(FollowUp.user_id==user.user_id,FollowUp.idempotency_key==payload.idempotency_key))
    if existing:
        if existing.creation_json != payload.model_dump(mode='json'):
            raise ConflictError('idempotency_conflict','这次请求标识已经使用。')
        return rule_read(existing)
    matter=await db.scalar(select(Matter).where(Matter.matter_id==payload.matter_id,Matter.user_id==user.user_id,Matter.deleted_at.is_(None)))
    if matter is None:
        raise NotFoundError('matter_not_found','没有找到这个事项。')
    from sqlalchemy import func
    if await db.scalar(select(func.count()).select_from(FollowUp).where(FollowUp.user_id==user.user_id, FollowUp.state!='closed')) >= 100:
        raise InvalidRequestError('follow_up_limit','请先结束不再需要的跟进。')
    refs=[r.model_dump(mode='json') for r in payload.references]
    values=await read_references(app,user.user_id,refs)
    trigger=payload.trigger.model_dump(mode='json',exclude_none=True)
    conditions=[c.model_dump(mode='json') for c in payload.stop_when]
    closed=condition_met(values,conditions)
    row=FollowUp(execution_mode=payload.execution_mode,creation_json=payload.model_dump(mode='json'),user_id=user.user_id,matter_id=payload.matter_id,idempotency_key=payload.idempotency_key,title=payload.title,instruction=payload.instruction,references_json=refs,trigger_json=trigger,stop_when_json=conditions,state='closed' if closed else 'enabled',next_trigger_at=None if closed else trigger_time(trigger,values),observed_json=observed_values(trigger,values))
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return rule_read(row)


async def patch_rule(app, db, user, rule_id, payload):
    """Update current rule parameters and stop pending executions when paused or closed."""
    row=await owned_rule(db,user.user_id,rule_id)
    if row.version!=payload.expected_version:
        raise ConflictError('follow_up_version_changed','跟进安排有更新，请重新读取。')
    changes=payload.model_dump(mode='json',exclude_unset=True)
    refs=changes.get('references',row.references_json)
    trigger=changes.get('trigger',row.trigger_json)
    conditions=changes.get('stop_when',row.stop_when_json)
    if ('state' in changes and changes['state'] is None) or refs is None or trigger is None or conditions is None:
        raise InvalidRequestError('follow_up_parameters','请提供完整的跟进参数。')
    state=changes.get('state',row.state)
    values=await read_references(app,user.user_id,refs) if state=='enabled' else {}
    if state=='enabled' and condition_met(values,conditions):
        state='closed'
    updates={'version':row.version+1,'state':state,'references_json':refs,'trigger_json':trigger,'stop_when_json':conditions,'observed_json':observed_values(trigger,values) if state=='enabled' else row.observed_json,'next_trigger_at':trigger_time(trigger,values) if state=='enabled' else None}
    for field in ('title','instruction','execution_mode'):
        if field in changes:
            if changes[field] is None:
                raise InvalidRequestError('follow_up_parameters','请填写跟进内容。')
            updates[field]=changes[field]
    result=await db.execute(update(FollowUp).where(FollowUp.follow_up_id==rule_id,FollowUp.version==payload.expected_version).values(**updates).execution_options(synchronize_session=False))
    if result.rowcount!=1:
        await db.rollback()
        raise ConflictError('follow_up_version_changed','跟进安排有更新，请重新读取。')
    if state!='enabled':
        await db.execute(update(FollowUpExecution).where(FollowUpExecution.follow_up_id==rule_id,FollowUpExecution.state.in_(('queued','waiting_user'))).values(state='skipped'))
    await db.commit()
    await db.refresh(row)
    return rule_read(row)


async def scheduler_tick(app, *, now=None, scheduler_id=None):
    """Enqueue due or changed rules and refresh record-relative times from committed events."""
    from assistant_api.models import BusinessChangeEvent
    from sqlalchemy.exc import IntegrityError
    now=aware(now or datetime.now(UTC))
    count=0
    from assistant_api.commute.services.leases import acquire_lease
    from uuid import uuid4
    if not scheduler_id:
        if not hasattr(app.state,'follow_up_scheduler_id'):
            app.state.follow_up_scheduler_id='followup:'+str(uuid4())
        scheduler_id=app.state.follow_up_scheduler_id
    async with app.state.session_factory() as db:
        if not await acquire_lease(db,name='assistant-follow-up-scheduler',owner=scheduler_id,now=now,ttl_seconds=300):
            return 0
        events=list(await db.scalars(select(BusinessChangeEvent).where(BusinessChangeEvent.processed_at.is_(None)).order_by(BusinessChangeEvent.created_at).limit(200)))
        changed_users=set(dict.fromkeys(e.user_id for e in events))
        due_rows=list(await db.scalars(select(FollowUp).where(FollowUp.state=='enabled',FollowUp.next_trigger_at<=now).order_by(FollowUp.next_trigger_at).limit(200)))
        changed_rows=list(await db.scalars(select(FollowUp).where(FollowUp.state=='enabled',FollowUp.user_id.in_(changed_users)))) if changed_users else []
        rows=list({row.follow_up_id:row for row in due_rows+changed_rows}.values())
        for row in rows:
            matter=await db.get(Matter,row.matter_id)
            if matter is None or matter.deleted_at or matter.status not in ('active','waiting'):
                row.state='paused';row.next_trigger_at=None
                await db.execute(update(FollowUpExecution).where(FollowUpExecution.follow_up_id==row.follow_up_id,FollowUpExecution.state.in_(('queued','waiting_user'))).values(state='skipped'))
                continue
            try:
                values=await read_references(app,row.user_id,row.references_json)
                if condition_met(values,row.stop_when_json):
                    row.state='closed';row.next_trigger_at=None
                    await db.execute(update(FollowUpExecution).where(FollowUpExecution.follow_up_id==row.follow_up_id,FollowUpExecution.state.in_(('queued','waiting_user'))).values(state='skipped'))
                    continue
                trigger=row.trigger_json
                due=trigger_time(trigger,values)
                observed=observed_values(trigger,values)
                causes = {e.cause_execution_id for e in events if e.user_id == row.user_id and e.cause_execution_id}
                own_causes = set(await db.scalars(select(FollowUpExecution.execution_id).where(FollowUpExecution.follow_up_id == row.follow_up_id, FollowUpExecution.execution_id.in_(causes)))) if causes else set()
                external = any(e.user_id == row.user_id and e.cause_execution_id not in own_causes for e in events)
                changed=trigger['kind']=='record_changed' and observed!=row.observed_json and external
                previous_due=aware(row.next_trigger_at) if row.next_trigger_at else None
                if previous_due != (aware(due) if due else None):
                    row.version += 1
                    await db.execute(update(FollowUpExecution).where(FollowUpExecution.follow_up_id==row.follow_up_id,FollowUpExecution.state.in_(('queued','waiting_user'))).values(state='skipped'))
                row.observed_json=observed
                row.next_trigger_at=due
                if not changed and (due is None or aware(due)>now):
                    continue
                key=('time:'+str(row.version)+':'+aware(due).isoformat()) if due else ('change:'+str(row.version)+':'+(events[-1].event_id if events else 'initial'))
                exists=await db.scalar(select(FollowUpExecution.execution_id).where(FollowUpExecution.follow_up_id==row.follow_up_id,FollowUpExecution.trigger_key==key))
                if exists:
                    continue
                active=await db.scalar(select(FollowUpExecution.execution_id).where(FollowUpExecution.follow_up_id==row.follow_up_id,FollowUpExecution.state.in_(('queued','running','waiting_user'))))
                if active:
                    continue
                async with db.begin_nested():
                    db.add(FollowUpExecution(user_id=row.user_id,follow_up_id=row.follow_up_id,trigger_key=key,rule_version=row.version))
                    await db.flush()
                row.last_trigger_at=now
                count+=1
            except IntegrityError:
                continue
            except NotFoundError:
                row.state='closed';row.next_trigger_at=None
        for event_row in events:
            event_row.processed_at=now
        await db.commit()
    return count


async def execution_is_current(db, request_key, *, lease_owner=None):
    """Allow another step only while this execution still owns the current active rule."""
    if not request_key.startswith('followup:'):
        return False
    execution = await db.get(FollowUpExecution, request_key.removeprefix('followup:'))
    if execution is not None:
        await db.refresh(execution)
    if execution is None or execution.state != 'running' or (lease_owner and execution.lease_owner != lease_owner):
        return False
    rule = await db.get(FollowUp, execution.follow_up_id)
    if rule is None:
        return False
    await db.refresh(rule)
    matter = await db.get(Matter, rule.matter_id)
    if matter is not None:
        await db.refresh(matter)
    return rule.state == 'enabled' and rule.version == execution.rule_version and matter is not None and not matter.deleted_at and matter.status in ('active','waiting')


async def continue_execution(db, user_id, parent_run_id, result):
    """Link a user's clarification answer back to the waiting background execution."""
    execution = await db.scalar(select(FollowUpExecution).where(FollowUpExecution.user_id == user_id, FollowUpExecution.local_run_id == parent_run_id, FollowUpExecution.state == 'waiting_user'))
    if execution:
        execution.local_run_id = result['local_run_id']
        execution.result_json = result
        execution.state = 'waiting_user' if result['outcome'] == 'needs_clarification' else 'succeeded' if result['outcome'] == 'answer' else 'unknown'
        await db.commit()


async def recover_executions(app, *, now):
    """Requeue expired planning turns; retain ambiguous already-dispatched calls for result lookup."""
    from assistant_api.models import NexusRun
    async with app.state.session_factory() as db:
        rows=list(await db.scalars(select(FollowUpExecution).where(FollowUpExecution.state=='running',FollowUpExecution.lease_expires_at<=now).limit(100)))
        for execution in rows:
            run=await db.scalar(select(NexusRun).where(NexusRun.user_id==execution.user_id,NexusRun.idempotency_key=='matter-intake:followup:'+execution.execution_id))
            flow=(run.context_json or {}).get('api_flow',{}) if run else {}
            execution.state='unknown' if flow.get('state') in ('executing','unknown') else 'queued'
            if execution.state == 'unknown' and run:
                import copy
                context=copy.deepcopy(run.context_json or {})
                resolution=dict(context.get('unified_intake_result') or {})
                resolution.update(outcome='unknown',source='follow_up',follow_up_execution_id=execution.execution_id,follow_up_id=execution.follow_up_id)
                context['unified_intake_result']=resolution
                run.context_json=context
            execution.local_run_id=run.local_run_id if run else None
            execution.lease_owner=None
            execution.lease_expires_at=None
        await db.commit()


async def renew_execution(app, execution_id, worker_id):
    """Keep a running worker's persisted claim alive during a slow model call."""
    async with app.state.session_factory() as db:
        await db.execute(update(FollowUpExecution).where(FollowUpExecution.execution_id==execution_id,FollowUpExecution.state=='running',FollowUpExecution.lease_owner==worker_id).values(lease_expires_at=datetime.now(UTC)+timedelta(minutes=10)))
        await db.commit()


async def worker_tick(app, *, now=None, worker_id='follow-up-worker'):
    """Claim one trigger and publish its answer or clarification through the existing intake flow."""
    import asyncio
    import logging
    from assistant_api.services.execution_context import ExecutionContext
    from assistant_api.services import operation_intake
    from assistant_api.services.business_events import cause_execution_id
    from assistant_api.models import NexusRun
    now=aware(now or datetime.now(UTC))
    await recover_executions(app,now=now)
    async with app.state.session_factory() as db:
        execution=await db.scalar(select(FollowUpExecution).where(FollowUpExecution.state=='queued').order_by(FollowUpExecution.created_at).limit(1))
        if execution is None:
            return False
        execution_id=execution.execution_id
        claimed=await db.execute(update(FollowUpExecution).where(FollowUpExecution.execution_id==execution_id,FollowUpExecution.state=='queued').values(state='running',lease_owner=worker_id,lease_expires_at=now+timedelta(minutes=10)).execution_options(synchronize_session=False))
        await db.commit()
        if claimed.rowcount!=1:
            return False
        await db.refresh(execution)
        rule=await db.get(FollowUp,execution.follow_up_id)
        if not await execution_is_current(db,'followup:'+execution_id,lease_owner=worker_id):
            execution.state='skipped';await db.commit();return True
        user=await db.get(User,rule.user_id)
        token=cause_execution_id.set(execution_id)
        stop=asyncio.Event()
        heartbeat=asyncio.create_task(execution_heartbeat(app,execution_id,worker_id,stop))
        try:
            values=await read_references(app,rule.user_id,rule.references_json)
            if condition_met(values,rule.stop_when_json):
                rule.state='closed';rule.next_trigger_at=None;execution.state='skipped';await db.commit();return True
            context=ExecutionContext(app,user.user_id,source='follow_up',follow_up_execution_id=execution_id,lease_owner=worker_id,follow_up_id=rule.follow_up_id)
            result=await operation_intake.intake(context,db,user,text=rule.instruction,idempotency_key='followup:'+execution_id,matter_id=rule.matter_id)
            await db.refresh(rule)
            await db.refresh(execution)
            if execution.lease_owner != worker_id:
                return True
            execution.local_run_id=result['local_run_id']
            execution.result_json=result
            execution.state={'needs_clarification':'waiting_user','answer':'succeeded','failed':'failed','unknown':'unknown','cancelled':'skipped'}.get(result['outcome'],'queued')
            if rule.state!='enabled' or rule.version!=execution.rule_version:
                execution.state='skipped'
            if execution.state == 'succeeded' and rule.idempotency_key.startswith('commute:'):
                rule.state='closed';rule.next_trigger_at=None
            execution.lease_owner=None;execution.lease_expires_at=None
            await db.commit()
        except Exception as exc:
            await db.rollback()
            execution=await db.get(FollowUpExecution,execution_id)
            if execution.lease_owner == worker_id:
                run=await db.scalar(select(NexusRun).where(NexusRun.user_id==execution.user_id,NexusRun.idempotency_key=='matter-intake:followup:'+execution_id))
                execution.local_run_id=run.local_run_id if run else None
                execution.state='unknown' if getattr(exc,'status_code',500)>=500 else 'failed'
                execution.error_code=getattr(exc,'code',type(exc).__name__)[:80]
                execution.lease_owner=None;execution.lease_expires_at=None
                await db.commit()
            logging.getLogger(__name__).warning('follow_up_failed execution=%s type=%s',execution_id,type(exc).__name__)
        finally:
            stop.set()
            await heartbeat
            cause_execution_id.reset(token)
    return True


async def execution_heartbeat(app, execution_id, worker_id, stop):
    """Renew a single active execution every thirty seconds until the worker exits."""
    import asyncio
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(),timeout=30)
        except TimeoutError:
            await renew_execution(app,execution_id,worker_id)
