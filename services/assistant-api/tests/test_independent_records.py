"""Focused checks for independent calendar/todo writes; temporary SQLite, no provider requests.

Run only this file for the new behavior; existing suites are intentionally not
included. Real-model use cases are a separate opt-in script.
"""
from datetime import datetime
import pytest
from sqlalchemy import func, select
from conftest import auth
from assistant_api.models import Matter, MatterTodo, Reminder, ScheduleEvent


@pytest.mark.asyncio
async def test_todo_create_is_complete_without_schedule_and_replays(harness):
    """One todo request creates its parent and todo without any calendar prerequisite."""
    h = harness
    body = {'idempotency_key': 'todo-independent-001', 'title': '整理销售品清单'}
    created = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json=body)
    assert created.status_code == 201, created.text
    result = created.json()
    assert result['todo']['title'] == body['title'] and result['applied']
    mid = result['matter']['matter_id']
    replay = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json=body)
    assert replay.status_code == 201 and replay.json()['replayed']
    assert replay.json()['todo']['todo_id'] == result['todo']['todo_id']
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(MatterTodo).where(MatterTodo.matter_id == mid)) == 1
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent).where(ScheduleEvent.matter_id == mid)) == 0
    assert (await h.client.get(f"/api/v1/todos/{result['todo']['todo_id']}", headers=auth(h.token_b))).status_code == 404


@pytest.mark.asyncio
async def test_schedule_create_has_optional_todos_and_own_reminder(harness):
    """A calendar creation returns its saved record and optional schedule reminder, with no todo."""
    h = harness
    created = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'schedule-independent-001', 'title': '产品讨论', 'starts_at': '2030-05-09T15:00:00+08:00',
        'location': '会议室A', 'reminder_at': '2030-05-09T14:30:00+08:00'})
    assert created.status_code == 201, created.text
    value = created.json()
    assert value['schedule']['title'] == '产品讨论'
    assert len(value['reminders']) == 1
    assert value['reminders'][0]['schedule_id'] == value['schedule']['schedule_id']
    assert value['reminders'][0].get('todo_id') is None
    detail = await h.client.get(f"/api/v1/matters/{value['matter']['matter_id']}", headers=auth(h.token_a))
    assert detail.json()['todos'] == []


@pytest.mark.asyncio
async def test_combination_does_not_infer_reminder_relationship(harness):
    """Same-request records share a matter, while an absolute todo reminder stays independent."""
    h = harness
    draft = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json={
        'idempotency_key': 'combination-independent-001', 'title': '客户交流',
        'schedule': {'title': '客户交流', 'starts_at': '2030-06-01T15:00:00+08:00'},
        'todos': [{'title': '准备材料', 'due_at': '2030-05-31T17:00:00+08:00', 'reminder_at': '2030-05-31T16:00:00+08:00'}]})
    assert draft.status_code == 201, draft.text
    saved = await h.client.post(f"/api/v1/matters/drafts/{draft.json()['draft_id']}/confirm", headers={**auth(h.token_a), 'Idempotency-Key': 'combination-save-001'}, json={'expected_version': draft.json()['version']})
    assert saved.status_code == 200, saved.text
    value = saved.json()
    assert len(value['todos']) == 1 and value['schedule'] is not None
    assert value['reminders'][0]['todo_id'] == value['todos'][0]['todo_id']
    assert value['reminders'][0]['schedule_id'] is None


