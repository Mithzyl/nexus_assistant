"""Turn one natural-language update into a validated matter plan.

This reuses the shared AI task service (Nexus general agent). The model proposes
structure; the server decides what is acceptable: ownership of the matched matter,
absolute times (naive input is anchored in the user's timezone), ranges, and the
todo ids it is allowed to complete. Nothing is written to formal schedules or
reminders here; the caller stores a draft preview.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from assistant_api.commute.timeutil import ensure_aware
from assistant_api.services import ai_tasks

PURPOSE = "matter_plan"
INPUT_VERSION = 4
MAX_TODOS = 30

PROMPT = """你是个人事项整理助手。请把用户这句话整理成一个严格 JSON 对象，不要输出任何解释或 Markdown。
字段：
- intent: "matter"（在交代/推进一件事）、"question"（只是提问或闲聊，不应创建事项）或 "unclear"（信息不足，无法判断）。
- answer: intent=question 时，用一两句话直接回答用户的问题；其余情况省略。
- clarify: intent=unclear 时，说明还缺什么信息（目标、时间、地点或要办的事）；其余情况省略。
- clarification: intent=unclear 时可选 {{missing_information:[...], pending:{{title,goal,schedule,todos}}}}；只保存理解这句话所必需的结构化字段，不要复制整段聊天历史。
- kind: "create"（新事项）或 "update"（接续已有事项）。
- matter_id: update 时必须是下面"已有事项"里的 id；create 时为 null。
- title: 事项标题（create 必填，update 可省略表示不改）。
- goal: 一句话目标与背景（可省略）。
- changes_note: 这次用户补充的进展摘要（可省略）。
- next_step: 下一步动作（可省略）。
- schedule: 可为 null；否则 {{title, starts_at, ends_at, location}}。不要推断坐标或门牌号，坐标由地点服务另行确认。
- related_schedules: 仅用于保存用户这句话里提到、但没有库中编号的上下文安排；每项 {{title, starts_at, ends_at, location}}。这些内容只用于预览和测算，不得当作已保存预约。
- todos: 数组，每项 {{title, due_at, reminder_at}}；due_at 是截止时间，reminder_at 是提醒时间，二者含义不同。没有明确说明相对哪个日程或待办时，不要猜 reminder_at。
- complete_todo_ids: 本次明确办妥的待办 id（只能来自已选事项的待办）。
- matter_action: "none" 或 "archive"。只有用户明确说暂停/归档/停止跟进时才用 archive。
- cancel_reminders: 用户明确说暂停/不做了/停止跟进/不用再提醒/不用催时为 true；只说“归档”而没有停止跟进意图时不要推断。
- confidence: 0~1。
时间必须用 ISO 8601；能确定时区就带偏移（用户时区：{timezone}）。
不要编造不存在的路线耗时、门牌号或人物；信息不足时省略字段。
用户没有说明会议结束时间或时长时，ends_at 必须省略，不能默认成 30/60/90 分钟。
“下班前”只有用户明确给出具体时间或已提供可核对习惯时间时才能转成绝对时间；否则提出追问，不能自行填 18:00。
“提前两小时提醒我”没有说明提醒对象时提出追问，不能同时猜会议和合同截止。
已有日程只有在上下文中能核对到同一对象时才作为参考；标题相同也必须结合时间、地点和来源，不能仅凭标题认定。
列表未出现不能证明记录已删除；没有覆盖某个时间范围的完整数据时，不得回答“该时段没有任何安排”，应说明资料范围不足。
聊天历史里出现过的旧事项不能自动当成当前有效事实；只使用本次输入里标为有效的事项、待办和日程。
你只能提出待确认建议；不得输出“已完成”“已保存”“已取消”等已执行结论，也不得编造路线结果或“时间充裕”。
输入：
{payload}
"""


_DURATION_CUE = re.compile(
    r"(?:半|一|两|二|三|\d+)\s*(?:个)?\s*(?:小时|分钟)"
    r"|点半"
    r"|(?<![\d-])(?:上午|下午|晚上)?\s*\d{1,2}"
    r"(?::\d{2})?\s*(?:到|至|[-–—~])\s*\d{1,2}"
)
_RELATIVE_REMINDER = re.compile(r"提前\s*(?:两|二|2)\s*(?:个)?小时")


def _has_explicit_duration(text: str) -> bool:
    """Return whether the user's text explicitly states an end or duration."""

    return bool(_DURATION_CUE.search(text))


