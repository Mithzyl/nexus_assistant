"""Verify persisted aggregation, distinct alert transitions and summary replay safety."""
from datetime import UTC, datetime
import json
import sqlite3

from assistant_api.operations_collector import SCHEMA, close_windows, evaluate_alerts, ingest, summarize_next


def test_collect_sanitize_alert_recover_and_save_summary(tmp_path):
    """Reopen the log reader without duplicate rows or paid summary attempts."""
    spool=tmp_path/'2026-11-19.jsonl'
    db=sqlite3.connect(tmp_path/'operations.sqlite3')
    db.executescript(SCHEMA)
    t=1795065600

    def write(event,offset,**extra):
        """Append shaped fixture data without handing raw fields to aggregation."""
        obj={'service':'assistant','schema_version':1,'event':event,'timestamp':datetime.fromtimestamp(t+offset,UTC).isoformat(),
             'body':'PRIVATE_FIXTURE',**extra}
        with spool.open('a') as f:
            f.write(json.dumps(obj)+'\n')

    for offset in range(3):
        write('http_request',offset,category='interface_failure',route='/api/v1/todos',method='POST',status_code=500,duration_ms=10,
              request_id='a2ea30b3-a1a7-4ee4-ab93-ce07d94517eb',authorization='PRIVATE_FIXTURE')
    for offset in (4,5):
        write('operations_snapshot',offset,database={'status':'readable'},queues={},
              alerts=[{'category':'queue_stalled','code':'ready_wait_exceeded','queue':'follow_up'}])
    assert ingest(db,tmp_path)==5
    assert ingest(db,tmp_path)==0
    evaluate_alerts(db,t+5,started=t)
    write('operations_snapshot',6,database={'status':'unavailable'},queues=None,
          alerts=[{'category':'database_failure','code':'database_read_failed'}])
    assert ingest(db,tmp_path)==1
    evaluate_alerts(db,t+6,started=t)
    assert {row[0].split(':')[0] for row in db.execute('SELECT key FROM alert_state')}=={
        'interface_failure','database_failure','queue_stalled'}
    close_windows(db,t+20,15)
    calls=[]

    def model(payload,window):
        """Verify the exact sanitized inputs that the production Nexus sender receives."""
        calls.append(window)
        assert set(key.split(':')[0] for key in payload['incident_counts'])=={
            'interface_failure','database_failure','queue_stalled'}
        assert 'request_id' not in str(payload) and 'PRIVATE_FIXTURE' not in str(payload)
        assert payload['latest_snapshot']['queues'] is None
        return '接口返回三次 500；检测到队列等待过久，随后数据库不可读。'

    summarize_next(db,model,t+20,seconds=15)
    summarize_next(db,model,t+20,seconds=15)
    assert len(calls)==1
    assert db.execute('SELECT ai_state,ai_summary FROM windows').fetchone()==('saved',
        '接口返回三次 500；检测到队列等待过久，随后数据库不可读。')
    write('http_request',7,category='interface_ok',route='/api/v1/todos',method='GET',status_code=200,duration_ms=2)
    assert ingest(db,tmp_path)==1
    close_windows(db,t+21,15)
    summarize_next(db,model,t+21,seconds=15)
    state,payload=db.execute('SELECT ai_state,aggregate FROM windows').fetchone()
    assert state=='saved_stale' and json.loads(payload)['http_status_counts']=={'500':3,'200':1}
    assert len(calls)==1
    assert db.execute("SELECT COUNT(*) FROM events WHERE payload LIKE '%PRIVATE_FIXTURE%'").fetchone()[0]==0
    write('operations_snapshot',301,database={'status':'readable'},queues={},alerts=[])
    ingest(db,tmp_path)
    evaluate_alerts(db,t+303,started=t)
    assert db.execute("SELECT count(*) FROM alert_history WHERE transition='recovered'").fetchone()[0]==3
    db.close()
