"""Public registration creates no sample business data."""
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from assistant_api.models import User, UserSession, Matter, ScheduleEvent, MatterTodo, Reminder, NexusRun
from assistant_api.core.security import verify_password
from conftest import auth
PAYLOAD = {'email':'New@Example.test', 'display_name':'新用户', 'password':'test-register-pass'}

async def test_register_empty_account_and_session(harness):
    h = harness
    response = await h.client.post('/api/v1/auth/register', json=PAYLOAD)
    assert response.status_code == 201, response.text
    body = response.json()
    me = await h.client.get('/api/v1/auth/me', headers=auth(body['token']))
    assert me.json() == {'user_id':body['user_id'], 'email':'new@example.test', 'display_name':'新用户'}
    async with h.app.state.session_factory() as db:
        user = await db.get(User, body['user_id'])
        assert user.password_verifier != PAYLOAD['password']
        assert verify_password(PAYLOAD['password'], user.password_verifier)
        for model in [Matter, ScheduleEvent, MatterTodo, Reminder, NexusRun]:
            assert await db.scalar(select(func.count()).select_from(model).where(model.user_id == user.user_id)) == 0
        assert await db.scalar(select(func.count()).select_from(UserSession).where(UserSession.user_id == user.user_id)) == 1
    assert (await h.client.post('/api/v1/auth/login', json={'email':PAYLOAD['email'],'password':PAYLOAD['password']})).status_code == 200

async def test_duplicate_does_not_reset_password(harness):
    h = harness
    assert (await h.client.post('/api/v1/auth/register', json=PAYLOAD)).status_code == 201
    response = await h.client.post('/api/v1/auth/register', json={**PAYLOAD,'email':' NEW@example.test ','password':'different-pass'})
    assert response.status_code == 409
    assert response.json()['error']['code'] == 'email_registered'
    assert (await h.client.post('/api/v1/auth/login',json={'email':PAYLOAD['email'],'password':PAYLOAD['password']})).status_code == 200

@pytest.mark.parametrize('field,value,code',[('password','short','password_too_short'),('email','invalid','invalid_email'),('display_name','   ','invalid_display_name')])
async def test_invalid_registration(harness,field,value,code):
    response=await harness.client.post('/api/v1/auth/register',json={**PAYLOAD,field:value})
    assert response.status_code == 422
    assert response.json()['error']['code'] == code

async def test_registration_disabled(harness):
    harness.app.state.settings.register_enabled=False
    assert (await harness.client.get('/api/v1/auth/register')).json()['enabled'] is False
    assert (await harness.client.post('/api/v1/auth/register',json=PAYLOAD)).status_code == 403

async def test_invite_policy(harness):
    h=harness
    h.app.state.settings.register_invite_code=SecretStr('测试邀请码-private')
    assert (await h.client.get('/api/v1/auth/register')).json()=={'enabled':True,'invite_required':True}
    for code in ['', 'wrong']:
        response=await h.client.post('/api/v1/auth/register',json={**PAYLOAD,'invite_code':code})
        assert response.status_code==403
        assert response.json()['error']['code']=='invalid_invite_code'
    assert (await h.client.post('/api/v1/auth/register',json={**PAYLOAD,'invite_code':'测试邀请码-private'})).status_code==201
