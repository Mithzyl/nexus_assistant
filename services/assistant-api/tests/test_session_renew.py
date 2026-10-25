"""Rolling seven-day sessions: boundaries, revocation, concurrency and devices."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from assistant_api.core.security import digest_token
from assistant_api.models import UserSession
from tests.conftest import auth


async def _session(h, token):
    async with h.app.state.session_factory() as db:
        return await db.scalar(select(UserSession).where(UserSession.token_digest == digest_token(token)))


@pytest.mark.asyncio
async def test_login_is_seven_days_and_renew_keeps_token(harness):
    h = harness
    response = await h.client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass"})
    assert response.status_code == 200
    payload = response.json()
    assert "server_time" in payload
    expires = datetime.fromisoformat(payload["expires_at"].replace("Z", "+00:00"))
    server = datetime.fromisoformat(payload["server_time"].replace("Z", "+00:00"))
    assert timedelta(days=6, hours=23) < expires - server <= timedelta(days=7)
    token = payload["token"]
    renewed = await h.client.post("/api/v1/auth/session/renew", headers=auth(token))
    assert renewed.status_code == 200
    body = renewed.json()
    assert body["token_rotated"] is False
    assert body["user"]["email"] == "a@example.com"
    assert (await h.client.get("/api/v1/auth/me", headers=auth(token))).status_code == 200
    # A normal read after renewal did not move the deadline.
    before = (await _session(h, token)).expires_at
    await h.client.get("/api/v1/auth/me", headers=auth(token))
    after = (await _session(h, token)).expires_at
    assert after == before


@pytest.mark.asyncio
async def test_expired_and_revoked_sessions_are_not_resurrected(harness):
    h = harness
    token = h.token_a
    session = await _session(h, token)
    async with h.app.state.session_factory() as db:
        row = await db.get(UserSession, session.session_id)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db.commit()
    expired = await h.client.post("/api/v1/auth/session/renew", headers=auth(token))
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "session_expired"

    token_b = h.token_b
    session_b = await _session(h, token_b)
    async with h.app.state.session_factory() as db:
        row = await db.get(UserSession, session_b.session_id)
        row.revoked_at = datetime.now(UTC)
        await db.commit()
    revoked = await h.client.post("/api/v1/auth/session/renew", headers=auth(token_b))
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "session_revoked"


@pytest.mark.asyncio
async def test_devices_are_independent_and_concurrent_renew_never_shortens(harness):
    h = harness
    login = await h.client.post("/api/v1/auth/login", json={"email": "a@example.com", "password": "secret-pass"})
    token2 = login.json()["token"]
    old_a = await _session(h, h.token_a)
    old_b = await _session(h, token2)
    assert old_a.session_id != old_b.session_id

    responses = await __import__("asyncio").gather(
        h.client.post("/api/v1/auth/session/renew", headers=auth(h.token_a)),
        h.client.post("/api/v1/auth/session/renew", headers=auth(token2)),
        h.client.post("/api/v1/auth/session/renew", headers=auth(h.token_a)),
    )
    assert all(r.status_code == 200 for r in responses)
    renewed_a = await _session(h, h.token_a)
    renewed_b = await _session(h, token2)
    assert renewed_a.expires_at >= old_a.expires_at
    assert renewed_b.expires_at >= old_b.expires_at
    assert renewed_a.session_id != renewed_b.session_id


@pytest.mark.asyncio
async def test_missing_token_is_invalid_session(harness):
    response = await harness.client.post("/api/v1/auth/session/renew")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_session"
