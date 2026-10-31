"""Necessary login regressions; remote transport is simulated, never a paid model."""

import pytest
import httpx
import json
from assistant_api.routers.nexus_login import linked_local_user
from assistant_api.core.errors import AssistantError


async def test_unlinked_login_never_creates_or_merges_local_account():
    """A Nexus subject without an external mapping must use explicit linking."""
    with pytest.raises(AssistantError) as error:
        await linked_local_user(None, {"subject": "nexus-owner", "external_user_id": None})
    assert error.value.code == "nexus_account_not_linked"


async def test_disabled_local_account_cannot_use_nexus_login():
    """An external assertion cannot reactivate the local account."""
    class Database:
        """Return the disabled local subject without creating records."""
        async def get(self, model, key):
            """Provide the disabled test record."""
            return type("User", (), {"status": "disabled"})()
    with pytest.raises(AssistantError):
        await linked_local_user(Database(), {"subject": "nexus-owner", "external_user_id": "local-user"})


async def test_exchange_has_no_external_identity_and_link_uses_authenticated_subject():
    """Check the actual HTTP envelope without printing application credentials."""
    from assistant_api.nexus_client import NexusPilotClient
    calls = []
    def handle(request):
        """Record only protocol fields needed by the assertions."""
        calls.append((request.url.path, request.headers.get("x-external-user-id"), json.loads(request.content)))
        return httpx.Response(200, json={"subject":"owner","external_user_id":"local-user"})
    client = NexusPilotClient(base_url="https://nexus.test",application_id="personal_assistant",
                              application_token="test-only",transport=httpx.MockTransport(handle))
    try:
        await client.exchange_login(code="c"*43,redirect_uri="https://pa.test/nexus-callback",verifier="v"*43)
        await client.begin_account_link("local-user")
    finally:
        await client.aclose()
    assert calls[0][0:2] == ("/api/v1/auth/app/exchange",None)
    assert set(calls[0][2]) == {"code","redirect_uri","code_verifier"}
    assert calls[1] == ("/api/v1/app/me/account-link-requests","local-user",{})


async def test_link_requires_local_session_and_original_login_remains_available(harness):
    """Adding optional login does not replace local credentials or accept caller-selected identities."""
    response = await harness.client.post("/api/v1/auth/nexus/link")
    assert response.status_code == 401
    original = await harness.client.post("/api/v1/auth/login", json={"email":"a@example.com","password":"secret-pass"})
    assert original.status_code == 200
    disabled = await harness.client.get("/api/v1/auth/nexus/options")
    assert disabled.json() == {"enabled":False}
