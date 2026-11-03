"""Standalone Assistant scheduler/worker for generic follow-ups and provider jobs.

Run python -m assistant_api.background scheduler|worker [--once]. Uses the same
.env and SQLite as the API; apply scripts.migrate_sqlite upgrade first.
"""
import argparse
import asyncio
import logging
import os
import signal
from datetime import UTC, datetime
from assistant_api.main import create_app
from assistant_api.core.config import get_settings
from assistant_api.services import follow_ups
from assistant_api.commute.heartbeat import record_heartbeat


async def tick(app, component, identity, *, now=None, active_state=None):
    """Run generic follow-ups first; retain existing provider queues as API implementation work."""
    from assistant_api.commute.scheduler import scheduler_tick as provider_scheduler
    from assistant_api.commute.worker import worker_tick as provider_worker
    now=now or datetime.now(UTC)
    runtime=app.state.commute_runtime
    settings=app.state.settings
    async with app.state.session_factory() as db:
        await record_heartbeat(db,component=component,instance_id=identity,now=now)
    if component=='scheduler':
        from assistant_api.services.commute_lifecycle import materialize_follow_up_rules
        await materialize_follow_up_rules(app,now=now)
        count=await follow_ups.scheduler_tick(app,now=now,scheduler_id=identity)
        # Weather/provider refreshes do not plan user actions or start another AI planner.
        async with app.state.session_factory() as db:
            await provider_scheduler(db,settings,owner=identity,now=now,auto_analysis_mode='weather_only',enqueue_enabled=settings.commute_auto_weather_enabled,runtime=None)
        return count
    handled=await follow_ups.worker_tick(app,now=now,worker_id=identity)
    if not handled:
        async with app.state.session_factory() as db:
            provider_handled=await provider_worker(db,runtime,worker_id=identity,now=now,active_state=active_state)
        handled=any(provider_handled)
    return handled


async def run(component, *, once=False):
    """Own one application lifespan and process the selected persisted queue until signalled."""
    settings=get_settings()
    app=create_app(settings)
    identity=f'assistant-{component}:{os.getpid()}'
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for signum in (signal.SIGTERM,signal.SIGINT):
        loop.add_signal_handler(signum,stop.set)
    async with app.router.lifespan_context(app):
        from assistant_api.commute.heartbeat import heartbeat_loop
        active_state={'job':None}
        heartbeat=asyncio.create_task(heartbeat_loop(app.state.session_factory, component=component, instance_id=identity, interval_seconds=settings.commute_heartbeat_interval_seconds, stop=stop, active_job=lambda: active_state['job'], lease_seconds=settings.commute_job_lease_seconds))
        try:
            await process_loop(app,component,identity,stop,once,active_state)
        finally:
            stop.set()
            await heartbeat


async def process_loop(app,component,identity,stop,once,active_state):
    """Poll persisted queues while the independent heartbeat keeps long provider calls alive."""
    while not stop.is_set():
        try:
            await tick(app,component,identity,active_state=active_state)
        except Exception:
            logging.getLogger(__name__).exception('assistant background tick failed component=%s',component)
            if once:
                raise
        if once:
            return
        try:
            await asyncio.wait_for(stop.wait(),timeout=5)
        except TimeoutError:
            pass


def main():
    """Start an independent scheduler or worker using the API's existing configuration."""
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('component',choices=['scheduler','worker'])
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    asyncio.run(run(args.component,once=args.once))


if __name__=='__main__':
    main()
