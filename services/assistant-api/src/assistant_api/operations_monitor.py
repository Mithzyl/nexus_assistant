"""Sample Assistant's SQLite state into private JSON logs without touching business rows."""
import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import shutil
import signal
import sqlite3
from threading import Event

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from assistant_api.core.config import Settings, get_settings
from assistant_api.core.observability import emit_event

REQUIRED_MIGRATION = '0032_intake_capture'
STATUSES = (
    'queued','pending','ready','running','sending','processing','dispatching','awaiting_answer',
    'waiting_user','completed','succeeded','failed','unknown','cancelled','skipped','accepted',
    'displayed','expired','suppressed','delivered','timed_out','superseded','sent',
)
BUSINESS_COUNTS = (
    'users','matters','matter_todos','schedules','reminders','assistant_memory_entries',
    'user_availability','next_action_states',
)
STALE_WITHOUT_LEASE_SECONDS = 900
MIN_FREE_BYTES = 512 * 1024**2


@dataclass(frozen=True)
class Queue:
    """Describe owned, trusted state and timing columns for one persisted queue."""

    table: str
    column: str = 'status'
    ready: tuple = ('queued',)
    in_progress: tuple = ('running',)
    ready_time: str = 'created_at'
    lease: str | None = None


QUEUES = {
    'follow_up':Queue('follow_up_executions',column='state',lease='lease_expires_at'),
    'supplement_analysis':Queue('matter_supplements',column='ai_status',lease='lease_expires_at'),
    'memory_after_answer':Queue('assistant_memory_operations',ready=('ready',),in_progress=(),ready_time='updated_at'),
    'commute_check':Queue('commute_checks',ready_time='COALESCE(next_attempt_at,created_at)',lease='lease_expires_at'),
    'commute_notification':Queue('notification_outbox',ready=('pending',),in_progress=('sending',),
                                 ready_time='next_attempt_at',lease='lease_expires_at'),
    'browser_push':Queue('browser_push_deliveries',ready=('pending',),in_progress=('sending',)),
    'assistant_agent':Queue('nexus_runs',ready=('pending',)),
    'agent_response':Queue('agent_response_receipts',ready=(),in_progress=('processing','dispatching')),
}


def _sql_values(values):
    """Quote only source-controlled state names for SQLite predicates and handle empty sets."""
    return ','.join(f"'{value}'" for value in values) or 'NULL'


def _age(seconds):
    """Return nonnegative whole seconds, preserving the distinction between no task and zero age."""
    return None if seconds is None else max(0, round(seconds))


def _queue(db, definition, now, *, enabled):
    """Aggregate statuses and lag without selecting any payload, user ID or record identifier."""
    q = definition
    state = q.column
    ready = f"{state} IN ({_sql_values(q.ready)})"
    in_progress = f"{state} IN ({_sql_values(q.in_progress)})"
    expired = (f"({q.lease} IS NOT NULL AND julianday({q.lease}) <= julianday(:now)) OR " if q.lease else '')
    no_lease = f"{q.lease} IS NULL AND " if q.lease else ''
    fallback = f"{no_lease}julianday(updated_at) <= julianday(:now)-{STALE_WITHOUT_LEASE_SECONDS}/86400.0"
    normalized_state = f"CASE WHEN {state} IN ({_sql_values(STATUSES)}) THEN {state} ELSE 'other' END"
    rows = db.execute(f"""
        SELECT {normalized_state} AS observed_state, COUNT(*),
            SUM(CASE WHEN {ready} AND julianday({q.ready_time}) > julianday(:now) THEN 1 ELSE 0 END),
            MIN(CASE WHEN {ready} AND julianday({q.ready_time}) <= julianday(:now)
                THEN julianday({q.ready_time}) END),
            SUM(CASE WHEN {in_progress} AND ({expired}({fallback})) THEN 1 ELSE 0 END),
            SUM(CASE WHEN {state} = 'failed' AND julianday(updated_at) >= julianday(:now)-1 THEN 1 ELSE 0 END)
        FROM {q.table} GROUP BY observed_state
    """,{'now':now.isoformat()}).fetchall()
    ages = [_age((now.timestamp()/86400+2440587.5-oldest)*86400) for _,_,_,oldest,_,_ in rows if oldest is not None]
    return {'consumer_enabled':enabled,'counts':{status:count for status,count,*_ in rows},
            'scheduled_retry_count':sum(row[2] for row in rows),
            'oldest_ready_age_seconds':max(ages,default=None),'stalled_in_progress_count':sum(row[4] for row in rows),
            'failed_last_24h':sum(row[5] for row in rows)}