@pytest.mark.asyncio
async def test_explicit_relative_reminder_tracks_only_related_schedule(harness):
    """Moving/cancelling a schedule changes its relative reminder without altering other todos."""
    h = harness
    created = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'relative-schedule-001', 'title': '项目评审', 'starts_at': '2030-07-01T15:00:00+08:00', 'location': '原地点'})
    assert created.status_code == 201, created.text
    value = created.json(); mid = value['matter']['matter_id']; sid = value['schedule']['schedule_id']
    relative = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json={
        'idempotency_key': 'relative-todo-001', 'matter_id': mid, 'expected_matter_version': value['matter']['version'],
        'title': '准备评审材料', 'due_at': '2030-06-30T18:00:00+08:00',
        'schedule_reminder': {'schedule_id': sid, 'offset_seconds': -3600}})
    assert relative.status_code == 201, relative.text
    r = relative.json()
    assert r['reminders'][0]['schedule_offset_seconds'] == -3600
    absolute = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json={
        'idempotency_key': 'absolute-todo-001', 'matter_id': mid, 'expected_matter_version': r['matter']['version'],
        'title': '提交清单', 'reminder_at': '2030-06-30T09:00:00+08:00'})
    assert absolute.status_code == 201, absolute.text
    moved = await h.client.patch(f'/api/v1/schedules/{sid}', headers=auth(h.token_a), json={
        'idempotency_key': 'move-independent-001', 'expected_matter_version': absolute.json()['matter']['version'],
        'starts_at': '2030-07-01T17:00:00+08:00'})
    assert moved.status_code == 200, moved.text
    assert moved.json()['schedule']['schedule_id'] == sid and moved.json()['schedule']['location'] == '原地点'
    detail = (await h.client.get(f'/api/v1/matters/{mid}', headers=auth(h.token_a))).json()
    by_title = {item['title']: item for item in detail['todos']}
    assert by_title['准备评审材料']['reminder_at'] == '2030-07-01T08:00:00Z'
    assert by_title['准备评审材料']['due_at'] == '2030-06-30T10:00:00Z'
    assert by_title['提交清单']['reminder_at'] == '2030-06-30T01:00:00Z'
    cancelled = await h.client.delete(f'/api/v1/schedules/{sid}', headers={**auth(h.token_a), 'Idempotency-Key': 'cancel-independent-001'}, params={'expected_matter_version': detail['version']})
    assert cancelled.status_code == 200, cancelled.text
    detail = (await h.client.get(f'/api/v1/matters/{mid}', headers=auth(h.token_a))).json()
    assert all(item['status'] == 'open' for item in detail['todos'])
    reminders = {item['todo_id']: item for item in detail['reminders']}
    assert reminders[r['todo']['todo_id']]['status'] == 'cancelled'
    assert reminders[absolute.json()['todo']['todo_id']]['status'] == 'scheduled'


@pytest.mark.asyncio
async def test_todo_edit_preserves_identity_and_does_not_change_schedule(harness):
    """Independent todo editing checks the parent version and returns the same saved todo."""
    h = harness
    created = await h.client.post('/api/v1/todos', headers=auth(h.token_a), json={'idempotency_key': 'edit-todo-create-001', 'title': '旧标题'})
    assert created.status_code == 201, created.text
    value = created.json(); tid = value['todo']['todo_id']
    body = {'idempotency_key': 'edit-todo-update-001', 'expected_matter_version': value['matter']['version'], 'title': '新标题', 'status': 'done'}
    saved = await h.client.patch(f'/api/v1/todos/{tid}', headers=auth(h.token_a), json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()['todo']['todo_id'] == tid and saved.json()['todo']['status'] == 'done'
    replay = await h.client.patch(f'/api/v1/todos/{tid}', headers=auth(h.token_a), json=body)
    assert replay.status_code == 200 and replay.json()['replayed']
    reused = await h.client.patch(f'/api/v1/todos/{tid}', headers=auth(h.token_a), json={**body, 'title': '不同请求'})
    assert reused.status_code == 409
    stale = await h.client.patch(f'/api/v1/todos/{tid}', headers=auth(h.token_a), json={**body, 'idempotency_key': 'edit-stale-001'})
    assert stale.status_code == 409


@pytest.mark.asyncio
async def test_combination_failure_rolls_back_all_formal_records(harness):
    """An invalid explicit reminder relation rejects the combined write without a partial parent."""
    h = harness
    other = await h.client.post('/api/v1/schedules', headers=auth(h.token_b), json={
        'idempotency_key': 'other-calendar-001', 'title': '另一账号日程', 'starts_at': '2030-08-01T15:00:00+08:00'})
    assert other.status_code == 201, other.text
    draft = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json={
        'idempotency_key': 'invalid-combination-001', 'title': '不应部分保存',
        'schedule': {'title': '不应部分保存', 'starts_at': '2030-08-03T15:00:00+08:00'},
        'todos': [{'title': '不应创建待办', 'schedule_reminder': {'schedule_id': other.json()['schedule']['schedule_id'], 'offset_seconds': -600}}]})
    assert draft.status_code == 201, draft.text
    saved = await h.client.post(f"/api/v1/matters/drafts/{draft.json()['draft_id']}/confirm", headers={**auth(h.token_a), 'Idempotency-Key': 'invalid-combination-save-001'}, json={'expected_version': draft.json()['version']})
    assert saved.status_code == 404, saved.text
    async with h.app.state.session_factory() as db:
        assert await db.scalar(select(func.count()).select_from(Matter).where(Matter.title == '不应部分保存')) == 0
        assert await db.scalar(select(func.count()).select_from(ScheduleEvent).where(ScheduleEvent.title == '不应部分保存')) == 0
        assert await db.scalar(select(func.count()).select_from(MatterTodo).where(MatterTodo.title == '不应创建待办')) == 0


