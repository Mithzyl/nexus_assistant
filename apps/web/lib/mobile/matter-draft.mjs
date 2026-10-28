/**
 * Pure formatting and state helpers for real matter drafts and change history.
 *
 * The backend keeps create, matter update, schedule-only update and todo-only
 * update in one draft shape. These helpers identify the intended operation
 * without inventing a title or querying the model.
 */

const DIRECT_EDIT_SOURCE = "直接编辑";

/** Identify which fields a server draft is intended to edit. */
export function classifyMatterDraft(draft) {
  if (!draft) return "none";
  if (draft.kind === "create") return "create";
  const payload = draft.payload || {};
  if (
    payload.source_text === DIRECT_EDIT_SOURCE &&
    payload.schedule &&
    payload.schedule.schedule_id
  ) {
    return "schedule";
  }
  if (
    payload.source_text === DIRECT_EDIT_SOURCE &&
    Array.isArray(payload.todos) &&
    payload.todos.length === 1 &&
    payload.todos[0] &&
    payload.todos[0].todo_id
  ) {
    return "todo";
  }
  return "matter";
}

/** A server-imported schedule is never editable through the web draft path. */
export function isEditableSchedule(schedule) {
  return Boolean(
    schedule &&
      (schedule.source === null ||
        schedule.source === undefined ||
        schedule.source === "web_native"),
  );
}

/** Selecting a different option or editing fields must clear the old choice. */
export function manualEditSelection() {
  return { selectedOption: null, acknowledge: false };
}

/** True when the server could not verify travel even if no hard conflict exists. */
export function conflictHasUnverifiedRoute(conflict) {
  return Boolean(
    conflict &&
      (String(conflict.eta_note || "").includes("travel_unverified") ||
        (conflict.conflicts || []).some(
          (item) => item.eta_status === "unverified",
        )),
  );
}

/** Short server-provided impact line used inside one option card. */
export function affectedScheduleSummary(item) {
  if (!item) return "受影响的相邻安排";
  const matter = item.matter_title ? `事项「${item.matter_title}」的` : "";
  const before = formatInstant(item.before_starts_at);
  const after = formatInstant(item.after_starts_at);
  return `${matter}日程「${item.title || "相邻安排"}」将从 ${before} 调整为 ${after}`;
}

/** Failures that mean a formal record changed after this preview was read. */
/** Initial checkbox state for a stop-following preview; read-only copies never start selected. */
export function selectedCancelScheduleIds(payload) {
  return (payload?.schedule_cancellations ?? [])
    .filter((item) => item && item.selected && item.cancellable)
    .map((item) => item.schedule_id);
}

export function isRecordChangedError(code) {
  return [
    "preview_stale",
    "matter_changed",
    "schedule_changed",
    "todo_changed",
    "reminder_changed",
    "option_not_selected",
    "schedule_started",
    "progress_added",
    "schedule_not_in_stop_preview",
    "not_initial_create",
    "initial_delete_unavailable",
  ].includes(String(code || ""));
}

/** Translate conflict reason codes without exposing server field names. */
export function conflictReasonText(reason, etaStatus) {
  if (etaStatus === "unverified") return "两段路线的路程耗时尚未核实，不能判定时间充裕。";
  if (reason === "overlap") return "两条安排时间重叠，需要调整其中一个安排。";
  if (reason === "eta_short") return "按服务器返回的行程耗时，与上一项之间的时间可能不够。";
  if (reason === "next_too_close") return "留给下一项安排的时间可能不足。";
  if (reason === "gap_short_route_unverified") return "间隔较短，路程尚未核实。";
  return "服务器提示这段安排可能衔接不上。";
}

/** Merge older local answer cache into server history without duplicate bubbles. */
export function mergeIntakeHistory(serverRows, localRows) {
  const server = Array.isArray(serverRows) ? serverRows : [];
  const local = Array.isArray(localRows) ? localRows : [];
  const runIds = new Set(server.map((item) => item.local_run_id).filter(Boolean));
  const fingerprints = new Set(
    server.map((item) => `${item.request_text}\u0000${item.answer || item.clarify || ""}`),
  );
  return [
    ...server,
    ...local.filter((item) => {
      if (item.localRunId && runIds.has(item.localRunId)) return false;
      return !fingerprints.has(`${item.question}\u0000${item.answer}`);
    }),
  ];
}

