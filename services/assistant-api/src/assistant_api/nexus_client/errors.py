"""Typed failures raised by the NexusPilot client.

The distinction that matters to callers is not the HTTP number but what may be
retried: authentication and permission failures are permanent, unavailable and
throttled are retryable, and a mid-stream disconnect is neither - it is a resume.
"""


class NexusError(Exception):
    """Base class carrying a stable code for API responses and logs."""

    code = "nexus_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code


class NexusAuthError(NexusError):
    """The application credential is missing, revoked, or expired."""

    code = "nexus_application_unauthenticated"


class NexusPermissionError(NexusError):
    """The application is not allowed this model, tool, principal or resource."""

    code = "nexus_permission_denied"


class NexusNotFoundError(NexusError):
    """The remote run, workflow, principal or resource does not exist."""

    code = "nexus_resource_not_found"


class NexusInvalidRequestError(NexusError):
    """NexusPilot rejected the request payload; resending it unchanged will fail again."""

    code = "nexus_invalid_request"


class NexusConflictError(NexusError):
    """An idempotency or state conflict; the caller must reconcile before retrying."""

    code = "nexus_conflict"


class NexusTimeoutError(NexusError):
    """A deadline expired with an unknown remote outcome."""

    code = "nexus_timeout"
    retryable = True


class NexusUnavailableError(NexusError):
    """NexusPilot or a dependency was unreachable or reported a temporary failure."""

    code = "nexus_unavailable"
    retryable = True


class NexusProtocolError(NexusError):
    """The remote response did not match the agreed contract.

    Raised for a malformed SSE frame, a dropped stream, or a payload without the
    identifiers needed to resume. It is never treated as a successful result.
    """

    code = "nexus_protocol_error"


STATUS_MAP: dict[int, type[NexusError]] = {
    400: NexusInvalidRequestError,
    401: NexusAuthError,
    403: NexusPermissionError,
    404: NexusNotFoundError,
    409: NexusConflictError,
    422: NexusInvalidRequestError,
    429: NexusUnavailableError,
    502: NexusUnavailableError,
    503: NexusUnavailableError,
    504: NexusTimeoutError,
}


def error_for_status(status_code: int, detail: str) -> NexusError:
    """Map an HTTP status to the narrowest error type the caller can act on."""

    return STATUS_MAP.get(status_code, NexusProtocolError)(detail)
