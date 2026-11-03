"""Exercise real Nexus planning against isolated Assistant calendar/todo records.

Requires the existing Assistant application credentials in --env-file and a
reachable Nexus. No credentials are printed or copied into the report. All
Assistant users and business rows are synthetic and stored in --output-dir; no
scheduler, notification sender, real map/weather provider or production DB is
started. This is an explicit paid-model evaluation, not a regression suite.

Run from services/assistant-api:
  .venv/bin/python scripts/evaluate_record_calls.py --env-file /path/to/.env \
      --output-dir /private/tmp/record-ai-evaluation
Use --cases case_id,... to evaluate only specific new use cases after a fix.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from uuid import uuid4
import httpx
from sqlalchemy import select
from assistant_api.core.config import Settings
from assistant_api.main import create_app
from assistant_api.models import Matter, MatterTodo, NexusRun, Reminder, ScheduleEvent, User
from assistant_api.services import operation_intake
from assistant_api.services.execution_context import ExecutionContext, dispatch_local

CASES = [
    ('todo_only', '新增一个整理销售品清单的待办，不设截止时间和提醒。', 'empty'),
    ('schedule_only', '新增2030年5月9日下午3点的产品讨论日程，地点会议室A，只添加日程，不添加待办。', 'empty'),
    ('combined', '新建2030年6月1日下午3点的客户交流日程，在同一事项下加一个准备材料的待办，待办5月31日下午5点截止，下午4点提醒。', 'empty'),
    ('append_todo', '给当前项目评审事项补一个核对报价的待办，不设时间，不修改原日程。', 'schedule'),
    ('rename_schedule', '把当前事项2030年7月1日下午3点、地点会议室B的项目评审日程，名称改为团建，时间地点保持。', 'schedule'),
    ('edit_todo', '把当前事项的提交清单待办改名为整理清单，并标记为已完成；日程不变。', 'absolute'),
    ('relative_reminder', '在当前项目评审事项新建准备评审材料待办，明确在该日程开始前一小时提醒，不设截止时间。', 'schedule'),
    ('move_with_dependency', '把当前事项2030年7月1日下午3点的项目评审日程推迟到当天17点，地点不变。', 'relative'),
    ('cancel_preserves_todos', '取消当前事项2030年7月1日下午3点的项目评审日程，保留两条待办和独立提醒。', 'relative'),
    ('missing_time', '帮我新增一个产品讨论日程。', 'empty'),
    ('ambiguous_schedule', '把当前事项里的项目评审日程改到下午5点。', 'ambiguous'),
    ('duplicate_request', '新增一个提交销售材料的待办，不设截止或提醒时间。', 'empty'),
    ('find_existing_schedule', '把2030年7月1日下午3点、地点会议室B的项目评审日程改名为团建，时间地点保持。', 'schedule'),
    ('find_existing_todo', '把提交清单待办改名为整理清单，原有提醒和日程不变。', 'absolute'),
    ('schedule_relative_alert', '新增2030年10月2日下午3点的电话沟通日程，开始前30分钟提醒，只加日程，不添加待办。', 'empty'),
    ('move_own_alert', '把当前事项2030年7月1日下午3点的项目评审日程改到当天17点，保留开始前30分钟的提醒。', 'own_relative'),
]


def write_json(path: Path, value):
    """Store synthetic evaluation evidence without application credentials."""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n')


async def post(client, path, payload):
    """Seed synthetic records through the actual new business endpoints."""
    response = await client.post(path, json={'idempotency_key': str(uuid4()), **payload})
    if response.status_code != 201:
        raise RuntimeError(f'seed {path} returned {response.status_code}: {response.text[:500]}')
    return response.json()


async def seed(client, preset):
    """Build only the records required by one use case and return their actual IDs."""
    if preset == 'empty':
        return {}
    schedule = await post(client, '/api/v1/schedules', {'title': '项目评审', 'starts_at': '2030-07-01T15:00:00+08:00', 'location': '会议室B', **({'reminder_offset_seconds': -1800} if preset == 'own_relative' else {})})
    state = {'schedule': schedule, 'matter_id': schedule['matter']['matter_id']}
    version = schedule['matter']['version']
    if preset == 'ambiguous':
        state['second_schedule'] = await post(client, '/api/v1/schedules', {'title': '项目评审', 'starts_at': '2030-07-02T15:00:00+08:00', 'location': '会议室C', 'matter_id': state['matter_id'], 'expected_matter_version': version})
    elif preset in {'absolute', 'relative'}:
        absolute = await post(client, '/api/v1/todos', {'title': '提交清单', 'reminder_at': '2030-06-30T09:00:00+08:00', 'matter_id': state['matter_id'], 'expected_matter_version': version})
        state['absolute'] = absolute
        if preset == 'relative':
            state['relative'] = await post(client, '/api/v1/todos', {'title': '准备评审材料', 'due_at': '2030-06-30T18:00:00+08:00', 'schedule_reminder': {'schedule_id': schedule['schedule']['schedule_id'], 'offset_seconds': -3600}, 'matter_id': state['matter_id'], 'expected_matter_version': absolute['matter']['version']})
    return state


async def snapshot(app, user_id):
    """Read actual isolated rows; expectations are checked independently of model prose."""
    async with app.state.session_factory() as db:
        result = {}
        for name, model, fields in (
            ('matters', Matter, ['matter_id', 'title', 'version', 'status']),
            ('todos', MatterTodo, ['todo_id', 'matter_id', 'title', 'status', 'due_at', 'reminder_at']),
            ('schedules', ScheduleEvent, ['schedule_id', 'matter_id', 'title', 'status', 'starts_at', 'location', 'deleted_at']),
            ('reminders', Reminder, ['reminder_id', 'matter_id', 'todo_id', 'schedule_id', 'trigger_at', 'schedule_offset_seconds', 'status']),
        ):
            rows = list(await db.scalars(select(model).where(model.user_id == user_id)))
            result[name] = [{field: getattr(row, field) for field in fields} for row in rows]
        return result


def timestamp(value):
    """Normalize SQLite timestamps for comparisons with explicit UTC expectations."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=UTC).isoformat() if value.tzinfo is None else value.astimezone(UTC).isoformat()


