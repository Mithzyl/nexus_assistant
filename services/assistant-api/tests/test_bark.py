"""Per-account Bark binding, status, redaction and sender outcomes."""

import httpx
import pytest
import pytest_asyncio

from assistant_api.core.config import Settings
from assistant_api.core.security import hash_password
from assistant_api.main import create_app
from assistant_api.models import User


def auth(token):
    return {"Authorization": "Bearer " + token}


@pytest_asyncio.fixture
async def bark_app():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"code": 200, "message": "success"})

    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        allow_schema_create=True,
    )
    app = create_app(settings, bark_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as db:
            users = {}
            for email in ("bark-a@example.test", "bark-b@example.test"):
                user = User(email=email, display_name=email,
                            password_verifier=hash_password("password-1"), status="active")
                db.add(user); await db.flush(); users[email] = user.user_id
            await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            tokens = {}
            for email in users:
                r = await client.post("/api/v1/auth/login", json={"email": email, "password": "password-1"})
                assert r.status_code == 200
                tokens[email] = r.json()["token"]
            yield client, users, tokens, calls


@pytest.mark.asyncio
async def test_binding_is_per_account_and_never_returns_key(bark_app):
    client, users, tokens, calls = bark_app
    ta, tb = tokens["bark-a@example.test"], tokens["bark-b@example.test"]
    initial = await client.get("/api/v1/commute/bark", headers=auth(ta))
    assert initial.status_code == 200
    assert initial.json()["state"] == "unconfigured"
    assert "device_key" not in initial.text

    key = "device-key-for-test-only"
    bound = await client.post("/api/v1/commute/bark/binding", headers=auth(ta), json={"device_key": key})
    assert bound.status_code == 201, bound.text
    assert bound.json()["state"] == "disabled"
    assert bound.json()["auto_reminders_enabled"] is False
    assert key not in bound.text and "device_key" not in bound.text
    # Binding is not visible to another account.
    other = await client.get("/api/v1/commute/bark", headers=auth(tb))
    assert other.json()["state"] == "unconfigured"

    # Saving a key does not send and does not enable timed reminders.
    assert calls == []
    tested = await client.post("/api/v1/commute/bark/test", headers=auth(ta), json={"confirm_send": True})
    assert tested.status_code == 200, tested.text
    assert tested.json()["status"] == "accepted"
    assert key not in tested.text
    after = (await client.get("/api/v1/commute/bark", headers=auth(ta))).json()
    assert after["state"] == "disabled" and after["last_test_status"] == "accepted"

    enabled = await client.patch("/api/v1/commute/bark", headers=auth(ta), json={"auto_reminders_enabled": True})
    assert enabled.status_code == 200 and enabled.json()["state"] == "enabled"
    removed = await client.delete("/api/v1/commute/bark/binding", headers=auth(ta))
    assert removed.status_code == 200 and removed.json()["state"] == "unconfigured"
    assert key not in removed.text


@pytest.mark.asyncio
async def test_replace_and_unbind_clear_previous_key(bark_app):
    client, _, tokens, calls = bark_app
    token = tokens["bark-a@example.test"]
    first, second = "first-device-key", "second-device-key"
    await client.post("/api/v1/commute/bark/binding", headers=auth(token), json={"device_key": first})
    await client.post("/api/v1/commute/bark/binding", headers=auth(token), json={"device_key": second})
    await client.post("/api/v1/commute/bark/test", headers=auth(token), json={"confirm_send": True})
    assert calls[-1].url.path.endswith(second)
    await client.delete("/api/v1/commute/bark/binding", headers=auth(token))
    missing = await client.post("/api/v1/commute/bark/test", headers=auth(token), json={"confirm_send": True})
    assert missing.status_code == 409


@pytest.mark.asyncio
async def test_auto_toggle_requires_binding(bark_app):
    client, _, tokens, _ = bark_app
    token = tokens["bark-a@example.test"]
    assert (await client.patch("/api/v1/commute/bark", headers=auth(token),
                               json={"auto_reminders_enabled": True})).status_code == 409
    assert (await client.post("/api/v1/commute/bark/test", headers=auth(token),
                              json={"confirm_send": True})).status_code == 409


@pytest.mark.parametrize(("response", "expected"), [
    (httpx.Response(400, json={"code": 400}), "failed"),
    (httpx.Response(200, json={"code": 400}), "failed"),
    (httpx.Response(503), "unknown"),
])
async def test_sender_outcomes_are_not_auto_accepted(response, expected):
    def handler(_request): return response
    settings = Settings(environment="test", database_url="sqlite+aiosqlite:///:memory:",
                        allow_schema_create=True)
    app = create_app(settings, bark_transport=httpx.MockTransport(handler))
    async with app.router.lifespan_context(app):
        async with app.state.session_factory() as db:
            user = User(email="outcome@test", display_name="O",
                        password_verifier=hash_password("password-1"), status="active")
            db.add(user); await db.commit()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            token = (await client.post("/api/v1/auth/login", json={"email": "outcome@test", "password": "password-1"})).json()["token"]
            await client.post("/api/v1/commute/bark/binding", headers=auth(token),
                              json={"device_key": "outcome-test-key"})
            result = await client.post("/api/v1/commute/bark/test", headers=auth(token),
                                       json={"confirm_send": True})
            assert result.json()["status"] == expected
            assert result.json()["accepted"] is False


@pytest.mark.asyncio
async def test_short_key_error_does_not_echo_key(bark_app):
    client, _, tokens, _ = bark_app
    token = tokens["bark-a@example.test"]
    key = "short"
    response = await client.post("/api/v1/commute/bark/binding", headers=auth(token),
                                 json={"device_key": key})
    assert response.status_code == 422
    assert key not in response.text
