"""Evaluate new ambiguous-intent and commute cases with real Nexus and isolated rows.

Run from services/assistant-api using .venv/bin/python scripts/evaluate_intent_commute.py
--env-file /path/to/.env --output-dir /private/tmp/new-dir --cases id,... .
The explicit case list prevents regression runs. Map/place/ETA providers are the
existing labelled mocks; model planning and all business dispatch are real. No
scheduler, notifications, live business database or account memory writes run.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from functools import partial
from datetime import UTC, date, datetime
from contextlib import nullcontext
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4

import httpx
from sqlalchemy import select

from assistant_api.core.config import Settings
from assistant_api.main import create_app
from assistant_api.models import CommutePlan, ScheduleEvent, ScheduleRouteResult, FollowUpExecution, User
from assistant_api.services import operation_intake
from assistant_api.services.execution_context import ExecutionContext, dispatch_local
from evaluate_record_calls import seed, snapshot, timestamp, trace, write_json

CASES = {
    'casual_todo': ('帮我记一下买牛奶，回头办。', 'empty'),
    'deadline_without_type': ('2030年11月8日下午3点前把报价核对完，帮我记着。', 'empty'),
    'reminder_without_time': ('提醒我买药。', 'empty'),
    'deadline_todo': ('2030年11月8日下午3点前把合同看完，记一项待办。', 'empty'),
    'meeting_schedule': ('2030年11月8日下午3点和客户聊报价，帮我排一下。', 'empty'),
    'meeting_materials': ('帮我安排一下开会材料，2030年11月8日下午3点前准备好。', 'empty'),
    'negated_schedule': ('先别安排会议，只加一个准备材料的待办。', 'empty'),
    'missing_kind': ('帮我安排一下开会。', 'empty'),
    'ambiguous_change': ('把那个事情往后挪一下。', 'absolute'),
    'query_only': ('看看我现在有哪些安排和要办的事情。', 'absolute'),
    'commute_past_time': ('把今天这条测试上班通勤的到达时间改到10:00，只改今天，长期计划不变。', 'linked_commute'),
    'commute_evening_worker': ('前晚22点自动更新2030年11月4日通勤', 'linked_commute'),
    'commute_create': ('新增一套通勤安排：上海，从测试家园到测试公司，自2030年11月4日起每周一至周五，开车，09:00到达，预留10分钟。不加待办。这是联调测试。', 'empty'),
    'commute_day_modify': ('只把现有测试上班通勤2030年11月4日这一天的到达时间改到10:00，长期计划仍为09:00；起终点、开车方式和10分钟余量都保持，不新增日程。', 'linked_commute'),
    'commute_modify_exact_name': ('把名称为“测试上班通勤”的现有通勤计划长期改成09:30到达，预留15分钟；原每周一至周五、起终点、交通方式和其他设置保持，不新建计划。', 'commute'),
    'commute_modify': ('把我现有的测试上班通勤计划长期改为每天09:30到达，预留15分钟；原来每周一至周五、出发地、目的地、交通方式和其他设置保持。不要新建计划。', 'commute'),
    'commute_missing': ('帮我安排每天上班的通勤，早上九点到。', 'empty'),
    'commute_vague': ('把现有测试上班通勤往后挪一点。', 'commute'),
}
CLARIFICATIONS = {'missing_kind', 'ambiguous_change', 'commute_missing', 'commute_vague', 'reminder_without_time', 'commute_past_time'}
TODO_CASES = {'deadline_without_type', 'casual_todo', 'deadline_todo', 'meeting_materials', 'negated_schedule'}


async def seed_case(client, preset, *, app=None, user_id=None):
    """Create only the synthetic reference records required by the selected case."""
    if preset == 'linked_commute':
        created = await client.post('/api/v1/schedules',json={
            'idempotency_key':str(uuid4()),'title':'测试上班通勤','starts_at':'2030-11-04T08:20:00+08:00',
            'ends_at':'2030-11-04T08:50:00+08:00','location':'测试公司','latitude':31.2304,'longitude':121.4737,'coordinate_system':'gcj02'})
        created.raise_for_status();basis=created.json()
        response=await client.post('/api/v1/commute-plans',json={
            'name':'测试上班通勤','enabled':True,'timezone':'Asia/Shanghai','weekdays':[1,2,3,4,5],
            'arrival_local_time':'09:00','check_local_time':'07:00','transport_mode':'driving','buffer_minutes':10,
            'notify_enabled':False,'effective_from':'2030-11-04',
            'origin':{'label':'测试家园','latitude':31.2231,'longitude':121.4456},
            'destination':{'label':'测试公司','latitude':31.2304,'longitude':121.4737}})
        response.raise_for_status();pid=response.json()['id']
        async with app.state.session_factory() as db:
            plan=await db.get(CommutePlan,pid);plan.matter_id=basis['matter']['matter_id'];plan.first_schedule_id=basis['schedule']['schedule_id']
            await db.commit()
        return {'matter_id':basis['matter']['matter_id'],'plan_id':pid}
    if preset != 'commute':
        return await seed(client, preset)
    response = await client.post('/api/v1/commute-plans', json={
        'name': '测试上班通勤', 'enabled': True, 'timezone': 'Asia/Shanghai',
        'weekdays': [1, 2, 3, 4, 5], 'arrival_local_time': '09:00',
        'check_local_time': '07:00', 'transport_mode': 'driving',
        'buffer_minutes': 10, 'rain_probability_threshold': 0.4,
        'rain_extra_buffer_minutes': 10, 'notify_enabled': False,
        'effective_from': '2030-11-04',
        'origin': {'label': '测试家园', 'latitude': 31.2231, 'longitude': 121.4456},
        'destination': {'label': '测试公司', 'latitude': 31.2304, 'longitude': 121.4737},
    })
    response.raise_for_status()
    return {'plan_id': response.json()['id']}


async def full_snapshot(app, user_id):
    """Read actual record identities and commute configuration without comparing hashes."""
    state = await snapshot(app, user_id)
    async with app.state.session_factory() as db:
        schedules = list(await db.scalars(select(ScheduleEvent).where(ScheduleEvent.user_id == user_id)))
        ends_by_id = {row.schedule_id: row.ends_at for row in schedules}
        for row in state['schedules']:
            row['ends_at'] = ends_by_id[row['schedule_id']]
        plans = list(await db.scalars(select(CommutePlan).where(CommutePlan.user_id == user_id)))
        fields = ['plan_id', 'matter_id', 'first_schedule_id', 'name', 'revision', 'enabled',
                  'timezone', 'weekdays_json', 'arrival_local_time', 'check_local_time',
                  'transport_mode', 'buffer_minutes', 'effective_from', 'notify_enabled',
                  'rain_probability_threshold', 'rain_extra_buffer_minutes',
                  'origin_label', 'origin_latitude', 'origin_longitude',
                  'destination_label', 'destination_latitude', 'destination_longitude']
        state['commute_plans'] = [{field: getattr(plan, field) for field in fields} for plan in plans]
        routes = list(await db.scalars(select(ScheduleRouteResult).where(ScheduleRouteResult.user_id == user_id)))
        state['routes'] = [{'schedule_id': row.schedule_id, 'result': row.result_json} for row in routes]
    return state


def check_result(case_id, result, before, after, turns):
    """Assert independent business expectations, including forbidden resource creation."""
    if case_id in CLARIFICATIONS:
        assert result['outcome'] == 'needs_clarification', result
        assert before == after, 'unclear input must not change formal records'
        decision = json.loads(turns[-1]['model_output'])
        assert decision['decision'] in {'ask_user', 'clarify'}, 'question must come from AI'
        assert decision.get('question'), 'AI must ask for actual missing information'
        return
    assert result['outcome'] == 'answer', result
    if case_id == 'query_only':
        assert before == after, 'query must not create or change records'
    elif case_id in TODO_CASES:
        assert len(after['todos']) == 1 and not after['schedules'], 'todo intent was lost or became calendar'
        todo = after['todos'][0]
        assert not after['commute_plans'] and not after['reminders']
        if case_id in {'deadline_todo', 'deadline_without_type', 'meeting_materials'}:
            assert todo['due_at'] and timestamp(todo['due_at']) == '2030-11-08T07:00:00+00:00', 'deadline must remain todo.due_at'
        else:
            assert todo['due_at'] is None and todo['reminder_at'] is None, 'unspecified time was invented'
        expected_words = {'deadline_without_type': ['报价'], 'casual_todo': ['牛奶'], 'deadline_todo': ['合同'], 'meeting_materials': ['材料'], 'negated_schedule': ['材料']}[case_id]
        assert all(word in todo['title'] for word in expected_words), todo
    elif case_id == 'meeting_schedule':
        assert len(after['schedules']) == 1 and not after['todos']
        assert timestamp(after['schedules'][0]['starts_at']) == '2030-11-08T07:00:00+00:00'
    elif case_id == 'commute_create':
        assert len(after['commute_plans']) == len(after['schedules']) == len(after['matters']) == 1, 'complete commute requires plan, matter and first schedule'
        assert not after['todos'], 'commute must not add an unrequested todo'
        plan = after['commute_plans'][0]
        assert plan['matter_id'] == after['matters'][0]['matter_id']
        assert plan['first_schedule_id'] == after['schedules'][0]['schedule_id']
        assert plan['arrival_local_time'] == '09:00' and plan['buffer_minutes'] == 10
        assert plan['weekdays_json'] == [1, 2, 3, 4, 5] and str(plan['effective_from']) == '2030-11-04'
        assert plan['transport_mode'] == 'driving'
        assert plan['origin_label'] == '测试家园' and plan['destination_label'] == '测试公司'
        assert len(after['routes']) == 1 and after['routes'][0]['result']['status'] == 'available'
        assert after['routes'][0]['result']['provider'] == 'mock', 'map fixture must stay labelled'
        assert timestamp(after['schedules'][0]['starts_at']) == '2030-11-04T00:20:00+00:00'
    elif case_id == 'commute_evening_worker':
        assert result['source']=='follow_up', 'must be an actual persisted follow-up execution'
        assert after['commute_plans']==before['commute_plans'] and after['schedules']==before['schedules']
        assert len(after['routes'])==1 and timestamp(after['routes'][0]['result']['target_arrival_at'])=='2030-11-04T01:00:00+00:00'
        calls=[call for turn in turns for call in turn['actual_calls']]
        assert any(call['operation_id']=='refresh_commute_day' and call['status']=='succeeded' for call in calls), 'AI must execute the actual refresh API'
        assert result.get('pending_context',{}).get('cards'), 'evening update must include real result card'
    elif case_id == 'commute_day_modify':
        assert result.get('pending_context',{}).get('cards'), 'saved day must return actual result card'
        assert after['commute_plans'] == before['commute_plans'], 'one-day edit must not change recurring configuration'
        assert len(after['schedules']) == 1 and after['schedules'][0]['schedule_id'] == before['schedules'][0]['schedule_id'], 'must update same day without duplicates'
        assert timestamp(after['schedules'][0]['starts_at']) == '2030-11-04T01:20:00+00:00', 'departure must be recomputed for 10:00 arrival'
        assert len(after['routes']) == 1
        assert timestamp(after['routes'][0]['result']['target_arrival_at']) == '2030-11-04T02:00:00+00:00', 'route must be remeasured for the changed arrival'
    elif case_id in {'commute_modify', 'commute_modify_exact_name'}:
        assert result.get('pending_context',{}).get('cards'), 'saved plan must return actual result card'
        assert len(after['commute_plans']) == 1, 'modification must not duplicate the plan'
        original, changed = before['commute_plans'][0], after['commute_plans'][0]
        assert changed['arrival_local_time'] == '09:30' and changed['buffer_minutes'] == 15, 'original plan was not updated to the requested arrival and buffer'
        assert changed['revision'] == original['revision'] + 1
        for field in original.keys() - {'arrival_local_time', 'buffer_minutes', 'revision'}:
            assert changed[field] == original[field], f'unrequested change: {field}'
        for field in ('matters', 'schedules', 'todos', 'reminders', 'routes'):
            assert before[field] == after[field], f'plan-only change wrote {field}'


def check_cards(result, turns):
    """Require each AI-requested card to survive local binding and response validation."""
    requested = json.loads(turns[-1]['model_output']).get('cards', [])
    if not requested:
        return 'not_requested'
    actual = result.get('pending_context', {}).get('cards', [])
    assert len(actual) == len(requested), 'AI-requested card was rejected or omitted after the business operation'
    return 'passed'


async def run_evening_worker(app, now, user_id):
    """Drive the actual durable 22:00 scheduler and one worker with a real Nexus planner."""
    from assistant_api.services import commute_lifecycle, follow_ups
    app.state.settings.commute_auto_ai_enabled=True
    await commute_lifecycle.materialize_follow_up_rules(app,now=now)
    assert await follow_ups.scheduler_tick(app,now=now,scheduler_id='isolated-evening')==1
    assert await follow_ups.worker_tick(app,now=now,worker_id='isolated-evening-worker')
    async with app.state.session_factory() as db:
        execution=await db.scalar(select(FollowUpExecution).where(FollowUpExecution.user_id==user_id))
        assert execution.state=='succeeded', (execution.state,execution.error_code)
        return execution.result_json


async def run_case(app, output_dir, case_id):
    """Run one new model case once and persist its decisions, HTTP receipts and rows."""
    text, preset = CASES[case_id]
    started = time.monotonic()
    report = {'case': case_id, 'input': text, 'status': 'running', 'model': 'real Nexus', 'maps': 'mock'}
    async with app.state.session_factory() as db:
        user = User(email=f'{uuid4()}@intent-evaluation.invalid', display_name='隔离意图验收', password_verifier='disabled-evaluation-login', status='active')
        db.add(user); await db.commit(); await db.refresh(user); user_id = user.user_id
    print(json.dumps({'case': case_id, 'status': 'started'}, ensure_ascii=False), flush=True)
    original_clock=app.state.commute_runtime.now
    clock = datetime(2030,11,4,3,tzinfo=UTC) if case_id=='commute_past_time' else datetime(2030,11,3,14,tzinfo=UTC) if case_id=='commute_evening_worker' else None
    class ScenarioClock(datetime):
        '''Supply the explicitly labelled synthetic business clock to real-model context.'''
        @classmethod
        def now(cls, tz=None):
            '''Keep planner context consistent with provider and schedule time.'''
            return clock.astimezone(tz) if tz else clock.replace(tzinfo=None)
    clock_patch=patch.object(operation_intake,'datetime',ScenarioClock) if clock else nullcontext()
    try:
        transport = httpx.ASGITransport(app=partial(dispatch_local, app, user_id=user_id))
        async with httpx.AsyncClient(transport=transport, base_url='http://isolated-assistant') as client:
            seeded = await seed_case(client, preset, app=app, user_id=user_id)
        if clock:
            app.state.commute_runtime.now=lambda:clock
            report['synthetic_clock']=clock.isoformat()
        before = await full_snapshot(app, user_id)
        report['before'] = before
        clock_patch.__enter__()
        async with app.state.session_factory() as db:
            user = await db.get(User, user_id)
            task = asyncio.create_task(run_evening_worker(app,clock,user_id) if case_id=='commute_evening_worker' else operation_intake.intake(ExecutionContext(app, user_id), db, user,
                text=text, idempotency_key=str(uuid4()), matter_id=seeded.get('matter_id')))
            while not task.done():
                await asyncio.wait({task}, timeout=20)
                if not task.done():
                    print(json.dumps({'case': case_id, 'status': 'model_running', 'elapsed_seconds': round(time.monotonic()-started)}), flush=True)
                if time.monotonic() - started > 420:
                    task.cancel(); await asyncio.gather(task, return_exceptions=True)
                    raise TimeoutError('Case exceeded 420 seconds')
            result = task.result()
        report['result'] = result
        report['after'] = await full_snapshot(app, user_id)
        report['turns'] = await trace(app, user_id)
        check_result(case_id, result, before, report['after'], report['turns'])
        report['business_status'] = 'passed'
        report['presentation_status'] = check_cards(result, report['turns'])
        report['status'] = 'passed'
    except Exception as exc:
        if report.get('business_status') == 'passed':
            report['presentation_status'] = 'failed'
        else:
            report['business_status'] = 'failed'
        report.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:1600],
                      error_code=getattr(exc, 'code', None))
        report['after'] = await full_snapshot(app, user_id)
        report['turns'] = await trace(app, user_id)
    clock_patch.__exit__(None,None,None)
    app.state.commute_runtime.now=original_clock
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    write_json(output_dir / f'{case_id}.json', report)
    print(json.dumps({key: report[key] for key in ('case', 'status', 'elapsed_seconds')}, ensure_ascii=False), flush=True)
    return report


async def evaluate(args):
    """Compose one isolated app and evaluate only explicitly requested new cases."""
    chosen = args.cases.split(',')
    if len(chosen) != len(set(chosen)) or set(chosen) - CASES.keys():
        raise ValueError('Unknown or repeated case IDs')
    if 'commute_evening_worker' in chosen and len(chosen)!=1:
        raise ValueError('Run the global background worker case alone in a fresh database')
    args.output_dir.mkdir(parents=True, exist_ok=True)
    database = args.output_dir / 'assistant.db'
    if database.exists():
        raise ValueError('Use a fresh output directory; previous evidence is retained')
    settings = Settings(_env_file=args.env_file, environment='test',
        database_url=f'sqlite+aiosqlite:///{database}', allow_schema_create=True,
        commute_provider_mode='mock', commute_auto_ai_enabled=False,
        commute_auto_weather_enabled=False, matter_ai_timeout_seconds=180)
    app = create_app(settings)
    reports = []
    async with app.router.lifespan_context(app):
        for case_id in chosen:
            reports.append(await run_case(app, args.output_dir, case_id))
            write_json(args.output_dir / 'summary.json', reports)
    print(json.dumps({'passed': sum(r['status'] == 'passed' for r in reports),
        'failed': sum(r['status'] == 'failed' for r in reports), 'report': str(args.output_dir / 'summary.json')}, ensure_ascii=False), flush=True)
    return all(report['status'] == 'passed' for report in reports)


def main():
    """Require explicit credentials, fresh storage and selected cases before model use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cases', required=True)
    raise SystemExit(0 if asyncio.run(evaluate(parser.parse_args())) else 1)


if __name__ == '__main__':
    main()
