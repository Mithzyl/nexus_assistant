"""Collect only Assistant operational JSON, persist windows and optionally store bounded Nexus summaries."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import UTC, datetime
import json
import math
from pathlib import Path
import re
import signal
import sqlite3
from threading import Event
from typing import Callable
from urllib.parse import urlsplit

import httpx

from assistant_api.core.observability import HTTP_METHODS, SAFE_CODES
from assistant_api.operations_monitor import BUSINESS_COUNTS, QUEUES, STATUSES

EVENTS = {'http_request','operations_snapshot','background_tick_failure','monitor_process_exit'}
CATEGORIES = {'interface_failure','interface_ok','queue_failure','queue_stalled','database_failure','monitor_failure'}
ALERT_CODES = {
    'ready_wait_exceeded','execution_timeout','outcome_unknown','due_without_execution','record_change_unprocessed',
    'heartbeat_missing','heartbeat_stale','heartbeat_clock_skew','required_migration_missing','disk_space_low',
    'database_open_failed','database_busy','database_locked','database_invalid','database_corrupt','database_schema_query_failed',
    'database_read_failed','database_file_unavailable','database_url_invalid','database_backend_unsupported',
}
COMPONENTS = {'worker','scheduler','agent_observer',*QUEUES}
MAX_LINE = 65536
ROUTE = re.compile(r'(?:/health|/openapi\.json|/(?:api/v1|internal)/[A-Za-z0-9_./{}-]{1,140}|unmatched)')
UUID = re.compile(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
SPOOL_FILE = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl')
SUMMARY_UNSAFE = re.compile(r'(?i)https?://|\b(?:bearer\s+|sk-|np_|pas_)\S+|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}')
SCHEMA = """
CREATE TABLE IF NOT EXISTS cursors (file TEXT PRIMARY KEY,inode INTEGER NOT NULL,offset INTEGER NOT NULL,dropping INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT,ts REAL NOT NULL,event TEXT NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_operations_event_ts ON events(ts);
CREATE TABLE IF NOT EXISTS windows (start INTEGER PRIMARY KEY,end INTEGER NOT NULL,aggregate TEXT NOT NULL,
  last_event_id INTEGER NOT NULL,ai_state TEXT NOT NULL DEFAULT 'not_requested',ai_summary TEXT,ai_error TEXT);
CREATE TABLE IF NOT EXISTS alert_state (key TEXT PRIMARY KEY,opened REAL NOT NULL);
CREATE TABLE IF NOT EXISTS alert_history (ts REAL NOT NULL,key TEXT NOT NULL,transition TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS collector_state (key TEXT PRIMARY KEY,value REAL NOT NULL);
"""


def _encode(record):
    """Serialize trusted structured content without column-dependent formatting."""
    return json.dumps(record,ensure_ascii=False,separators=(',',':'),allow_nan=False)


def _dict(value):
    """Treat malformed object fields as absent instead of passing arbitrary input through."""
    return value if isinstance(value,dict) else {}


def _number(value):
    """Accept bounded finite numbers; never coerce identifiers or user strings."""
    if type(value) is int:
        return value if 0<=value<=10**15 else None
    return value if type(value) is float and math.isfinite(value) and 0<=value<=10**15 else None


def _counts(value, keys):
    """Copy only the named numeric fields."""
    return {key:n for key in sorted(keys) if (n:=_number(_dict(value).get(key))) is not None}


def normalize_event(raw):
    """Rebuild the stored event using an explicit field list; never forward arbitrary strings."""
    if not isinstance(raw,dict) or raw.get('schema_version')!=1 or raw.get('service')!='assistant':
        return None
    event = raw.get('event')
    if not isinstance(event,str) or event not in EVENTS:
        return None
    try:
        stamp=datetime.fromisoformat(raw['timestamp'])
        if stamp.tzinfo is None or not 2020<=stamp.year<=2100:
            return None
    except (TypeError,ValueError,KeyError):
        return None
    clean={'event':event,'timestamp':stamp.astimezone(UTC).isoformat()}
    category=raw.get('category')
    if isinstance(category,str) and category in CATEGORIES:
        clean['category']=category
    if event=='http_request':
        route, method, identity, code = (raw.get(name) for name in ('route','method','request_id','error_code'))
        clean.update(_counts(raw,('status_code','duration_ms')))
        clean['route']=route if isinstance(route,str) and ROUTE.fullmatch(route) else 'unmatched'
        clean['method']=method if isinstance(method,str) and method in HTTP_METHODS else 'OTHER'
        if isinstance(identity,str) and UUID.fullmatch(identity):
            clean['request_id']=identity
        if isinstance(code,str) and code in SAFE_CODES|{'response_interrupted','other_application_error'}:
            clean['error_code']=code
    elif event=='operations_snapshot':
        clean['database']=_counts(raw.get('database'),('disk_free_bytes','size_bytes'))
        database=_dict(raw.get('database'))
        if database.get('status') in ('readable','unavailable'):
            clean['database']['status']=database['status']
        if type(database.get('required_migration_applied')) is bool:
            clean['database']['required_migration_applied']=database['required_migration_applied']
        clean['business_counts']=None if raw.get('business_counts') is None else _counts(raw['business_counts'],BUSINESS_COUNTS)
        clean['dispatch']=_counts(raw.get('dispatch'),('due_without_execution_count','oldest_due_age_seconds',
            'unprocessed_change_count','oldest_unprocessed_change_age_seconds'))
        clean['queues']=None if raw.get('queues') is None else {}
        for name,value in _dict(raw.get('queues')).items():
            if name not in QUEUES or not isinstance(value,dict):
                continue
            q=_counts(value,('oldest_ready_age_seconds','stalled_in_progress_count','failed_last_24h','scheduled_retry_count'))
            q['counts']=_counts(value.get('counts'),(*STATUSES,'other'))
            clean['queues'][name]=q
        clean['heartbeats']={}
        for name,value in _dict(raw.get('heartbeats')).items():
            if name in ('worker','scheduler') and _dict(value).get('status') in ('online','stale','missing','clock_skew'):
                clean['heartbeats'][name]={'status':value['status'],**_counts(value,('age_seconds',))}
        clean['alerts']=[]
        for alert in raw['alerts'][:32] if isinstance(raw.get('alerts'),list) else ():
            a=_dict(alert)
            if a.get('category') not in ('queue_failure','queue_stalled','database_failure') or a.get('code') not in tuple(ALERT_CODES):
                continue
            clean['alerts'].append({k:a[k] for k in ('category','code','queue','component') if k in a and isinstance(a[k],str)
                                    and (k not in ('queue','component') or a[k] in COMPONENTS)})
    elif raw.get('component') in tuple(COMPONENTS):
        clean['component']=raw['component']
    return stamp.timestamp(),clean


def ingest(db, spool: Path, *, limit=2500):
    """Checkpoint per file in the same transaction as captured records; ignore an unfinished last line."""
    remaining=limit
    accepted=0
    for source in sorted(spool.iterdir()):
        if remaining<=0:
            break
        if not SPOOL_FILE.fullmatch(source.name) or source.is_symlink() or not source.is_file():
            continue
        try:
            with source.open('rb') as file, db:
                inode=source.stat().st_ino
                saved=db.execute('SELECT inode,offset,dropping FROM cursors WHERE file=?',(source.name,)).fetchone()
                offset,dropping=saved[1:] if saved and saved[0]==inode and saved[1]<=source.stat().st_size else (0,0)
                file.seek(offset)
                while remaining:
                    line=file.readline(MAX_LINE+1)
                    if not line:
                        break
                    if len(line)>MAX_LINE:
                        dropping=int(not line.endswith(b'\n'))
                    elif not line.endswith(b'\n'):
                        break
                    elif dropping:
                        dropping=0
                    else:
                        try:
                            value=normalize_event(json.loads(line))
                        except (ValueError,RecursionError,TypeError):
                            value=None
                        if value:
                            stamp,item=value
                            db.execute('INSERT INTO events(ts,event,payload) VALUES (?,?,?)',(stamp,item['event'],_encode(item)))
                            accepted+=1
                    offset=file.tell()
                    remaining-=1
                db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,?,?)',(source.name,inode,offset,dropping))
        except FileNotFoundError:
            continue
    return accepted


def aggregate(events, *, duration, end):
    """Keep request and snapshot failures distinct; never include request IDs in the model input."""
    statuses, routes, incidents = Counter(), Counter(), Counter()
    durations=defaultdict(list)
    latest={}
    snapshots=0
    for event in events:
        if event['event']=='http_request':
            code=event.get('status_code')
            if code is not None:
                statuses[str(code)]+=1
                routes[event['route']]+=1
                if 'duration_ms' in event:
                    durations[event['route']].append(event['duration_ms'])
            if event.get('category')=='interface_failure':
                incidents['interface_failure:'+str(code)]+=1
        elif event['event']=='operations_snapshot':
            snapshots+=1
            latest={key:event.get(key) for key in ('queues','database','heartbeats','business_counts','dispatch')}
            for alert in event['alerts']:
                incidents[':'.join((alert['category'],alert['code'],alert.get('queue',alert.get('component','')))).rstrip(':')]+=1
        else:
            incidents[event.get('category',event['event'])]+=1
    return {'window_end_utc':datetime.fromtimestamp(end,UTC).isoformat(),'window_seconds':duration,
            'http_status_counts':dict(statuses),'route_summary':[
                {'route':route,'requests':count,'p95_ms':round(sorted(durations[route])[math.ceil(.95*len(durations[route]))-1],2)
                 if durations[route] else None} for route,count in routes.most_common(16)],
            'snapshot_count':snapshots,'latest_snapshot':latest,'incident_counts':dict(incidents)}


def close_windows(db, now, seconds):
    """Include late events in aggregates and mark saved prose stale without another model call."""
    cutoff=math.floor((now-3)/seconds)*seconds
    windows=db.execute("""
        WITH batches AS (
            SELECT CAST(ts/? AS INTEGER)*? AS start, MAX(id) AS last_event_id
            FROM events WHERE ts<? GROUP BY start
        )
        SELECT batches.start,batches.last_event_id FROM batches
        LEFT JOIN windows USING(start)
        WHERE batches.last_event_id>COALESCE(windows.last_event_id,0)
        ORDER BY batches.start LIMIT 32
    """,(seconds,seconds,cutoff)).fetchall()
    with db:
        for start,last_event_id in windows:
            items=(json.loads(row[0]) for row in db.execute('SELECT payload FROM events WHERE ts>=? AND ts<? ORDER BY ts,id',(start,start+seconds)))
            summary=aggregate(items,duration=seconds,end=start+seconds)
            db.execute("""
                INSERT INTO windows(start,end,aggregate,last_event_id) VALUES (?,?,?,?)
                ON CONFLICT(start) DO UPDATE SET aggregate=excluded.aggregate,last_event_id=excluded.last_event_id,
                    ai_state=CASE WHEN windows.ai_state='saved' THEN 'saved_stale' ELSE windows.ai_state END
            """,(start,start+seconds,_encode(summary),last_event_id))


def evaluate_alerts(db, now, *, started):
    """Persist opening and recovery transitions, deduplicated within this deployment."""
    current=set()
    recent=db.execute("SELECT payload FROM events WHERE event='http_request' AND ts>? AND ts<=?",(now-300,now))
    errors=Counter()
    for payload, in recent:
        event=json.loads(payload)
        code=event.get('status_code')
        if code is not None and 500<=code<=599:
            errors[event['route']]+=1
    current.update('interface_failure:http_5xx:'+route for route,count in errors.items() if count>=3)
    recent=db.execute("SELECT ts,payload FROM events WHERE event='operations_snapshot' AND ts<=? ORDER BY ts DESC,id DESC LIMIT 2",(now,)).fetchall()
    if now-(recent[0][0] if recent else started)>=180:
        current.add('monitor_failure:snapshot_missing')
    elif recent:
        def signals(row):
            """Return stable categories, never a raw exception or identifying payload."""
            return {':'.join([a['category'],a['code'],a.get('queue',a.get('component',''))]).rstrip(':')
                    for a in json.loads(row[1]).get('alerts',[])}
        latest=signals(recent[0])
        current.update(key for key in latest if not key.startswith('queue_stalled:'))
        if len(recent)>1:
            current.update(key for key in latest & signals(recent[1]) if key.startswith('queue_stalled:'))
    active={row[0] for row in db.execute('SELECT key FROM alert_state')}
    # Loss of visibility must not clear an existing queue or database alert.
    if 'monitor_failure:snapshot_missing' in current:
        current.update(key for key in active if key.startswith(('queue_','database_')))
    elif recent and json.loads(recent[0][1]).get('queues') is None:
        current.update(key for key in active if key.startswith('queue_'))
    with db:
        for key in sorted(current-active):
            db.execute('INSERT INTO alert_state VALUES (?,?)',(key,now))
            db.execute("INSERT INTO alert_history VALUES (?,?, 'opened')",(now,key))
        for key in sorted(active-current):
            db.execute('DELETE FROM alert_state WHERE key=?',(key,))
            db.execute("INSERT INTO alert_history VALUES (?,?, 'recovered')",(now,key))



def _summary_text(text):
    """Save bounded prose as untrusted commentary, never a tool output or credential."""
    if (not isinstance(text,str) or not 1<=len(text.strip())<=2500 or SUMMARY_UNSAFE.search(text)
            or any(ord(ch)<32 and ch not in '\n\t' for ch in text)):
        raise ValueError('invalid_model_summary')
    return text.strip()


class NexusSummarizer:
    """Send aggregate-only, tool-free completion requests with a dedicated Nexus execute key."""

    def __init__(self, url, key_file, owner, provider, model):
        """Validate destination and keep credentials outside command arguments and logs."""
        parsed=urlsplit(url)
        if (parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in ('','/')):
            raise ValueError('invalid_nexus_origin')
        if parsed.scheme=='http' and parsed.hostname not in ('127.0.0.1','::1','localhost','host.docker.internal','host-gateway','nexus'):
            raise ValueError('plaintext_nexus_origin')
        self.url=url.rstrip('/')+'/api/v1'
        self.key=key_file.read_text().strip()
        if not re.fullmatch(r'np_[0-9a-f-]{36}\.[A-Za-z0-9_-]{32,}',self.key) or not owner or not UUID.fullmatch(owner):
            raise ValueError('invalid_nexus_execute_key')
        self.owner=owner
        self.provider=provider
        self.model=model

    def __call__(self, summary, window):
        """Start one fresh operations-only run; send no tools, prior messages, or record IDs."""
        with httpx.Client(timeout=40,trust_env=False,follow_redirects=False,
                          headers={'Authorization':'Bearer '+self.key}) as client:
            response=client.post(self.url+'/runs',json={'user_request':'Assistant 运维聚合摘要','user_id':self.owner,'run_type':'operations_summary'})
            response.raise_for_status()
            run=response.json()['run_id']
            if not isinstance(run,str) or not UUID.fullmatch(run):
                raise ValueError('invalid_nexus_run')
            result=client.post(self.url+'/responses',json={
                'run_id':run,'provider':self.provider,'model':self.model,'tools':[],
                'input':_encode(summary),'instructions':
                '以中文总结给定的 Assistant 运维聚合数据。区分接口失败、队列卡住、数据库异常；'
                '只使用给定数字，不猜原因或用户数据。样本缺失要明确说未知。只返回简短文字，无链接或标识。',
                'append_input_to_context':False,'max_output_tokens':700,'timeout_seconds':30,
                'idempotency_key':f'assistant-operations-{window}','stream':False})
            result.raise_for_status()
            body=result.json()
            if body.get('status')!='completed' or body.get('tool_calls'):
                raise ValueError('invalid_nexus_response')
            return _summary_text(body.get('output_text'))


def summarize_next(db, summarize: Callable, now, *, seconds):
    """Take at most one recent pending window; do not retry an in-doubt paid call."""
    row=db.execute("""SELECT start,aggregate FROM windows WHERE ai_state='not_requested'
                   AND end>? AND end<=? ORDER BY start DESC LIMIT 1""",(now-seconds*2,now)).fetchone()
    if not row:
        return
    window,payload=row
    with db:
        if db.execute("UPDATE windows SET ai_state='in_progress' WHERE start=? AND ai_state='not_requested'",
                      (window,)).rowcount!=1:
            return
    try:
        text=_summary_text(summarize(json.loads(payload),window))
    except httpx.HTTPStatusError as error:
        state='failed'
        text=None
        status=error.response.status_code
        code={401:'upstream_auth_failed',403:'upstream_auth_failed',429:'upstream_rate_limited'}.get(
            status,'upstream_http_failure')
    except httpx.HTTPError:
        state,text,code='unknown',None,'upstream_transport_unknown'
    except (ValueError,KeyError,TypeError):
        state,text,code='failed',None,'invalid_summary'
    else:
        state,code='saved',None
    with db:
        db.execute('UPDATE windows SET ai_state=?,ai_summary=?,ai_error=? WHERE start=?',
                   (state,text,code,window))


def run(args):
    """Keep a bounded log store in a volume separate from the Assistant business database."""
    args.spool.mkdir(parents=True,exist_ok=True)
    args.store.mkdir(parents=True,exist_ok=True)
    database=args.store/'operations.sqlite3'
    with closing(sqlite3.connect(database,timeout=5)) as db:
        db.executescript(SCHEMA)
        stop=Event()

        def finish(*_):
            """Stop between bounded collection cycles."""
            stop.set()

        signal.signal(signal.SIGTERM,finish)
        signal.signal(signal.SIGINT,finish)
        with db:
            db.execute("UPDATE windows SET ai_state='unknown',ai_error='collector_restarted' WHERE ai_state='in_progress'")
        key_file = args.ai_key_file
        summarizer = NexusSummarizer(args.nexus_url,key_file,args.ai_user_id,args.provider,args.model) if key_file else None
        with db:
            db.execute("INSERT OR IGNORE INTO collector_state VALUES ('started',?)",(datetime.now(UTC).timestamp(),))
        started=db.execute("SELECT value FROM collector_state WHERE key='started'").fetchone()[0]
        while not stop.is_set():
            now=datetime.now(UTC).timestamp()
            captured = ingest(db,args.spool)
            if captured<2500:
                close_windows(db,now,args.window_seconds)
            evaluate_alerts(db,now,started=started)
            if summarizer:
                summarize_next(db,summarizer,now,seconds=args.window_seconds)
            with db:
                db.execute('DELETE FROM events WHERE ts<?',(now-14*86400,))
                db.execute('DELETE FROM windows WHERE end<?',(now-30*86400,))
                db.execute('DELETE FROM alert_history WHERE ts<?',(now-30*86400,))
                db.execute("INSERT OR REPLACE INTO collector_state VALUES ('sampled_at',?)",(now,))
                for name, in db.execute('SELECT file FROM cursors').fetchall():
                    cutoff=datetime.fromtimestamp(now-14*86400,UTC).date().isoformat()
                    if SPOOL_FILE.fullmatch(name) and name[:10]<cutoff:
                        (args.spool/name).unlink(missing_ok=True)
                        db.execute('DELETE FROM cursors WHERE file=?',(name,))
            if args.once:
                break
            stop.wait(5)


def main():
    """Run collection without model calls until a dedicated summary credential is supplied."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spool',type=Path,required=True)
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--nexus-url',default='http://127.0.0.1:8000')
    parser.add_argument('--ai-key-file',type=Path)
    parser.add_argument('--ai-user-id')
    parser.add_argument('--provider',default='opencode')
    parser.add_argument('--model',default='glm-5.3-flash')
    parser.add_argument('--window-seconds',type=int,default=900)
    parser.add_argument('--once',action='store_true')
    parser.add_argument('--healthcheck',action='store_true')
    args=parser.parse_args()
    if not 15<=args.window_seconds<=3600:
        parser.error('window-seconds must be between 15 and 3600')
    if args.healthcheck:
        # Health probes only read the separate collector store.
        try:
            with closing(sqlite3.connect((args.store/'operations.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                row=db.execute("SELECT value FROM collector_state WHERE key='sampled_at'").fetchone()
            if row and datetime.now(UTC).timestamp()-row[0]<90:
                return
        except (sqlite3.Error,OSError):
            pass
        raise SystemExit(1)
    run(args)


if __name__=='__main__':
    main()