def _uses_unstated_workday_end(
    due_at: datetime | None, text: str, timezone: str
) -> bool:
    """Detect the model filling 18:00 for a bare "before work ends" request."""

    if due_at is None or "下班前" not in text:
        return False
    local = due_at.astimezone(ZoneInfo(timezone))
    explicit_time = re.search(
        r"(?:18(?::00)?|十八点|六点|6点|下午六点)",
        text,
    )
    return local.hour == 18 and local.minute == 0 and explicit_time is None


def _relative_reminder_is_ambiguous(
    text: str, schedule: dict | None, todos: list[dict]
) -> bool:
    """Detect an unqualified two-hour reminder with multiple time targets."""

    if not _RELATIVE_REMINDER.search(text):
        return False
    candidate_count = (1 if schedule else 0) + sum(
        1 for todo in todos if todo.get("due_at")
    )
    if candidate_count < 2:
        return False
    explicit_target = re.search(
        r"(?:合同|会议|日程|待办|预约)(?:截止)?(?:前)?\s*"
        r"提前\s*(?:两|二|2)\s*(?:个)?小时",
        text,
    )
    return explicit_target is None


def _restrict_unverified_absence_answer(
    answer: str | None, context: dict[str, Any]
) -> str | None:
    """Replace a broad no-arrangements claim when schedule coverage is absent."""

    if answer is None or context.get("schedule_coverage_complete") is True:
        return answer
    if re.search(r"(?:没有|没有任何|暂无|无).{0,8}(?:安排|事项|日程)", answer):
        return "当前提供的资料没有覆盖完整的时间范围，不能确认该时段完全没有安排。"
    return answer


def _reject(reason: str) -> None:
    raise ai_tasks.AiTaskResultRejected(reason)


def prepare_input(context: dict[str, Any]) -> str:
    payload = {
        "text": context["text"],
        "now": context["now"],
        "timezone": context["timezone"],
        "pinned_matter_id": context.get("pinned_matter_id"),
        "continuation_context": context.get("continuation_context"),
        "matters": context.get("matters", []),
        "todos": context.get("todos", {}),
        "schedule_coverage_complete": context.get("schedule_coverage_complete", False),
        "deleted_or_revoked_notice": (
            "本次上下文未提供已删除或已撤权记录；它们不能被历史聊天重新引用。"
        ),
    }
    return PROMPT.format(
        timezone=context["timezone"],
        payload=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )


def _parse_time(value: Any, timezone: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        _reject("time_missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _reject("time_invalid")
    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
        except ZoneInfoNotFoundError:
            parsed = parsed.replace(tzinfo=UTC)
    parsed = ensure_aware(parsed).astimezone(UTC)
    now = datetime.now(UTC)
    if not (now - timedelta(days=400) <= parsed <= now + timedelta(days=800)):
        _reject("time_out_of_range")
    return parsed


def _clean_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        _reject("text_invalid")
    stripped = value.strip()
    if not stripped:
        return None
    if len(stripped) > limit:
        _reject("text_too_long")
    return stripped


def _parse_schedule(
    value: Any,
    fallback_title: str | None,
    timezone: str,
    source_text: str,
) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        _reject("schedule_invalid")
    starts_at = _parse_time(value.get("starts_at"), timezone)
    ends_raw = value.get("ends_at")
    if ends_raw and not _has_explicit_duration(source_text):
        ends_raw = None
    ends_at = _parse_time(ends_raw, timezone) if ends_raw else None
    if ends_at is not None and ends_at <= starts_at:
        _reject("schedule_time_order")
    title = _clean_text(value.get("title"), 255) or fallback_title
    if not title:
        _reject("schedule_title_missing")
    return {
        "schedule_id": None,
        "title": title,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "location": _clean_text(value.get("location"), 255),
        "latitude": None,
        "longitude": None,
        "coordinate_system": "unknown",
        "location_city": None,
        "location_address": None,
        "location_source": None,
        "location_external_id": None,
    }


def _parse_related_schedules(value: Any, timezone: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        _reject("related_schedules_invalid")
    if len(value) > 8:
        _reject("related_schedules_too_many")
    related = []
    for item in value:
        if not isinstance(item, dict):
            _reject("related_schedule_invalid")
        title = _clean_text(item.get("title"), 255)
        if not title:
            _reject("related_schedule_title_missing")
        starts_at = _parse_time(item.get("starts_at"), timezone)
        ends_at = (
            _parse_time(item.get("ends_at"), timezone)
            if item.get("ends_at")
            else None
        )
        if ends_at is not None and ends_at <= starts_at:
            _reject("related_schedule_time_order")
        related.append(
            {
                "kind": "unverified",
                "schedule_id": None,
                "title": title,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "location": _clean_text(item.get("location"), 255),
                "latitude": None,
                "longitude": None,
                "coordinate_system": "unknown",
                "location_city": None,
                "location_address": None,
                "location_source": None,
                "location_external_id": None,
            }
        )
    return related


def validate_result(text: str | None, context: dict[str, Any]) -> dict:
    if not text or not text.strip():
        _reject("empty")
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.S)
        if not match:
            _reject("invalid_json")
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            _reject("invalid_json")
    if not isinstance(value, dict):
        _reject("not_object")

    intent = value.get("intent") or "matter"
    if intent not in ("matter", "question", "unclear"):
        _reject("intent_invalid")
    if intent != "matter":
        clarification = value.get("clarification")
        clarification = clarification if isinstance(clarification, dict) else {}
        pending = clarification.get("pending")
        pending = pending if isinstance(pending, dict) else {}
        pending_schedule = pending.get("schedule")
        if not isinstance(pending_schedule, dict):
            pending_schedule = None
        pending_todos = []
        for item in pending.get("todos") or []:
            if not isinstance(item, dict):
                continue
            title_value = _clean_text(item.get("title"), 255)
            if title_value:
                pending_todos.append(
                    {
                        "title": title_value,
                        "due_at": item.get("due_at")
                        if isinstance(item.get("due_at"), str)
                        else None,
                        "reminder_at": item.get("reminder_at")
                        if isinstance(item.get("reminder_at"), str)
                        else None,
                    }
                )
        missing = [
            text
            for item in clarification.get("missing_information") or []
            if (text := _clean_text(item, 120))
        ]
        return {
            "intent": intent,
            "answer": _restrict_unverified_absence_answer(
                _clean_text(value.get("answer"), 2000), context
            ),
            "clarify": _clean_text(value.get("clarify"), 500),
            "pending_context": {
                "title": _clean_text(pending.get("title"), 255),
                "goal": _clean_text(pending.get("goal"), 4000),
                "schedule": pending_schedule,
                "todos": pending_todos[:MAX_TODOS],
                "missing_information": missing[:10],
            },
            "kind": "create",
            "matter_id": None,
            "candidate_matter_ids": [],
            "title": None,
            "goal": None,
            "changes_note": None,
            "next_step": None,
            "schedule": None,
            "related_schedules": [],
            "todos": [],
            "complete_todo_ids": [],
            "dropped_complete_todo_ids": [],
            "confidence": None,
            "matter_action": "none",
            "cancel_reminders": False,
        }
    kind = value.get("kind") or "create"
    if kind not in ("create", "update"):
        _reject("kind_invalid")
    timezone = context["timezone"]
    valid_matter_ids = set(context.get("matter_ids", []))
    candidate_ids = [
        candidate
        for candidate in (value.get("candidate_matter_ids") or [])
        if isinstance(candidate, str) and candidate in valid_matter_ids
    ]
    matter_id = value.get("matter_id")
    if matter_id is not None and matter_id not in valid_matter_ids:
        _reject("unknown_matter")
    if kind == "update" and matter_id is None and not candidate_ids:
        _reject("missing_matter")

    title = _clean_text(value.get("title"), 255)
    if kind == "create" and not title:
        _reject("title_missing")
    if kind == "update" and title:
        _reject("update_title_not_allowed_here")

    source_text = context["text"]
    schedule = _parse_schedule(value.get("schedule"), title, timezone, source_text)
    related_schedules = _parse_related_schedules(
        value.get("related_schedules"), timezone
    )
    matter_action = value.get("matter_action") or "none"
    if matter_action not in ("none", "archive"):
        _reject("matter_action_invalid")
    cancel_reminders = value.get("cancel_reminders", False)
    if not isinstance(cancel_reminders, bool):
        _reject("cancel_reminders_invalid")

    todos: list[dict] = []
    raw_todos = value.get("todos") or []
    if not isinstance(raw_todos, list):
        _reject("todos_invalid")
    if len(raw_todos) > MAX_TODOS:
        _reject("todos_too_many")
    allowed_todo_ids = set(context.get("todo_ids", []))
    workday_time_removed = False
    for item in raw_todos:
        if not isinstance(item, dict):
            _reject("todo_invalid")
        todo_title = _clean_text(item.get("title"), 255)
        if not todo_title:
            _reject("todo_title_missing")
        todo_id = item.get("todo_id")
        if todo_id is not None and todo_id not in allowed_todo_ids:
            _reject("unknown_todo")
        due = item.get("due_at")
        reminder = item.get("reminder_at")
        parsed_due = _parse_time(due, timezone) if due else None
        if _uses_unstated_workday_end(parsed_due, source_text, timezone):
            parsed_due = None
            workday_time_removed = True
        todos.append(
            {
                "todo_id": todo_id,
                "title": todo_title,
                "due_at": parsed_due,
                "reminder_at": _parse_time(reminder, timezone) if reminder else None,
                "status": "open",
            }
        )

    raw_complete = value.get("complete_todo_ids") or []
    if not isinstance(raw_complete, list):
        _reject("complete_todos_invalid")
    complete_ids = [
        todo_id
        for todo_id in raw_complete
        if isinstance(todo_id, str) and todo_id in allowed_todo_ids
    ]

    confidence = value.get("confidence")
    if confidence is not None and (
        not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1
    ):
        _reject("confidence_invalid")

    result = {
        "intent": intent,
        "answer": None,
        "clarify": None,
        "kind": kind,
        "matter_id": matter_id,
        "candidate_matter_ids": candidate_ids,
        "title": title,
        "goal": _clean_text(value.get("goal"), 4000),
        "changes_note": _clean_text(value.get("changes_note"), 2000)
        or context["text"][:2000],
        "next_step": _clean_text(value.get("next_step"), 500),
        "schedule": schedule,
        "related_schedules": related_schedules,
        "todos": todos,
        "complete_todo_ids": complete_ids,
        "dropped_complete_todo_ids": [
            todo_id for todo_id in raw_complete if todo_id not in allowed_todo_ids
        ],
        "confidence": float(confidence) if confidence is not None else None,
        "matter_action": matter_action,
        "cancel_reminders": cancel_reminders,
        "requires_clarification": False,
        "clarify": None,
        "pending_context": {},
    }
    relative_reminder_ambiguous = _relative_reminder_is_ambiguous(
        source_text, schedule, todos
    )
    if relative_reminder_ambiguous or workday_time_removed:
        clarify = (
            "“提前两小时”要提醒的是会议还是合同截止？请明确提醒对象。"
            if relative_reminder_ambiguous
            else "“下班前”没有可核对的固定下班时间，请明确具体日期和时间。"
        )
        result.update(
            {
                "requires_clarification": True,
                "clarify": clarify,
                "pending_context": {
                    "missing_information": ["提醒对象"],
                    "pending": {
                        "title": title,
                        "goal": result["goal"],
                        "schedule": schedule,
                        "todos": todos,
                    },
                },
            }
        )
    return result


SPEC = ai_tasks.register(
    ai_tasks.AiTaskSpec(
        purpose=PURPOSE,
        input_version=INPUT_VERSION,
        strict_input_match=True,
        prepare_input=prepare_input,
        validate_result=validate_result,
    )
)


__all__ = ["PURPOSE", "SPEC", "prepare_input", "validate_result"]
