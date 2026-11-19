"""Emit versioned operational events from allowlisted metadata only."""
from datetime import UTC, datetime
import json
import os
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request

SAFE_CODES = frozenset({
    'invalid_request', 'internal_error', 'not_found', 'method_not_allowed', 'http_error',
    'unauthenticated', 'registration_disabled', 'invalid_invite_code', 'recommendation_changed',
    'conflict_detected', 'idempotency_key_reused', 'nexus_timeout', 'nexus_unavailable',
    'nexus_protocol_error', 'nexus_application_unauthenticated', 'nexus_permission_denied',
    'nexus_resource_not_found', 'nexus_conflict', 'nexus_invalid_request', 'rate_limited',
    'recommendation_not_found', 'recommendation_target_not_found',
})
HTTP_METHODS = frozenset({'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'})


def emit_event(event: str, *, level='info', now=None, **fields) -> None:
    """Write one stable JSON line to the existing service log stream; never attach exceptions."""
    record = {'schema_version':1, 'timestamp':(now or datetime.now(UTC)).isoformat(),
              'service':'assistant', 'event':event, 'level':level, **fields}
    line = json.dumps(record, ensure_ascii=False, separators=(',',':'))
    try:
        print(line, flush=True)
    except OSError:
        # A closed log stream must not turn an already saved request into a client failure.
        pass
    directory = os.environ.get('ASSISTANT_OPERATIONS_SPOOL')
    if directory:
        # A single append syscall preserves record boundaries across supervisor children.
        try:
            path = Path(directory) / (datetime.now(UTC).strftime('%Y-%m-%d')+'.jsonl')
            fd = os.open(path,os.O_WRONLY|os.O_APPEND|os.O_CREAT|os.O_NOFOLLOW,0o600)
            try:
                os.write(fd,(line+'\n').encode())
            finally:
                os.close(fd)
        except OSError:
            return


def safe_error_code(code):
    """Keep known application codes; never copy arbitrary upstream strings into logs."""
    return code if code in SAFE_CODES else 'other_application_error'


class RequestObservationMiddleware:
    """Log completed HTTP exchanges using route definitions, never URL or body values."""

    def __init__(self, app, *, on_unhandled):
        """Receive the shared public error response builder without importing application routes."""
        self.app = app
        self.on_unhandled = on_unhandled

    async def __call__(self, scope, receive, send):
        """Attach a server-generated request ID and preserve failure signals for interrupted streams."""
        if scope['type'] != 'http':
            await self.app(scope,receive,send)
            return
        state = scope.setdefault('state',{})
        request_id = str(uuid4())
        state['request_id'] = request_id
        started = perf_counter()
        response_status = None
        finished = False
        fault = None

        async def observe_send(message):
            """Observe headers and last response bytes without reading payload values."""
            nonlocal response_status, finished
            if message['type']=='http.response.start':
                response_status = message['status']
                MutableHeaders(scope=message)['X-Request-ID'] = request_id
            await send(message)
            if message['type']=='http.response.body' and not message.get('more_body',False):
                finished = True

        try:
            await self.app(scope,receive,observe_send)
        except Exception:
            fault = 'internal_error' if response_status is None else 'response_interrupted'
            if response_status is None:
                await self.on_unhandled(Request(scope))(scope,receive,observe_send)
            elif not finished:
                # Keep the transport failure visible without leaking the original exception into server logs.
                raise RuntimeError('Assistant response interrupted') from None
        finally:
            route = getattr(scope.get('route'),'path',None) or 'unmatched'
            method = scope.get('method')
            failed = not finished or fault is not None or (response_status or 0) >= 400
            level = 'error' if not finished or fault or (response_status or 0)>=500 else 'warning' if failed else 'info'
            emit_event('http_request',level=level, category='interface_failure' if failed else 'interface_ok',
                method=method if method in HTTP_METHODS else 'OTHER',route=route,status_code=response_status,
                request_id=request_id,duration_ms=round((perf_counter()-started)*1000,2),
                response_complete=finished,error_code=fault or state.get('observation_error_code')
                or ('http_error' if failed and finished else 'response_interrupted' if not finished else None))
