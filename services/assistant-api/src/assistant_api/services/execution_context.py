"""Trusted execution identity shared by HTTP chat and persisted follow-up jobs."""
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """Carry app services and an already checked local user without storing session tokens."""
    app: Any
    user_id: str
    headers: dict = field(default_factory=dict)
    source: str = 'chat'
    follow_up_execution_id: str | None = None
    lease_owner: str | None = None
    follow_up_id: str | None = None

    @classmethod
    def from_request(cls, request, user):
        """Adapt a browser request after the normal authentication dependency has run."""
        return cls(request.app, user.user_id, dict(request.headers))


async def dispatch_local(app, scope, receive, send, *, user_id):
    """Attach a server-owned identity to an in-process ASGI call, unavailable to HTTP clients."""
    scoped = dict(scope)
    scoped['assistant_execution_user_id'] = user_id
    await app(scoped, receive, send)