def check_case(case_id, result, before, after, seeded):
    """Judge saved behavior against the user's requested operations, not model self-report."""
    assert result['outcome'] in {'answer', 'needs_clarification'}, result
    todos, schedules, reminders = after['todos'], after['schedules'], after['reminders']
    if case_id in {'missing_time', 'ambiguous_schedule'}:
        assert result['outcome'] == 'needs_clarification', result
        assert before == after, 'clarification must not write business records'
        return
    assert result['outcome'] == 'answer', result
    if case_id in {'todo_only', 'duplicate_request'}:
        title = '整理销售品清单' if case_id == 'todo_only' else '提交销售材料'
        assert len(todos) == 1 and todos[0]['title'] == title and len(schedules) == 0
        assert len(after['matters']) == 1 and not reminders
        assert todos[0]['due_at'] is None and todos[0]['reminder_at'] is None
    elif case_id == 'schedule_only':
        assert len(schedules) == 1 and schedules[0]['title'] == '产品讨论' and not todos
        assert timestamp(schedules[0]['starts_at']) == '2030-05-09T07:00:00+00:00'
        assert schedules[0]['location'] == '会议室A'
    elif case_id == 'schedule_relative_alert':
        assert len(schedules) == 1 and not todos and len(reminders) == 1
        assert reminders[0]['todo_id'] is None and reminders[0]['schedule_offset_seconds'] == -1800
        assert timestamp(reminders[0]['trigger_at']) == '2030-10-02T06:30:00+00:00'
    elif case_id == 'move_own_alert':
        assert len(schedules) == 1 and not todos and len(reminders) == 1
        assert schedules[0]['schedule_id'] == before['schedules'][0]['schedule_id']
        assert reminders[0]['reminder_id'] == before['reminders'][0]['reminder_id']
        assert reminders[0]['schedule_offset_seconds'] == -1800
        assert timestamp(reminders[0]['trigger_at']) == '2030-07-01T08:30:00+00:00'
    elif case_id == 'combined':
        assert len(schedules) == 1 and len(todos) == 1 and len(after['matters']) == 1
        assert todos[0]['matter_id'] == schedules[0]['matter_id']
        assert len(reminders) == 1 and reminders[0]['schedule_id'] is None
        assert timestamp(todos[0]['due_at']) == '2030-05-31T09:00:00+00:00'
        assert timestamp(schedules[0]['starts_at']) == '2030-06-01T07:00:00+00:00'
        assert timestamp(reminders[0]['trigger_at']) == '2030-05-31T08:00:00+00:00'
        assert todos[0]['title'] == '准备材料'
    elif case_id == 'append_todo':
        assert schedules == before['schedules'] and len(todos) == 1 and todos[0]['title'] == '核对报价'
        assert todos[0]['matter_id'] == seeded['matter_id'] and len(after['matters']) == 1
    elif case_id in {'rename_schedule', 'find_existing_schedule'}:
        assert len(schedules) == 1 and schedules[0]['title'] == '团建'
        for field in ('schedule_id', 'starts_at', 'location'):
            assert schedules[0][field] == before['schedules'][0][field]
        assert after['matters'][0]['title'] == before['matters'][0]['title']
    elif case_id == 'edit_todo':
        assert schedules == before['schedules'] and len(todos) == 1
        assert todos[0]['todo_id'] == before['todos'][0]['todo_id']
        assert todos[0]['title'] == '整理清单' and todos[0]['status'] == 'done'
    elif case_id == 'find_existing_todo':
        assert schedules == before['schedules'] and len(todos) == 1
        assert todos[0]['todo_id'] == before['todos'][0]['todo_id'] and todos[0]['title'] == '整理清单'
        assert todos[0]['reminder_at'] == before['todos'][0]['reminder_at']
        assert todos[0]['status'] == 'open' and len(reminders) == 1 and reminders[0]['status'] == 'pending'
    elif case_id == 'relative_reminder':
        assert schedules == before['schedules'] and len(todos) == 1
        assert len(reminders) == 1 and reminders[0]['schedule_id'] == schedules[0]['schedule_id']
        assert reminders[0]['schedule_offset_seconds'] == -3600
        assert timestamp(reminders[0]['trigger_at']) == '2030-07-01T06:00:00+00:00'
        assert todos[0]['due_at'] is None
    elif case_id == 'move_with_dependency':
        assert len(schedules) == 1 and schedules[0]['schedule_id'] == before['schedules'][0]['schedule_id']
        assert timestamp(schedules[0]['starts_at']) == '2030-07-01T09:00:00+00:00'
        relative = next(row for row in reminders if row['schedule_offset_seconds'] is not None)
        absolute = next(row for row in reminders if row['schedule_offset_seconds'] is None)
        assert timestamp(relative['trigger_at']) == '2030-07-01T08:00:00+00:00'
        assert timestamp(absolute['trigger_at']) == '2030-06-30T01:00:00+00:00'
        assert {row['title']: row['due_at'] for row in todos} == {row['title']: row['due_at'] for row in before['todos']}
    elif case_id == 'cancel_preserves_todos':
        assert len(schedules) == 1 and schedules[0]['status'] == 'cancelled'
        assert len(todos) == 2 and all(row['status'] == 'open' for row in todos)
        assert next(row for row in reminders if row['schedule_offset_seconds'] is None)['status'] == 'pending'
        assert next(row for row in reminders if row['schedule_offset_seconds'] is not None)['status'] == 'cancelled'


