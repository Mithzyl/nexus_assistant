"""Import every model so metadata is complete before create_all or migrations run."""

from assistant_api.models.base import Base
from assistant_api.models.bark import BarkReminderSetting
from assistant_api.models.bridge import NexusRun, ToolCallReceipt, UserAsk
from assistant_api.models.business import BusinessRecord, Note, ScheduleEvent, TaskItem
from assistant_api.models.content import (
    ContentItem,
    ContentSource,
    ContentSourceScope,
    ContentSyncBatch,
)
from assistant_api.models.commute import (
    AdvisoryAction,
    CommuteAdvisory,
    CommuteCheck,
    CommuteEtaSnapshot,
    CommuteIdempotencyKey,
    CommuteInstance,
    CommuteLease,
    CommutePlan,
    CommuteWeatherSnapshot,
    ServiceHeartbeat,
    NotificationOutbox,
    PushAttempt,
    PushDevice,
)
from assistant_api.models.identity import NexusIdentityLink, User, UserProfile, UserSession
from assistant_api.models.matter import (
    MATTER_STATUSES,
    SYNC_STATUSES,
    TODO_STATUSES,
    Matter,
    MatterChange,
    MatterDraft,
    MatterProgress,
    MatterTodo,
    Reminder,
)

__all__ = [
    "AdvisoryAction",
    "Base",
    "BarkReminderSetting",
    "BusinessRecord",
    "CommuteAdvisory",
    "ContentItem",
    "ContentSource",
    "ContentSourceScope",
    "ContentSyncBatch",
    "CommuteCheck",
    "CommuteEtaSnapshot",
    "CommuteIdempotencyKey",
    "CommuteInstance",
    "CommuteLease",
    "CommutePlan",
    "CommuteWeatherSnapshot",
    "ServiceHeartbeat",
    "MATTER_STATUSES",
    "Matter",
    "MatterChange",
    "MatterDraft",
    "MatterProgress",
    "MatterTodo",
    "NexusIdentityLink",
    "NexusRun",
    "Reminder",
    "SYNC_STATUSES",
    "TODO_STATUSES",
    "Note",
    "NotificationOutbox",
    "PushAttempt",
    "PushDevice",
    "ScheduleEvent",
    "TaskItem",
    "ToolCallReceipt",
    "User",
    "UserAsk",
    "UserProfile",
    "UserSession",
]
