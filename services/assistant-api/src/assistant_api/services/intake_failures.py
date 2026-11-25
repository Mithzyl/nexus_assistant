"""Public intake failures aligned with Nexus WorkflowErrorRead, without raw exceptions."""
from assistant_api.core.errors import AssistantError
from assistant_api.nexus_client.errors import NexusError
from assistant_api.nexus_client.models import WorkflowErrorRead


def public_failure(row=None, exc=None) -> dict:
    """Build a public failure from a durable run or a safe request error."""
    status = row.status if row is not None else "unknown"
    if status not in {"failed", "cancelled", "unknown", "pending", "running"}:
        status = "unknown"
    outcome = "processing" if status in {"pending", "running"} else status
    stored = (row.result_json or {}).get("error") if row is not None else None
    if isinstance(stored, dict):
        error = WorkflowErrorRead.model_validate(stored).model_dump()
    else:
        known = status in {"failed", "cancelled"}
        code = row.error_code if row is not None else None
        if isinstance(exc, AssistantError):
            code = code or exc.code
            message = exc.message
            if row is None:
                known = exc.status_code < 500
                status = "failed" if known else "unknown"
                outcome = status
        else:
            code = code or (exc.code if isinstance(exc, NexusError) else "assistant_internal_error")
            message = "这次请求已取消。" if status == "cancelled" else "这次请求未能完成，请稍后重试。" if known else "暂未取得最终结果，请核对本次请求；不会自动重新提交。"
        error = WorkflowErrorRead(
            error_code=code or ("assistant_request_cancelled" if status == "cancelled" else "assistant_request_failed"),
            error_type="request_error" if known else "outcome_unknown",
            public_message=message,
            is_retryable=bool(getattr(exc, "retryable", False)) if known else False,
            outcome_is_known=known,
        ).model_dump()
    return {"error": error, "status": status, "outcome": outcome,
            "local_run_id": row.local_run_id if row is not None else None,
            "workflow_execution_id": row.workflow_execution_id if row is not None else None}


def stream_failure(result: dict, row) -> dict:
    """Keep the actual run identity and emit the workflow-error fields expected by Web."""
    failure = public_failure(row)
    outcome = result["outcome"]
    error = result.get("error")
    if isinstance(error, dict) and error.get("code") == "final_output_invalid":
        # A finished model run can have an invalid business answer even after saving a record.
        # The answer failure is known, while repeating the business task is unsafe.
        failure["error"] = WorkflowErrorRead(
            error_code=error["code"],
            error_type="response_validation",
            public_message=error["message"],
            outcome_is_known=True,
        ).model_dump()
    return {**failure, "outcome": outcome, "status": result.get("status"),
            "local_run_id": result.get("local_run_id"),
            "workflow_execution_id": result.get("workflow_execution_id")}