def _heartbeats(db, now, stale_seconds):
    """Report scheduler and worker heartbeat age without disclosing process identity."""
    result = {}
    for component in ('scheduler','worker'):
        age, = db.execute("""
            SELECT (julianday(:now)-julianday(
                (SELECT last_seen_at FROM service_heartbeats WHERE component=:component)))*86400
        """,{'now':now.isoformat(),'component':component}).fetchone()
        status = ('missing' if age is None else 'clock_skew' if age < -10 else
                  'online' if age <= stale_seconds else 'stale')
        result[component] = {'status':status,'age_seconds':_age(age)}
    return result


def _pending_rules(db, now):
    """Count due, active time rules with no current execution or recorded trigger."""
    count, oldest = db.execute("""
        SELECT COUNT(*),(julianday(:now)-MIN(julianday(f.next_trigger_at)))*86400
        FROM follow_ups f JOIN matters m ON m.matter_id=f.matter_id
        WHERE f.state='enabled' AND m.status IN ('active','waiting') AND m.deleted_at IS NULL
          AND m.user_id=f.user_id AND julianday(f.next_trigger_at)<=julianday(:now)
          AND (f.last_trigger_at IS NULL OR julianday(f.last_trigger_at)<julianday(f.next_trigger_at))
          AND NOT EXISTS (SELECT 1 FROM follow_up_executions e WHERE e.follow_up_id=f.follow_up_id
            AND e.state IN ('queued','running','waiting_user'))
    """,{'now':now.isoformat()}).fetchone()
    changes, change_age = db.execute(
        "SELECT COUNT(*),(julianday(:now)-MIN(julianday(created_at)))*86400 "
        "FROM business_change_events WHERE processed_at IS NULL", {'now':now.isoformat()}).fetchone()
    return {'due_without_execution_count':count,'oldest_due_age_seconds':_age(oldest),
            'unprocessed_change_count':changes,'oldest_unprocessed_change_age_seconds':_age(change_age)}


def _sqlite_path(url):
    """Accept a local on-disk SQLite URL and deliberately exclude URI mode or memory databases."""
    parsed = make_url(url)
    if parsed.get_backend_name() != 'sqlite' or not parsed.database or parsed.database in (':memory:', '') or parsed.query:
        return None
    if parsed.database.startswith('file:'):
        return None
    return Path(parsed.database).resolve()


def _failure(code):
    """Emit an unavailable database without representing unknown queues as empty."""
    return {'database':{'status':'unavailable','code':code},'queues':None,'heartbeats':None,'dispatch':None,
            'business_counts':None,'alerts':[{'category':'database_failure','code':code}]}


