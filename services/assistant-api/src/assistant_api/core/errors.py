"""Expected failures with stable public codes.

Every error the client can act on carries a machine code plus an HTTP status. The
code, not the sentence, is the contract: wording may change, codes may not.
"""

from dataclasses import dataclass


@dataclass
class AssistantError(Exception):
    """Base class for failures that are safe to expose through the API."""

    code: str
    message: str
    status_code: int = 400

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class UnauthenticatedError(AssistantError):
    """Report a missing, expired or revoked local session."""

    def __init__(self, message: str = "登录状态无效或已过期。") -> None:
        super().__init__("unauthenticated", message, 401)


class ForbiddenError(AssistantError):
    """Report a valid caller acting outside its own resources or application."""

    def __init__(self, code: str = "forbidden", message: str = "无权访问该资源。") -> None:
        super().__init__(code, message, 403)


class NotFoundError(AssistantError):
    """Report a resource that does not exist or is not visible to this caller."""

    def __init__(self, code: str = "not_found", message: str = "资源不存在。") -> None:
        super().__init__(code, message, 404)


class ConflictError(AssistantError):
    """Report a duplicate idempotency key, revoked credential or state conflict."""

    def __init__(self, code: str = "conflict", message: str = "请求与已有状态冲突。") -> None:
        super().__init__(code, message, 409)


class InvalidRequestError(AssistantError):
    """Report a semantically invalid request that passed schema validation."""

    def __init__(self, code: str = "invalid_request", message: str = "请求内容无效。") -> None:
        super().__init__(code, message, 422)


class UpstreamUnavailableError(AssistantError):
    """Report that NexusPilot could not be reached; the request may be retried."""

    def __init__(
        self,
        code: str = "nexus_unavailable",
        message: str = "AI 服务暂时不可用，请稍后重试。",
        retryable: bool = True,
    ) -> None:
        super().__init__(code, message, 503)
        self.retryable = retryable


class UpstreamTimeoutError(AssistantError):
    """Report an exhausted deadline whose side effect is not known to have failed."""

    def __init__(self, code: str = "nexus_timeout", message: str = "AI 服务响应超时。") -> None:
        super().__init__(code, message, 504)
