"""Current App API identity: trusted local header, no principal resolution."""

import pytest
from conftest import auth, create_ask
from sqlalchemy import func, select

from assistant_api.models import NexusIdentityLink, NexusRun


def upstream_calls(harness) -> list[dict]:
    return list(harness.fake.calls)


async def test_first_ask_uses_current_headers_without_principal_resolve(harness):
    """The client asserts the local user id and never calls /principals/resolve."""

    response = await create_ask(harness, token=harness.token_a, key="map-key-0001")
    assert response.status_code == 202

    assert not [c for c in upstream_calls(harness) if c["path"].endswith("/principals/resolve")]
    run_call = [c for c in upstream_calls(harness) if c["path"] == "/api/v1/app/runs"][0]
    assert run_call["headers"]["x-application-id"] == "personal_assistant"
    assert run_call["headers"]["x-external-user-id"] == harness.user_a
    assert not any(key.startswith("x-nexuspilot-") for key in run_call["headers"])
    assert "user_id" not in run_call["body"]
    assert "principal_id" not in run_call["body"]


async def test_local_run_projection_has_no_remote_principal(harness):
    """Current NexusPilot derives the owner; it returns no principal for local storage."""

    response = await create_ask(harness, token=harness.token_a, key="map-key-0002")
    local_run_id = response.json()["local_run_id"]
    async with harness.app.state.session_factory() as session:
        row = await session.scalar(
            select(NexusRun).where(NexusRun.local_run_id == local_run_id)
        )
    assert row is not None
    assert row.nexus_principal_id is None
    assert row.nexus_run_id == "run_remote_1"


async def test_each_user_gets_an_independent_trusted_subject(harness):
    """The same application asserts different local subject ids for different users."""

    await create_ask(harness, token=harness.token_a, key="map-key-0003")
    await create_ask(harness, token=harness.token_b, key="map-key-0004")

    run_calls = [c for c in upstream_calls(harness) if c["path"] == "/api/v1/app/runs"]
    subjects = {call["headers"]["x-external-user-id"] for call in run_calls}
    assert subjects == {harness.user_a, harness.user_b}

    async with harness.app.state.session_factory() as session:
        assert (
            await session.scalar(select(func.count()).select_from(NexusIdentityLink))
        ) == 0


async def test_ask_requires_authentication_before_any_upstream_call(harness):
    """An anonymous request never reaches NexusPilot."""

    response = await harness.client.post(
        "/api/v1/asks",
        json={"text": "匿名请求", "idempotency_key": "map-key-0005"},
        headers=auth("pas_invalid"),
    )
    assert response.status_code == 401
    assert upstream_calls(harness) == []