function formatInstant(value) {
  if (!value) return "未设置";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

/**
 * Explain the concrete effect of an applied server option.
 *
 * `before` and `after` are the returned draft payloads. This does not calculate
 * a conflict or propose another time; it only describes the server response.
 */
export function optionConsequences(option, before, after) {
  const lines = [];
  if (!option) return lines;
  for (const item of option.affected || []) {
    lines.push(`${affectedScheduleSummary(item)}。`);
  }
  if (option.option_id === "A" && after?.schedule) {
    lines.push(
      `本次日程「${after.schedule.title}」将改为 ${formatInstant(after.schedule.starts_at)}${
        after.schedule.ends_at ? ` 至 ${formatInstant(after.schedule.ends_at)}` : ""
      }。`,
    );
  }
  if (option.option_id === "B") {
    const adjustment = after?.previous_adjustment;
    if (adjustment && (option.affected || []).length === 0) {
      const minutes = Math.max(1, Math.round(Math.abs(adjustment.shift_seconds || 0) / 60));
      lines.push(
        `${adjustment.shift_seconds < 0 ? "提前" : "顺延"}相邻安排约 ${minutes} 分钟。`,
      );
    } else if ((option.affected || []).length === 0) {
      lines.push(option.detail);
    }
    const previousTitles = new Set((before?.todos || []).map((item) => item.title));
    const addedTodos = (after?.todos || []).filter(
      (item) => !item.todo_id && !previousTitles.has(item.title),
    );
    for (const todo of addedTodos) {
      lines.push(`新增待办「${todo.title}」。`);
    }
  }
  if (option.option_id === "C") {
    lines.push("本次安排不变，需要确认接受仍然存在的行程风险。");
  }
  if (lines.length === 0) lines.push(option.detail);
  return lines;
}

/**
 * Translate server revert reason codes into direct user-facing copy.
 * @param {string | null | undefined} code
 * @param {string | null} [fallback]
 * @returns {string}
 */
export function changeReasonText(code, fallback = null) {
  if (code === "later_changes") return "这条安排后来又被修改，不能直接撤回。";
  if (code === "already_reverted") return "这条变更已经撤回。";
  if (code === "matter_changed") return "事项在此之后又改过，不能覆盖撤回。";
  if (code === "schedule_changed") return "关联日程在此之后又改过，不能覆盖撤回。";
  if (code === "todo_changed") return "关联待办在此之后又改过，不能覆盖撤回。";
  if (code === "reminder_changed") return "关联提醒在此之后又改过，不能覆盖撤回。";
  if (code === "creation_revert_unsupported") {
    return "这条新建事项暂不提供快捷撤回；请使用事项详情中的删除操作。";
  }
  if (code === "incomplete_change_snapshot") {
    return "这条早期记录无法恢复修改前状态，请手动修改当前安排。";
  }
  if (code === "revert_from_source_matter") {
    return "这条变更来自其他事项，请到来源事项处理。";
  }
  if (code === "not_revertable") return "服务器暂时不允许撤回这条变更。";
  return fallback || "这条变更目前不能直接撤回。";
}

function scheduleSummary(schedule) {
  if (!schedule) return null;
  return `${schedule.title || "日程"} · ${formatInstant(schedule.starts_at)}${
    schedule.ends_at ? ` 至 ${formatInstant(schedule.ends_at)}` : ""
  }${schedule.location ? ` · ${schedule.location}` : ""}`;
}

function matterSummary(matter) {
  if (!matter) return null;
  return `${matter.title || "事项"}${matter.next_step ? ` · 下一步：${matter.next_step}` : ""}`;
}

/** Describe the recorded result using only fields present in the change. */
export function summarizeMatterChange(change) {
  if (!change) return "事项有变更";
  const before = change.before || {};
  const after = change.after || {};
  const lines = [];
  if (change.relation === "affected") {
    const beforeAdjacent = (before.adjacent_schedules || []).reduce(
      (result, item) => {
        if (item?.schedule_id) result[item.schedule_id] = item;
        return result;
      },
      {},
    );
    const affectedItems = [
      ...(after.affected || []),
      ...(after.adjacent_schedules || []),
    ];
    const affected =
      affectedItems.find(
        (item) =>
          item &&
          (item.matter_id === change.source_matter_id ||
            item.matter_id === change.matter_id),
      ) || affectedItems[0];
    const source = change.source_matter_title
      ? `来自事项「${change.source_matter_title}」：`
      : "来自其他事项：";
    if (affected) {
      const beforeAffected = beforeAdjacent[affected.schedule_id];
      const presented = affected.before_starts_at
        ? affected
        : {
            ...affected,
            before_starts_at: beforeAffected?.starts_at || null,
            before_ends_at: beforeAffected?.ends_at || null,
            after_starts_at:
              affected.starts_at || affected.after_starts_at || null,
            after_ends_at: affected.ends_at || affected.after_ends_at || null,
          };
      lines.push(
        `${source}${affectedScheduleSummary(presented)}。`,
      );
    } else {
      lines.push(`${source}关联日程已调整。`);
    }
    return lines.join("");
  }
  if (before.matter && after.matter) {
    if (before.matter.title !== after.matter.title) {
      lines.push(`标题从「${before.matter.title || "未填写"}」改为「${after.matter.title || "未填写"}」`);
    }
    if (before.matter.next_step !== after.matter.next_step) {
      lines.push(`下一步改为「${after.matter.next_step || "未填写"}」`);
    }
  }
  if (after.schedule) {
    lines.push(`日程：${scheduleSummary(after.schedule)}`);
  }
  for (const affected of after.affected || after.adjacent_schedules || []) {
    lines.push(affectedScheduleSummary(affected));
  }
  if (after.completed_todo_ids?.length) {
    lines.push(`完成 ${after.completed_todo_ids.length} 项待办`);
  }
  if (before.reminder_ids?.length || after.reminder_ids?.length) {
    lines.push("提醒随待办时间一并更新");
  }
  if (lines.length === 0) {
    if (change.change_type === "create") lines.push("创建事项和已确认安排");
    else if (after.matter) lines.push(matterSummary(after.matter));
    else lines.push("更新事项内容");
  }
  return lines.join("；");
}

/** Convert the backend's concise today summary into a user-facing label. */
export function todayChangeSummary(change) {
  if (!change) return "事项已更新";
  const raw = String(change.summary || "").trim();
  const type = String(change.change_type || "");
  const detail = raw.replace(new RegExp(`^${type.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\/\\s*`), "");
  if (type === "create") return detail ? `新建：${detail}` : "新建事项和安排";
  if (type === "revert") return detail ? `撤回：${detail}` : "撤回了这次变更";
  if (detail) return `修改：${detail}`;
  return "事项已更新";
}

/** Explain what a successful server revert would restore. */
export function describeRevert(change) {
  const before = change?.before || {};
  const lines = [];
  if (before.matter) lines.push(matterSummary(before.matter));
  if (before.schedule) lines.push(`恢复到 ${scheduleSummary(before.schedule)}`);
  else if (change?.after?.schedule) lines.push("移除这次新增的日程");
  for (const affected of before?.adjacent_schedules || before?.affected || []) {
    lines.push(`相邻安排恢复到 ${scheduleSummary(affected)}`);
  }
  if (change?.after?.completed_todo_ids?.length) {
    lines.push(`重新打开 ${change.after.completed_todo_ids.length} 项由这次变更完成的待办`);
  }
  if (change?.after?.reminder_ids?.length) lines.push("取消这次变更创建的待发提醒");
  return lines.length > 0 ? lines.join("；") : "服务器会恢复这条变更记录中的上一版内容。";
}

/**
 * Classify a deadline relative to the requested day in the server timezone.
 * @param {string} dueAt
 * @param {string} day
 * @param {string} [timeZone]
 */
export function deadlineTiming(dueAt, day, timeZone) {
  const due = new Date(dueAt);
  if (Number.isNaN(due.getTime())) return { state: "unknown", label: "截止时间未知" };
  const parts = new Intl.DateTimeFormat("en-CA", {
    ...(timeZone ? { timeZone } : {}),
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(due);
  const value = (type) => parts.find((part) => part.type === type)?.value || "";
  const dueDay = `${value("year")}-${value("month")}-${value("day")}`;
  if (dueDay < day) return { state: "overdue", label: "已逾期" };
  if (dueDay === day) return { state: "today", label: "今天到期" };
  return { state: "future", label: "稍后到期" };
}
