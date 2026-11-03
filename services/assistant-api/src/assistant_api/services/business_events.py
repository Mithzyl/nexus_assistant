"""Save business record changes in the same SQLite transaction as their mutations."""
from contextvars import ContextVar
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session
from assistant_api.models import BusinessChangeEvent

cause_execution_id = ContextVar('follow_up_cause_execution', default=None)
_installed = False


def record_changes(session, flush_context, instances):
    """Append small change events for owned business records before their commit."""
    ignored = {'follow_ups','follow_up_executions','business_change_events','nexus_runs','user_asks','user_sessions','service_heartbeats','commute_leases','tool_call_receipts'}
    for row in list(session.new)+list(session.dirty)+list(session.deleted):
        state=inspect(row)
        if state.mapper.local_table.name in ignored or 'user_id' not in state.mapper.columns:
            continue
        fields=[attr.key for attr in state.mapper.column_attrs if state.attrs[attr.key].history.has_changes()]
        if not fields:
            continue
        owner=getattr(row,'user_id',None)
        if not owner:
            continue
        key=state.mapper.primary_key[0].key
        session.add(BusinessChangeEvent(user_id=owner,resource=state.mapper.local_table.name,resource_id=str(getattr(row,key,None) or ''),changed_fields_json=fields,cause_execution_id=cause_execution_id.get()))


def record_bulk_changes(orm_state):
    """Capture affected owned rows before ORM bulk updates or deletes in the same transaction."""
    if not (orm_state.is_update or orm_state.is_delete):
        return
    table = orm_state.statement.table
    if 'user_id' not in table.c or table.name in {'follow_ups','follow_up_executions','business_change_events','nexus_runs','user_asks','user_sessions'}:
        return
    from sqlalchemy import select
    primary = list(table.primary_key.columns)
    if not primary:
        return
    rows = orm_state.session.connection().execute(select(table.c.user_id, primary[0]).where(*orm_state.statement._where_criteria)).fetchall()
    for owner, record_id in rows:
        if owner:
            orm_state.session.add(BusinessChangeEvent(user_id=owner, resource=table.name, resource_id=str(record_id), changed_fields_json=['*'], cause_execution_id=cause_execution_id.get()))


def install_change_events():
    """Register the shared transaction hook once for API and standalone worker processes."""
    global _installed
    if not _installed:
        event.listen(Session,'before_flush',record_changes)
        event.listen(Session,'do_orm_execute',record_bulk_changes)
        _installed=True
