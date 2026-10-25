"""HTTP client for the current NexusPilot application boundary.

This module deliberately contains no database code, no provider adapter and no
Agent loop. It only knows how to authenticate as an application, assert the
already-verified local user, and move runs, workflow events and results.

The current server contract is:
  X-Application-Id: <application>
  X-External-User-Id: <local user id>
  POST /api/v1/app/runs {"user_request": ...}
  POST /api/v1/app/runs/{run_id}/agent-workflows {general_agent body}
  GET  /api/v1/app/agent-workflows/{id}/events
  GET  /api/v1/app/agent-workflows/{id}/result
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from assistant_api.nexus_client.errors import (
    NexusError,
    NexusProtocolError,
    NexusTimeoutError,
    NexusUnavailableError,
    error_for_status,
)
from assistant_api.nexus_client.models import (
    MemoryQueryRequest,
    MemoryQueryResponse,
    RunCreateRequest,
    RunView,
    SseEvent,
    WorkflowCreateRequest,
    WorkflowResultView,
    WorkflowView,
)
from assistant_api.nexus_client.sse import iter_sse

APPLICATION_PREFIX = "/api/v1/app"


class NexusPilotClient:
    """Talk to one NexusPilot deployment as one registered application.

    The external user id is a server-side assertion set by the caller after local
    session validation. A browser or model can never supply it.
    """

    def __init__(
        self,
        *,
        base_url: str,
        application_id: str,
        application_token: str,
        request_timeout_seconds: float = 30.0,
        stream_timeout_seconds: float = 300.0,
        transport: httpx.AsyncBaseTransport | None = None,
        trust_env: bool = False,
    ) -> None:
        self.application_id = application_id
        self._token = application_token
        # trust_env=False on purpose: a deployment that exports HTTP_PROXY must not
        # route application credentials and user requests through an unknown proxy.
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(request_timeout_seconds, read=stream_timeout_seconds),
            transport=transport,
            trust_env=trust_env,
            headers={"User-Agent": "assistant-api/0.1"},
        )

    async def aclose(self) -> None:
        """Close pooled connections; the app lifespan owns this call."""

        await self._client.aclose()

    def _headers(
        self,
        external_user_id: str | None,
        *,
        accept: str = "application/json",
        extra: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """Build the current application credential and trusted user assertion."""

        headers = {
            "Authorization": f"Bearer {self._token}",
            "X-Application-Id": self.application_id,
            "Accept": accept,
        }
        if external_user_id is not None:
            headers["X-External-User-Id"] = external_user_id
        if extra:
            headers.update(extra)
        return headers

    async def _post(self, path: str, payload: dict[str, Any], *, external_user_id: str) -> dict[str, Any]:
        """Send one JSON request and convert every failure into a typed error."""

        try:
            response = await self._client.post(
                path, json=payload, headers=self._headers(external_user_id)
            )
        except httpx.TimeoutException as exc:
            raise NexusTimeoutError("NexusPilot request timed out") from exc
        except httpx.HTTPError as exc:
            raise NexusUnavailableError("NexusPilot is unreachable") from exc
        return self._decode(response)

    async def _get(self, path: str, *, external_user_id: str) -> dict[str, Any]:
        try:
            response = await self._client.get(
                path, headers=self._headers(external_user_id)
            )
        except httpx.TimeoutException as exc:
            raise NexusTimeoutError("NexusPilot request timed out") from exc
        except httpx.HTTPError as exc:
            raise NexusUnavailableError("NexusPilot is unreachable") from exc
        return self._decode(response)

    def _decode(self, response: httpx.Response) -> dict[str, Any]:
        """Return a JSON object or raise the mapped error; never return a bare 200."""

        if response.status_code >= 400:
            raise error_for_status(response.status_code, _detail_from(response))
        try:
            body = response.json()
        except ValueError as exc:
            raise NexusProtocolError("NexusPilot returned a non-JSON body") from exc
        if not isinstance(body, dict):
            raise NexusProtocolError("NexusPilot returned a non-object body")
        return body

    async def create_run(self, external_user_id: str, payload: RunCreateRequest) -> RunView:
        """Create the durable Run. The current API accepts only user_request."""

        body = await self._post(
            f"{APPLICATION_PREFIX}/runs",
            payload.model_dump(mode="json", exclude_none=True),
            external_user_id=external_user_id,
        )
        return RunView.model_validate(body)

    async def start_workflow_events(
        self, external_user_id: str, run_id: str, payload: WorkflowCreateRequest
    ) -> AsyncIterator[SseEvent]:
        """Start the general Agent with stream=true and yield its committed SSE frames.

        The first frame contains the workflow id. The caller must keep iterating to
        terminal so the execution is observed and the result can be persisted.
        """

        headers = self._headers(
            external_user_id,
            accept="text/event-stream",
            extra={"Cache-Control": "no-cache"},
        )
        try:
            async with self._client.stream(
                "POST",
                f"{APPLICATION_PREFIX}/runs/{run_id}/agent-workflows",
                json=payload.model_dump(mode="json", exclude_none=True),
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise error_for_status(response.status_code, _detail_from(response))
                async for event in iter_sse(response.aiter_lines()):
                    yield event
        except httpx.TimeoutException as exc:
            raise NexusTimeoutError("NexusPilot workflow stream timed out") from exc
        except httpx.HTTPError as exc:
            raise NexusUnavailableError("NexusPilot workflow stream was interrupted") from exc

    async def get_workflow_summary(self, external_user_id: str, workflow_id: str) -> WorkflowView:
        body = await self._get(
            f"{APPLICATION_PREFIX}/agent-workflows/{workflow_id}",
            external_user_id=external_user_id,
        )
        return WorkflowView.model_validate(body)

    async def get_workflow_result(
        self, external_user_id: str, workflow_id: str
    ) -> WorkflowResultView:
        body = await self._get(
            f"{APPLICATION_PREFIX}/agent-workflows/{workflow_id}/result",
            external_user_id=external_user_id,
        )
        return WorkflowResultView.model_validate(body)

    async def cancel_workflow(self, external_user_id: str, workflow_id: str) -> WorkflowView:
        """Ask NexusPilot to stop future nodes; the remote state stays authoritative."""

        body = await self._post(
            f"{APPLICATION_PREFIX}/agent-workflows/{workflow_id}/cancel",
            {},
            external_user_id=external_user_id,
        )
        return WorkflowView.model_validate(body)

    async def query_memory(
        self, external_user_id: str, payload: MemoryQueryRequest
    ) -> MemoryQueryResponse:
        """Legacy memory proxy retained outside the current App Run scope."""

        body = await self._post(
            f"{APPLICATION_PREFIX}/memory-retrievals",
            payload.model_dump(mode="json", exclude_none=True),
            external_user_id=external_user_id,
        )
        return MemoryQueryResponse.model_validate(body)

    async def stream_events(
        self, external_user_id: str, workflow_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[SseEvent]:
        """Yield committed workflow events, resuming after the last seen sequence.

        The current endpoint is a finite replay of already-persisted events. A
        transport failure is raised rather than swallowed, so a partial answer is
        never presented as complete.
        """

        headers = self._headers(
            external_user_id,
            accept="text/event-stream",
            extra={"Cache-Control": "no-cache"},
        )
        if last_event_id:
            headers["Last-Event-ID"] = last_event_id
        try:
            async with self._client.stream(
                "GET",
                f"{APPLICATION_PREFIX}/agent-workflows/{workflow_id}/events",
                headers=headers,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise error_for_status(response.status_code, _detail_from(response))
                async for event in iter_sse(response.aiter_lines()):
                    yield event
        except httpx.TimeoutException as exc:
            raise NexusTimeoutError("NexusPilot event stream timed out") from exc
        except httpx.HTTPError as exc:
            raise NexusUnavailableError("NexusPilot event stream was interrupted") from exc

    @staticmethod
    def event_payload(event: SseEvent) -> dict[str, Any]:
        """Decode one event body; a non-JSON frame is a contract violation."""

        try:
            value = json.loads(event.data)
        except ValueError as exc:
            raise NexusProtocolError(f"Event {event.id or '?'} is not valid JSON") from exc
        if not isinstance(value, dict):
            raise NexusProtocolError(f"Event {event.id or '?'} is not a JSON object")
        return value


def _detail_from(response: httpx.Response) -> str:
    """Extract a bounded public message, ignoring an HTML error page body."""

    try:
        body = response.json()
    except ValueError:
        return f"NexusPilot returned HTTP {response.status_code}"
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
        if isinstance(detail, str) and detail:
            return detail[:500]
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"][:500]
    return f"NexusPilot returned HTTP {response.status_code}"


__all__ = ["NexusPilotClient", "NexusError"]
