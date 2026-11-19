"""One isolated fault drill for the monitoring JSON, with no scheduler or provider started."""
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from assistant_api.core.config import Settings
from assistant_api.main import create_app
from assistant_api.models import Matter, MatterSupplement, ServiceHeartbeat, User
from scripts.migrate_sqlite import init


@pytest.mark.asyncio
async def test_fault_events_distinguish_interface_queue_and_database(tmp_path, capsys):
    """Log route failure, isolated queue backlog and unreadable database as separate incidents."""
    from assistant_api.operations_monitor import report_once

    now = datetime(2030, 1, 2, 3, tzinfo=UTC)
    path = tmp_path / 'assistant.db'
    url = f'sqlite+aiosqlite:///{path}'
    await init(url)
    settings = Settings(_env_file=None, environment='test', database_url=url,
                        commute_auto_ai_enabled=False, commute_auto_weather_enabled=False)
    app = create_app(settings)
    private = 'PRIVATE_DRILL_SENTINEL'

    @app.get('/ops-drill/{record_id}')
    async def failed(record_id: str):
        """Simulate an unexpected failure whose raw exception text must stay private."""
        raise RuntimeError(private + record_id)

    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as db:
            user = User(email='monitor-drill@example.test', password_verifier=private, display_name=private)
            db.add(user)
            await db.flush()
            matter = Matter(user_id=user.user_id, title=private)
            db.add(matter)
            await db.flush()
            db.add_all([
                ServiceHeartbeat(component=component, last_seen_at=now-timedelta(seconds=2))
                for component in ('worker','scheduler')
            ])
            db.add(MatterSupplement(user_id=user.user_id, matter_id=matter.matter_id, idempotency_key='drill-queue',
                request_json={'text':private}, ai_status='queued', created_at=now-timedelta(minutes=12)))
            await db.commit()
        capsys.readouterr()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://isolated') as client:
            response = await client.get('/ops-drill/'+private,params={'secret':private},headers={
                'Authorization':'Bearer '+private,'X-Request-ID':private})
            assert response.status_code == 500
            assert response.headers['x-request-id'] != private
        report_once(settings,now=now)
    missing = tmp_path / 'does-not-exist.db'
    report_once(settings.model_copy(update={'database_url':f'sqlite+aiosqlite:///{missing}'}),now=now)
    assert not missing.exists()
    output = capsys.readouterr().out
    assert private not in output and 'monitor-drill@example.test' not in output and 'does-not-exist.db' not in output
    request, backlog, unavailable = map(json.loads,output.splitlines())
    assert request['event']=='http_request' and request['route']=='/ops-drill/{record_id}'
    assert request['category']=='interface_failure' and request['status_code']==500
    assert request['request_id']==response.headers['x-request-id']
    assert backlog['database']['status']=='readable'
    assert backlog['queues']['supplement_analysis']['oldest_ready_age_seconds']==720
    assert backlog['heartbeats']['scheduler']['status']=='online'
    assert {'category':'queue_stalled','code':'ready_wait_exceeded','queue':'supplement_analysis'} in backlog['alerts']
    assert unavailable['database']['status']=='unavailable' and unavailable['queues'] is None
    assert unavailable['alerts'][0]['category']=='database_failure'
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM nexus_runs").fetchone()[0]==0