def collect(settings: Settings, now: datetime) -> dict:
    """Read a consistent local database snapshot with no writes, migrations or provider calls."""
    try:
        path = _sqlite_path(settings.database_url)
    except (ArgumentError,ValueError):
        return _failure('database_url_invalid')
    if path is None:
        return _failure('database_backend_unsupported')
    try:
        if not path.is_file():
            return _failure('database_file_unavailable')
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            versions = set()
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'").fetchone():
                versions = {row[0] for row in db.execute('SELECT version FROM schema_migrations')}
            migrations = [v[:4] for v in versions if isinstance(v,str) and re.fullmatch(r'[0-9]{4}_[a-z0-9_]+', v)]
            database = {'status':'readable','write_probe':'not_run','required_migration_applied':REQUIRED_MIGRATION in versions,
                        'latest_recorded_migration':max(migrations,default=None),'size_bytes':path.stat().st_size,
                        'disk_free_bytes':shutil.disk_usage(path.parent).free}
            alerts = []
            if not database['required_migration_applied']:
                alerts.append({'category':'database_failure','code':'required_migration_missing'})
            if database['disk_free_bytes'] < MIN_FREE_BYTES:
                alerts.append({'category':'database_failure','code':'disk_space_low'})
            result = {'database':database,'alerts':alerts,'heartbeats':None,'queues':None,'dispatch':None,'business_counts':None}
            if not database['required_migration_applied']:
                return result
            result['heartbeats'] = _heartbeats(db,now,settings.commute_heartbeat_stale_seconds)
            result['queues'] = {name:_queue(db,definition,now,enabled=(name!='browser_push' or settings.web_push_enabled))
                                for name,definition in QUEUES.items()}
            result['dispatch'] = _pending_rules(db,now)
            result['business_counts'] = {table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                                         for table in BUSINESS_COUNTS}
    except sqlite3.Error as exc:
        # SQLite exception text can contain the statement, path or database content. Use numeric engine codes only.
        engine_code = getattr(exc,'sqlite_errorcode',0) & 0xFF
        code = {sqlite3.SQLITE_CANTOPEN:'database_open_failed', sqlite3.SQLITE_BUSY:'database_busy',
                sqlite3.SQLITE_LOCKED:'database_locked', sqlite3.SQLITE_NOTADB:'database_invalid',
                sqlite3.SQLITE_CORRUPT:'database_corrupt', sqlite3.SQLITE_ERROR:'database_schema_query_failed'}.get(
                    engine_code,'database_read_failed')
        return _failure(code)
    except OSError:
        return _failure('database_file_unavailable')
    for component,state in result['heartbeats'].items():
        if state['status'] != 'online':
            alerts.append({'category':'queue_stalled','code':'heartbeat_'+state['status'],'component':component})
    for name,summary in result['queues'].items():
        ready_age = summary['oldest_ready_age_seconds']
        if summary['consumer_enabled'] and ready_age is not None and ready_age>=settings.monitor_queue_stall_seconds:
            alerts.append({'category':'queue_stalled','code':'ready_wait_exceeded','queue':name})
        if summary['stalled_in_progress_count']:
            alerts.append({'category':'queue_stalled','code':'execution_timeout','queue':name})
        if summary['counts'].get('unknown'):
            alerts.append({'category':'queue_failure','code':'outcome_unknown','queue':name})
    due_age = result['dispatch']['oldest_due_age_seconds']
    if due_age is not None and due_age>=settings.monitor_queue_stall_seconds:
        alerts.append({'category':'queue_stalled','code':'due_without_execution','queue':'follow_up'})
    change_age = result['dispatch']['oldest_unprocessed_change_age_seconds']
    if change_age is not None and change_age>=settings.monitor_queue_stall_seconds:
        alerts.append({'category':'queue_stalled','code':'record_change_unprocessed','queue':'follow_up'})
    return result


def report_once(settings, *, now=None) -> None:
    """Write one JSON snapshot with the same format for periodic sampling and diagnosis."""
    observed_at = now or datetime.now(UTC)
    data = collect(settings,observed_at)
    emit_event('operations_snapshot',now=observed_at,level='error' if any(
        alert['category']=='database_failure' for alert in data['alerts']) else 'warning' if data['alerts'] else 'info',
        automation={'commute_auto_ai_enabled':settings.commute_auto_ai_enabled,'web_push_enabled':settings.web_push_enabled},
        **data)


def main():
    """Run one shot or supervise periodic monitoring independently of both queue consumers."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    stop = Event()

    def request_stop(*_args):
        """Stop at the next sample boundary without interrupting logged JSON."""
        stop.set()

    signal.signal(signal.SIGTERM,request_stop)
    signal.signal(signal.SIGINT,request_stop)
    while not stop.is_set():
        report_once(settings)
        if args.once:
            break
        stop.wait(settings.monitor_interval_seconds)


if __name__=='__main__':
    main()