@pytest.mark.asyncio
async def test_combination_can_explicitly_bind_a_new_schedule_reminder(harness):
    """An explicit relative request may bind the same transaction's new calendar record."""
    h = harness
    draft = await h.client.post('/api/v1/matters/drafts', headers=auth(h.token_a), json={
        'idempotency_key': 'new-relative-combination-001', 'title': '联合评审',
        'schedule': {'title': '联合评审', 'starts_at': '2030-09-01T15:00:00+08:00'},
        'todos': [{'title': '带上材料', 'schedule_reminder': {'offset_seconds': -900}}]})
    assert draft.status_code == 201, draft.text
    saved = await h.client.post(f"/api/v1/matters/drafts/{draft.json()['draft_id']}/confirm", headers={**auth(h.token_a), 'Idempotency-Key': 'new-relative-combination-save-001'}, json={'expected_version': draft.json()['version']})
    assert saved.status_code == 200, saved.text
    value = saved.json()
    assert value['reminders'][0]['schedule_id'] == value['schedule']['schedule_id']
    assert value['reminders'][0]['schedule_offset_seconds'] == -900
    assert value['todos'][0]['reminder_at'] == '2030-09-01T06:45:00Z'


@pytest.mark.asyncio
async def test_schedule_title_patch_preserves_own_absolute_reminder(harness):
    """Changing only a schedule title retains its reminder time and updates the owned reminder label."""
    h = harness
    created = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'own-reminder-title-001', 'title': '原日程', 'starts_at': '2030-10-01T15:00:00+08:00',
        'reminder_at': '2030-10-01T14:00:00'})
    assert created.status_code == 201, created.text
    value = created.json(); sid = value['schedule']['schedule_id']
    assert value['reminders'][0]['trigger_at'] == '2030-10-01T06:00:00Z'
    updated = await h.client.patch(f'/api/v1/schedules/{sid}', headers=auth(h.token_a), json={
        'idempotency_key': 'own-reminder-title-patch-001', 'expected_matter_version': value['matter']['version'], 'title': '新日程'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['reminders'][0]['title'] == '提醒：新日程'
    assert updated.json()['reminders'][0]['trigger_at'] == value['reminders'][0]['trigger_at']
    assert updated.json()['reminders'][0]['reminder_id'] == value['reminders'][0]['reminder_id']


@pytest.mark.asyncio
async def test_explicit_dependency_migration_preserves_existing_reminder(tmp_path):
    """The new nullable dependency column upgrades an old reminder row without changing it."""
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    from assistant_api.models import Base, User
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('record_dependency_migration', Path(__file__).resolve().parents[1] / 'scripts/migrate_sqlite.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    upgrade = migration.upgrade
    url = f"sqlite+aiosqlite:///{tmp_path / 'old-reminder.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("INSERT INTO users (user_id,email,display_name,password_verifier,status,created_at,updated_at) VALUES ('migration-user','migration@example.invalid','迁移用例','disabled','active',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        await conn.execute(text("INSERT INTO reminders (reminder_id,user_id,title,trigger_at,status,created_at,updated_at) VALUES ('old-reminder','migration-user','保留原提醒','2030-01-01 00:00:00','pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"))
        await conn.execute(text('ALTER TABLE reminders DROP COLUMN schedule_offset_seconds'))
    await engine.dispose()
    await upgrade(url)
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        row = (await conn.execute(text("SELECT title,status,schedule_offset_seconds FROM reminders WHERE reminder_id='old-reminder'"))).one()
        assert tuple(row) == ('保留原提醒', 'pending', None)
    await engine.dispose()


@pytest.mark.asyncio
async def test_schedule_own_relative_reminder_moves_without_creating_todo(harness):
    """A calendar's own explicitly relative alert moves with it and never creates a todo."""
    h = harness
    created = await h.client.post('/api/v1/schedules', headers=auth(h.token_a), json={
        'idempotency_key': 'schedule-own-relative-001', 'title': '电话沟通',
        'starts_at': '2030-10-02T15:00:00+08:00', 'reminder_offset_seconds': -1800})
    assert created.status_code == 201, created.text
    result = created.json(); sid = result['schedule']['schedule_id']
    reminder = result['reminders'][0]
    assert reminder.get('todo_id') is None and reminder['schedule_offset_seconds'] == -1800
    assert reminder['trigger_at'] == '2030-10-02T06:30:00Z'
    updated = await h.client.patch(f'/api/v1/schedules/{sid}', headers=auth(h.token_a), json={
        'idempotency_key': 'schedule-own-relative-move-001', 'expected_matter_version': result['matter']['version'],
        'starts_at': '2030-10-02T17:00:00+08:00'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['reminders'][0]['reminder_id'] == reminder['reminder_id']
    assert updated.json()['reminders'][0]['trigger_at'] == '2030-10-02T08:30:00Z'
    detail = (await h.client.get(f"/api/v1/matters/{result['matter']['matter_id']}", headers=auth(h.token_a))).json()
    assert detail['todos'] == []
