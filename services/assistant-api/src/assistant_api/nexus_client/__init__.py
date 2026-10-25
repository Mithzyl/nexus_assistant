"""Public surface of the NexusPilot client package."""

from assistant_api.nexus_client.client import NexusPilotClient
from assistant_api.nexus_client.errors import (
    NexusAuthError,
    NexusConflictError,
    NexusError,
    NexusInvalidRequestError,
    NexusNotFoundError,
    NexusPermissionError,
    NexusProtocolError,
    NexusTimeoutError,
    NexusUnavailableError,
)
from assistant_api.nexus_client.models import (
    MemoryHit,
    MemoryQueryRequest,
    MemoryQueryResponse,
    RoleBinding,
    RunCreateRequest,
    RunView,
    SseEvent,
    WorkflowCreateRequest,
    WorkflowResultView,
    WorkflowView,
)

__all__ = [
    "MemoryHit",
    "MemoryQueryRequest",
    "MemoryQueryResponse",
    "NexusAuthError",
    "NexusConflictError",
    "NexusError",
    "NexusInvalidRequestError",
    "NexusNotFoundError",
    "NexusPermissionError",
    "NexusPilotClient",
    "NexusProtocolError",
    "NexusTimeoutError",
    "NexusUnavailableError",
    "RoleBinding",
    "RunCreateRequest",
    "RunView",
    "SseEvent",
    "WorkflowCreateRequest",
    "WorkflowResultView",
    "WorkflowView",
]
