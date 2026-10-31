import type {
  DraftAction,
  MatterDetail,
  MatterDraft,
  MatterSchedule,
  MatterTodo,
} from "@/lib/mobile/api";
import { dateTimeLabel } from "@/lib/mobile/work-plan";

/** Format one server-provided schedule without calculating new times. */
function scheduleRange(schedule: Pick<MatterSchedule, "starts_at" | "ends_at" | "location">): string {
  const start = dateTimeLabel(schedule.starts_at);
  const end = schedule.ends_at
    ? new Date(schedule.ends_at).toLocaleTimeString("zh-CN", {
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : null;
  return `${start}${end ? `–${end}` : ""}${schedule.location ? ` · ${schedule.location}` : ""}`;
}

/** Prefer the current server todo title, then the action's returned title. */
function todoLabel(todo: MatterTodo | undefined, fallback: string | null | undefined): string {
  return todo?.title || fallback || "关联待办";
}

/** Build display-only fallbacks for older server drafts that lack action_plan. */
function displayActions(draft: MatterDraft): DraftAction[] {
  if (draft.action_plan?.length) return draft.action_plan;
  if (draft.payload.action_plan?.length) return draft.payload.action_plan;
  const actions: DraftAction[] = [];
  const payload = draft.payload;
  if (payload.proposed_status === "archived") {
    actions.push({
      kind: "update_matter_status",
      status: "archived",
      reason: payload.changes_note,
    });
  }
  if (payload.schedule) {
    actions.push({
      kind: payload.schedule.schedule_id ? "update_schedule" : "create_schedule",
      schedule_id: payload.schedule.schedule_id,
      title: payload.schedule.title,
      starts_at: payload.schedule.starts_at,
      location: payload.schedule.location,
    });
  }
  for (const todo of payload.todos ?? []) {
    actions.push({
      kind: todo.todo_id ? "update_todo" : "create_todo",
      todo_id: todo.todo_id,
      title: todo.title,
      due_at: todo.due_at,
      reminder_at: todo.reminder_at,
    });
  }
  for (const todoId of payload.complete_todo_ids ?? []) {
    actions.push({ kind: "complete_todo", todo_id: todoId });
  }
  for (const reminder of payload.reminder_cancellations ?? []) {
    actions.push({
      kind: "cancel_reminder",
      reminder_id: reminder.reminder_id,
      title: reminder.title,
      trigger_at: reminder.trigger_at,
    });
  }
  return actions;
}

/** Translate one server action into a concise, non-speculative change summary. */
function actionDescription(
  action: DraftAction,
  draft: MatterDraft,
  matter: MatterDetail | null,
): string | null {
  const payload = draft.payload;
  const proposed = payload.schedule;
  if (action.kind === "create_matter") {
    return `新建事项「${action.title || payload.title || "未命名事项"}」`;
  }
  if (action.kind === "update_matter_status") {
    return action.status === "archived"
      ? "将事项归档，保留记录但停止跟进"
      : "更新事项状态";
  }
  if (action.kind === "create_schedule") {
    const start = action.starts_at ? dateTimeLabel(action.starts_at) : "时间未提供";
    return `新增日程「${action.title || "未命名日程"}」 · ${start}${
      action.location ? ` · ${action.location}` : ""
    }`;
  }
  if (action.kind === "update_schedule") {
    const before = matter?.schedules.find(
      (schedule) => schedule.schedule_id === action.schedule_id,
    );
    const after = proposed?.schedule_id === action.schedule_id ? proposed : null;
    const afterStart = after?.starts_at || action.starts_at;
    const afterEnd = after?.ends_at;
    const afterLocation = after?.location ?? action.location;
    const title = action.title || before?.title || "日程";
    if (before) {
      const afterText = afterStart
        ? scheduleRange({
            starts_at: afterStart,
            ends_at: afterEnd ?? null,
            location: afterLocation ?? null,
          })
        : "云端未返回新时间";
      return `调整日程「${title}」：${scheduleRange(before)} → ${afterText}`;
    }
    return `调整日程「${title}」${
      afterStart ? `至 ${dateTimeLabel(afterStart)}` : ""
    }${afterLocation ? ` · ${afterLocation}` : ""}`;
  }
  if (action.kind === "create_todo") {
    return `新增待办「${action.title || "未命名待办"}」${
      action.due_at ? ` · 截止 ${dateTimeLabel(action.due_at)}` : ""
    }${action.reminder_at ? ` · 提醒 ${dateTimeLabel(action.reminder_at)}` : ""}`;
  }
  if (action.kind === "update_todo") {
    const before = matter?.todos.find((todo) => todo.todo_id === action.todo_id);
    return `调整待办「${todoLabel(before, action.title)}」${
      action.due_at ? ` · 截止 ${dateTimeLabel(action.due_at)}` : ""
    }${action.reminder_at ? ` · 提醒 ${dateTimeLabel(action.reminder_at)}` : ""}`;
  }
  if (action.kind === "complete_todo") {
    const before = matter?.todos.find((todo) => todo.todo_id === action.todo_id);
    return `将待办「${todoLabel(before, action.title)}」标为完成`;
  }
  if (action.kind === "cancel_reminder") {
    return `取消提醒「${action.title || "未命名提醒"}」${
      action.trigger_at ? ` · 原定 ${dateTimeLabel(action.trigger_at)}` : ""
    }`;
  }
  return null;
}

/**
 * Present server-backed draft scope before editable fields.
 *
 * Existing schedule references are never described as newly created records,
 * and unsupported cancellation or acknowledgement actions are not invented.
 */
export function DraftImpactSummary({
  draft,
  matter,
}: {
  draft: MatterDraft;
  matter: MatterDetail | null;
}) {
  const payload = draft.payload;
  const relatedSchedules = payload.related_schedules ?? [];
  const existing = relatedSchedules.filter((item) => item.kind === "existing");
  const unverified = relatedSchedules.filter((item) => item.kind !== "existing");
  const actions = displayActions(draft);
  const actionLines = actions
    .filter((action) => action.kind !== "context_only")
    .map((action) => actionDescription(action, draft, matter))
    .filter((line): line is string => Boolean(line));
  const archiveDraft =
    payload.matter_action === "archive" || payload.proposed_status === "archived";
  const reminderCancellations = payload.reminder_cancellations ?? [];
  const scheduleCancellations = payload.schedule_cancellations ?? [];
  const selectedSchedules = scheduleCancellations.filter((item) => item.selected);
  const keptSchedules = scheduleCancellations.filter((item) => !item.selected);

  if (
    existing.length === 0 &&
    unverified.length === 0 &&
    actionLines.length === 0 &&
    !archiveDraft
  ) {
    return null;
  }

  return (
    <div className="draft-impact">
      {existing.length > 0 ? (
        <section className="draft-impact__group" aria-label="已有安排，仅作参考">
          <strong>已有安排，仅作参考</strong>
          <ul className="compact-list">
            {existing.map((item, index) => (
              <li key={item.schedule_id || `existing-${index}`}>
                {item.title} · {item.starts_at ? dateTimeLabel(item.starts_at) : "时间未提供"}
                {item.location ? ` · ${item.location}` : ""}
              </li>
            ))}
          </ul>
          <p className="micro">这些安排已经保存在云端中，不会因为本次确认重复新增。</p>
        </section>
      ) : null}

      {unverified.length > 0 ? (
        <section className="draft-impact__group" aria-label="仅作预览的背景安排">
          <strong>话中提及但未找到的已有安排</strong>
          <ul className="compact-list">
            {unverified.map((item, index) => (
              <li key={`unverified-${index}`}>
                {item.title} · {item.starts_at ? dateTimeLabel(item.starts_at) : "时间未提供"}
                {item.location ? ` · ${item.location}` : ""}
              </li>
            ))}
          </ul>
          <p className="micro">云端只把这里用于核对冲突，不会把这些内容当成已保存预约，也不会创建它们。</p>
        </section>
      ) : null}

      {actionLines.length > 0 ? (
        <section className="draft-impact__group" aria-label="准备新增或修改的内容">
          <strong>准备新增或修改的内容</strong>
          <ul className="compact-list">
            {actionLines.map((line, index) => <li key={`${line}-${index}`}>{line}</li>)}
          </ul>
        </section>
      ) : null}

      {archiveDraft ? (
        <section className="draft-impact__group draft-impact__group--amber" aria-label="停止跟进范围">
          <strong>停止跟进范围</strong>
          <ul className="compact-list">
            <li>事项将归档保留，不会删除事项记录。</li>
            {reminderCancellations.map((reminder) => (
              <li key={reminder.reminder_id}>
                取消未触发提醒「{reminder.title}」 · {dateTimeLabel(reminder.trigger_at)}
              </li>
            ))}
          </ul>
          {scheduleCancellations.length > 0 ? (
            <div className="mt-8">
              <p className="micro" style={{ margin: 0 }}>
                未来日程 {scheduleCancellations.length} 项：确认前可在下方逐项选择是否一并取消；
                手机导入的只读日程不能由网页取消。
              </p>
              {selectedSchedules.length > 0 ? (
                <ul className="compact-list">
                  {selectedSchedules.map((schedule) => (
                    <li key={schedule.schedule_id}>
                      将取消「{schedule.title}」 · {scheduleRange(schedule)}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  目前没有勾选要取消的未来日程。
                </p>
              )}
              {keptSchedules.length > 0 ? (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  保留 {keptSchedules.length} 项未来日程。
                </p>
              ) : null}
            </div>
          ) : archiveDraft ? (
            <p className="micro" style={{ margin: "4px 0 0" }}>
              暂时没有可勾选的未来日程；归档只处理事项状态和未触发提醒。
            </p>
          ) : null}
          <p className="micro">
            注：若此前已有通知发送到手机锁屏，需在手机上手动清除；你单独保存在「个人记忆」中的习惯仍为你留存，如需移除可前往「个人记忆」管理。
          </p>
        </section>
      ) : null}
    </div>
  );
}
