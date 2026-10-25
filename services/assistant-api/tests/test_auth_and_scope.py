"""Local authentication and cross-user resource isolation."""

import asyncio

from conftest import auth, create_ask


async def test_login_rejects_wrong_password(harness):
    """A wrong password and an unknown account return the same public error."""

    response = await harness.client.post(
        "/api/v1/auth/login", json={"email": "a@example.com", "password": "wrong"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


async def test_me_requires_a_session(harness):
    """No token, an unknown token and a revoked token are all just 401."""

    assert (await harness.client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await harness.client.get("/api/v1/auth/me", headers=auth("pas_not-a-real-token"))
    ).status_code == 401


async def test_me_returns_only_local_identity(harness):
    """The profile response contains no NexusPilot identifier."""

    response = await harness.client.get("/api/v1/auth/me", headers=auth(harness.token_a))
    body = response.json()
    assert response.status_code == 200
    assert body["user_id"] == harness.user_a
    assert set(body) == {"user_id", "email", "display_name"}


async def test_logout_revokes_only_the_presented_session(harness):
    """Signing out one device must not sign out the other."""

    second_token = (
        await harness.client.post(
            "/api/v1/auth/login",
            json={"email": "a@example.com", "password": "secret-pass"},
        )
    ).json()["token"]

    assert (
        await harness.client.post("/api/v1/auth/logout", headers=auth(second_token))
    ).status_code == 204
    assert (
        await harness.client.get("/api/v1/auth/me", headers=auth(second_token))
    ).status_code == 401
    assert (
        await harness.client.get("/api/v1/auth/me", headers=auth(harness.token_a))
    ).status_code == 200


async def test_another_user_cannot_read_a_run(harness):
    """A valid session for the wrong user gets 404, not 403, to avoid leaking ids."""

    created = await create_ask(harness, token=harness.token_a, key="scope-key-0001")
    assert created.status_code == 202
    local_run_id = created.json()["local_run_id"]

    response = await harness.client.get(
        f"/api/v1/asks/{local_run_id}", headers=auth(harness.token_b)
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"


async def test_ask_list_is_scoped_to_the_caller(harness):
    """Listing never returns another user's request text."""

    created_a = await create_ask(
        harness, token=harness.token_a, key="list-key-0001", text="甲的请求"
    )
    # Let the in-memory single-connection test database settle the background
    # observer before starting the second run; production uses a real pool.
    for _ in range(50):
        row = await harness.client.get(
            f"/api/v1/asks/{created_a.json()['local_run_id']}",
            headers=auth(harness.token_a),
        )
        if row.json()["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
            break
        await asyncio.sleep(0.01)
    await create_ask(harness, token=harness.token_b, key="list-key-0002", text="乙的请求")

    mine = (await harness.client.get("/api/v1/asks", headers=auth(harness.token_a))).json()
    assert [row["request_text"] for row in mine] == ["甲的请求"]


async def test_expired_session_is_rejected(harness):
    """A live-looking token with an expired database row cannot access resources."""

    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from assistant_api.core.security import digest_token
    from assistant_api.models import UserSession

    async with harness.app.state.session_factory() as session:
        row = await session.scalar(
            select(UserSession).where(
                UserSession.token_digest == digest_token(harness.token_a)
            )
        )
        assert row is not None
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    response = await harness.client.get("/api/v1/auth/me", headers=auth(harness.token_a))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"