async def trace(app, user_id):
    """Record real model decisions, offered operations and actual HTTP receipts for diagnosis."""
    async with app.state.session_factory() as db:
        rows = list(await db.scalars(select(NexusRun).where(NexusRun.user_id == user_id).order_by(NexusRun.created_at)))
        turns = []
        for row in rows:
            context = row.context_json or {}; output = (row.result_json or {}).get('final_text')
            turns.append({'local_run_id': row.local_run_id, 'workflow_execution_id': row.workflow_execution_id,
                          'offered_operations': [op['operation_id'] for op in context.get('api_operations', [])],
                          'model_output': output, 'outcome': context.get('unified_intake_result'),
                          'actual_calls': (context.get('api_flow') or {}).get('receipts', [])})
        return turns


async def evaluate(args):
    """Run selected real-AI use cases with explicit credentials and isolated application storage."""
    args.output_dir.mkdir(parents=True, exist_ok=True)
    database = args.output_dir / 'assistant.db'
    if database.exists():
        raise RuntimeError('Output database already exists; use a fresh output directory.')
    settings = Settings(_env_file=args.env_file, environment='test',
                        database_url=f'sqlite+aiosqlite:///{database}', allow_schema_create=True,
                        commute_provider_mode='mock', commute_auto_ai_enabled=False, commute_auto_weather_enabled=False,
                        matter_ai_timeout_seconds=180)
    app = create_app(settings)
    chosen = set(args.cases.split(',')) if args.cases else {case[0] for case in CASES}
    if chosen - {case[0] for case in CASES}:
        raise RuntimeError('Unknown case IDs')
    reports = []
    async with app.router.lifespan_context(app):
        for case_id, text, preset in CASES:
            if case_id not in chosen:
                continue
            started = time.monotonic(); report = {'case': case_id, 'input': text, 'status': 'running'}
            print(json.dumps({'case': case_id, 'status': 'started'}, ensure_ascii=False), flush=True)
            async with app.state.session_factory() as db:
                user = User(email=f'{uuid4()}@record-evaluation.invalid', display_name='隔离AI验收', password_verifier='disabled-evaluation-login', status='active')
                db.add(user); await db.commit(); await db.refresh(user); user_id = user.user_id
            try:
                transport = httpx.ASGITransport(app=partial(dispatch_local, app, user_id=user_id))
                async with httpx.AsyncClient(transport=transport, base_url='http://isolated-assistant') as client:
                    seeded = await seed(client, preset)
                before = await snapshot(app, user_id)
                async with app.state.session_factory() as db:
                    user = await db.get(User, user_id)
                    request_key = str(uuid4())
                    task = asyncio.create_task(operation_intake.intake(
                        ExecutionContext(app, user_id), db, user, text=text, idempotency_key=request_key,
                        matter_id=None if case_id.startswith('find_existing_') else seeded.get('matter_id')))
                    while not task.done():
                        await asyncio.wait({task}, timeout=20)
                        if not task.done():
                            print(json.dumps({'case': case_id, 'status': 'model_running', 'elapsed_seconds': round(time.monotonic()-started)}), flush=True)
                        if time.monotonic() - started > 420:
                            task.cancel(); await asyncio.gather(task, return_exceptions=True)
                            raise TimeoutError('Use case exceeded 420 seconds')
                    result = task.result()
                    if case_id == 'duplicate_request':
                        replay = await operation_intake.intake(ExecutionContext(app, user_id), db, user, text=text, idempotency_key=request_key)
                        assert replay['local_run_id'] == result['local_run_id']
                after = await snapshot(app, user_id)
                report.update(result=result, before=before, after=after)
                check_case(case_id, result, before, after, seeded)
                report['status'] = 'passed'
            except Exception as exc:
                report.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:1800])
                report['after'] = await snapshot(app, user_id)
            report['turns'] = await trace(app, user_id)
            if report['status'] == 'passed' and case_id in {'ambiguous_schedule', 'missing_time'}:
                try:
                    last = json.loads(report['turns'][-1]['model_output'])
                    assert last['decision'] in {'ask_user', 'clarify'}, 'AI must generate the user question itself'
                except Exception as exc:
                    report.update(status='failed', error_type=type(exc).__name__, error=str(exc))
            report['elapsed_seconds'] = round(time.monotonic()-started, 2)
            reports.append(report)
            write_json(args.output_dir / f'{case_id}.json', report)
            write_json(args.output_dir / 'summary.json', reports)
            print(json.dumps({key: report[key] for key in ('case', 'status', 'elapsed_seconds')}, ensure_ascii=False), flush=True)
    print(json.dumps({'passed': sum(r['status']=='passed' for r in reports), 'failed': sum(r['status']=='failed' for r in reports), 'report': str(args.output_dir / 'summary.json')}, ensure_ascii=False), flush=True)


def main():
    """Accept explicit local configuration and never run model calls on import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--cases')
    asyncio.run(evaluate(parser.parse_args()))


if __name__ == '__main__':
    main()
