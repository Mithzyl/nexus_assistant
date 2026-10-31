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


# Only these public codes may become user-facing memory explanations.
MEMORY_MESSAGES = {
    "memory_version_conflict": "这条记忆或设置已变更，请刷新后重新确认。",
    "memory_request_conflict": "此操作标识已用于其他内容，请刷新后重新提交。",
    "memory_confirmation_required": "此内容包含敏感信息，请核对后勾选敏感信息保存确认。",
    "memory_type_confirmation_required": "请核对并确认记忆类型和字段。",
    "memory_disabled": "当前记忆已关闭，请先确认是否开启。",
    "memory_history_unavailable": "这条记忆没有可恢复的历史版本。",
    "memory_share_target_unavailable": "目标账号尚未关联或关联已解除，请重新核对关联。",
    "memory_share_fields_invalid": "共享字段已变化，请刷新后重新选择。",
}
