"use client";

/**
 * Editable server draft for creating or updating a matter, schedule or todo.
 * Every write stays in preview until the user explicitly confirms it.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, RefreshCw } from "lucide-react";

import type {
  DraftConfirmResponse,
  DraftPatchInput,
  DraftTodo,
  MatterAffectedSchedule,
  MatterConflict,
  MatterDraft,
  MatterTodoStatus,
  PlaceCandidate,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  applyDraftOption,
  cancelDraft as cancelDraftRequest,
  confirmDraft,
  confirmPlaceSelection,
  getDraft,
  newOperationKey,
  patchDraft,
  searchPlaces,
  selectDraftMatter,
} from "@/lib/mobile/matters-api";
import {
  affectedScheduleSummary,
  classifyMatterDraft,
  conflictReasonText,
  conflictHasUnverifiedRoute,
  isRecordChangedError,
  manualEditSelection,
  optionConsequences,
} from "@/lib/mobile/matter-draft.mjs";
import { dateTimeLabel } from "@/lib/mobile/work-plan";
import { RouteTextCard } from "./route-text-card";
import { Sheet } from "./ui";

interface EditableTodo {
  todoId: string | null;
  title: string;
  dueAt: string;
  reminderAt: string;
  originalReminderAt: string | null;
  status: MatterTodoStatus;
}

function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function fromLocalInput(value: string): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function editableTodos(todos: DraftTodo[] | undefined): EditableTodo[] {
  return (todos ?? []).map((todo) => ({
    todoId: todo.todo_id ?? null,
    title: todo.title,
    dueAt: toLocalInput(todo.due_at),
    reminderAt: toLocalInput(todo.reminder_at),
    originalReminderAt: toLocalInput(todo.reminder_at),
    status: todo.status ?? "open",
  }));
}

function todosEqual(left: EditableTodo[], right: EditableTodo[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((todo, index) => {
    const other = right[index];
    return (
      other &&
      todo.todoId === other.todoId &&
      todo.title.trim() === other.title.trim() &&
      todo.dueAt === other.dueAt &&
      todo.reminderAt === other.reminderAt
    );
  });
}

function conflictText(conflict: MatterConflict | null): string | null {
  if (!conflict?.has_conflict) return null;
  const item = conflict.conflicts[0];
  if (!item) return "服务器报告存在时间冲突，请先核对风险。";
  if (item.eta_status === "unverified") {
    return `${item.title}：路程耗时尚未核实，不能判定时间充裕。${item.reason}`;
  }
  if (item.relation === "overlap") return `${item.title}：与这次安排时间重叠。`;
  return `${item.title}：${item.reason}`;
}

function sheetTitle(mode: string): string {
  if (mode === "create") return "整理成事项";
  if (mode === "schedule") return "调整日程";
  if (mode === "todo") return "调整待办";
  return "确认事项变更";
}

function primarySummary(mode: string): string {
  if (mode === "create") return "新建事项";
  if (mode === "schedule") return "只调整日程";
  if (mode === "todo") return "只调整待办";
  return "修改事项";
}

export function MatterDraftSheet({
  draft,
  open,
  onClose,
  onConfirmed,
  onDraftCancelled,
}: {
  draft: MatterDraft | null;
  open: boolean;
  onClose: () => void;
  onConfirmed: (response: DraftConfirmResponse) => void;
  onDraftCancelled?: (draft: MatterDraft) => void;
}) {
  const [current, setCurrent] = useState<MatterDraft | null>(draft);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [nextStep, setNextStep] = useState("");
  const [scheduleTitle, setScheduleTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [location, setLocation] = useState("");
  const [createSchedule, setCreateSchedule] = useState(true);
  const [todos, setTodos] = useState<EditableTodo[]>([]);
  const [syncToIos, setSyncToIos] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [selectedMatter, setSelectedMatter] = useState("");
  const [selectedOption, setSelectedOption] = useState<"A" | "B" | "C" | null>(null);
  const [selectedImpact, setSelectedImpact] = useState<string[]>([]);
  const [selectedAffected, setSelectedAffected] = useState<MatterAffectedSchedule[]>([]);
  const [placeQuery, setPlaceQuery] = useState("");
  const [placeCity, setPlaceCity] = useState("");
  const [placeSearchType, setPlaceSearchType] = useState<"place" | "address">("place");
  const [placeCandidates, setPlaceCandidates] = useState<PlaceCandidate[]>([]);
  const [selectedPlaceToken, setSelectedPlaceToken] = useState<string | null>(null);
  const [placeSearching, setPlaceSearching] = useState(false);
  const [placeError, setPlaceError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<"idle" | "submitting" | "unknown" | "confirmed" | "failed">("idle");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const confirmKey = useRef(newOperationKey("web-confirm"));
  const confirmDraftId = useRef<string | null>(null);

  const hydrate = (
    next: MatterDraft,
    impact: string[] = [],
    affected: MatterAffectedSchedule[] = [],
  ) => {
    setCurrent(next);
    setTitle(next.payload.title ?? "");
    setGoal(next.payload.goal ?? "");
    setNextStep(next.payload.next_step ?? "");
    setScheduleTitle(next.payload.schedule?.title ?? "");
    setStartsAt(toLocalInput(next.payload.schedule?.starts_at));
    setEndsAt(toLocalInput(next.payload.schedule?.ends_at));
    setLocation(next.payload.schedule?.location ?? "");
    setPlaceQuery(next.payload.schedule?.location ?? "");
    setPlaceCity(next.payload.schedule?.location_city ?? "");
    setPlaceCandidates([]);
    setSelectedPlaceToken(null);
    setPlaceError(null);
    setTodos(editableTodos(next.payload.todos));
    setCreateSchedule(Boolean(next.payload.schedule));
    setSelectedMatter(next.matter_id ?? "");
    setSelectedOption(next.payload.selected_option ?? null);
    setSelectedImpact(impact);
    setSelectedAffected(affected);
    setAcknowledge(false);
  };

  const load = async () => {
    if (!draft) return;
    setLoading(true);
    setLoadError(null);
    try {
      const next = await getDraft(draft.draft_id);
      hydrate(next);
      setMessage("已读取服务器最新草稿。");
    } catch (caught) {
      setLoadError(caught instanceof Error ? caught.message : "读取草稿失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!open || !draft) return;
    if (confirmDraftId.current !== draft.draft_id) {
      confirmKey.current = newOperationKey("web-confirm");
      confirmDraftId.current = draft.draft_id;
    }
    setCurrent(draft);
    hydrate(draft);
    setSyncToIos(false);
    setAcknowledge(false);
    setMessage(null);
    setFieldError(null);
    setOutcome("idle");
    setLoadError(null);
    setConfirmDiscard(false);
    setConfirmCancel(false);
    void load();
    // The draft id is the reset boundary; loading the same id must not discard edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draft?.draft_id]);

  const mode = classifyMatterDraft(current);
  const directSchedule = mode === "schedule";
  const directTodo = mode === "todo";
  const matterFieldsVisible = !directSchedule && !directTodo;
  const scheduleVisible =
    directSchedule ||
    mode === "create" ||
    (mode === "matter" && (createSchedule || Boolean(current?.payload.schedule)));
  const todosVisible = !directSchedule && (directTodo || todos.length > 0);
  const conflict = current?.conflicts ?? null;
  const conflictNote = useMemo(() => conflictText(conflict), [conflict]);
  const routeUnverified = conflictHasUnverifiedRoute(conflict);
  const isTerminal = current ? current.status !== "preview" : false;
  const originalTodos = useMemo(() => editableTodos(current?.payload.todos), [current]);
  const dirty = Boolean(
    current &&
      (title !== (current.payload.title ?? "") ||
        goal !== (current.payload.goal ?? "") ||
        nextStep !== (current.payload.next_step ?? "") ||
        scheduleTitle !== (current.payload.schedule?.title ?? "") ||
        startsAt !== toLocalInput(current.payload.schedule?.starts_at) ||
        endsAt !== toLocalInput(current.payload.schedule?.ends_at) ||
        location !== (current.payload.schedule?.location ?? "") ||
        createSchedule !== Boolean(current.payload.schedule) ||
        !todosEqual(todos, originalTodos)),
  );
  const candidates = current?.candidates ?? [];
  const needsChoice = candidates.length > 0;
  const hasConflictOptions = Boolean(conflict?.has_conflict && (conflict.options?.length ?? 0) > 0);
  const selectedOptionValue =
    conflict?.options.find((option) => option.option_id === selectedOption) ?? null;
  const selectedOptionBlocked = Boolean(
    selectedOptionValue && !selectedOptionValue.executable,
  );
  const clearedReminder = todos.some(
    (todo) => todo.originalReminderAt && !todo.reminderAt,
  );

  const markManualEdit = () => {
    const cleared = manualEditSelection();
    setSelectedOption(cleared.selectedOption);
    setAcknowledge(cleared.acknowledge);
    setSelectedImpact([]);
    setSelectedAffected([]);
    setFieldError(null);
  };

  const requestClose = () => {
    if (busy || actionBusy) return;
    if (dirty) {
      setConfirmDiscard(true);
      return;
    }
    onClose();
  };

  const selectMatter = async (matterId: string) => {
    if (!current) return;
    setActionBusy("select");
    setFieldError(null);
    try {
      const next = await selectDraftMatter(current.draft_id, current.version, matterId);
      hydrate(next);
      setMessage("已绑定所选事项，冲突已按服务器结果重新计算。");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "选择事项失败");
      await load();
    } finally {
      setActionBusy(null);
    }
  };

  const applyOption = async (optionId: "A" | "B" | "C") => {
    if (!current) return;
    const option = current.conflicts?.options.find((item) => item.option_id === optionId) ?? null;
    if (option && !option.executable) {
      setMessage(option.unavailable_reason || "当前方案不能完整执行，请选择其他方案。");
      setFieldError(null);
      return;
    }
    const before = current.payload;
    setActionBusy(`option-${optionId}`);
    setFieldError(null);
    try {
      const next = await applyDraftOption(current.draft_id, current.version, optionId);
      hydrate(
        next,
        optionConsequences(option, before, next.payload),
        option?.affected || [],
      );
      setAcknowledge(false);
      setMessage("方案已写入服务器预览，正式安排尚未修改。");
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === "option_not_executable") {
          setMessage(caught.message || option?.unavailable_reason || "当前方案不能完整执行。");
        } else if (isRecordChangedError(caught.code)) {
          setMessage("记录已发生变化，请刷新后检查。");
          await load();
        } else {
          setMessage(caught.message || "服务器没有接受这次方案，请重新读取草稿后再试。");
          await load();
        }
      } else {
        setMessage(caught instanceof Error ? caught.message : "应用方案失败");
        await load();
      }
    } finally {
      setActionBusy(null);
    }
  };

  const updateTodo = (index: number, patch: Partial<EditableTodo>) => {
    markManualEdit();
    setTodos((rows) =>
      rows.map((todo, rowIndex) => (rowIndex === index ? { ...todo, ...patch } : todo)),
    );
  };

  const patch = async () => {
    if (!current) return;
    setFieldError(null);
    if (mode === "create" && !title.trim()) {
      setFieldError("新建事项需要填写标题。");
      return;
    }
    if ((directSchedule || (createSchedule && scheduleVisible)) && !scheduleTitle.trim()) {
      setFieldError("日程标题不能为空。");
      return;
    }
    if ((directSchedule || (createSchedule && scheduleVisible)) && !startsAt) {
      setFieldError("日程开始时间不能为空。");
      return;
    }
    if (todos.some((todo) => !todo.title.trim())) {
      setFieldError("待办标题不能为空。");
      return;
    }
    const start = fromLocalInput(startsAt);
    const end = fromLocalInput(endsAt);
    if (start && end && new Date(end) <= new Date(start)) {
      setFieldError("日程结束时间必须晚于开始时间。");
      return;
    }

    const input: DraftPatchInput = { expected_version: current.version };
    if (matterFieldsVisible) {
      if (title !== (current.payload.title ?? "")) input.title = title.trim();
      if (goal !== (current.payload.goal ?? "")) input.goal = goal.trim();
      if (nextStep !== (current.payload.next_step ?? "")) input.next_step = nextStep.trim();
    }
    const shouldSaveSchedule = directSchedule || (mode !== "todo" && createSchedule && scheduleVisible);
    if (shouldSaveSchedule && start) {
      const locationChanged =
        location.trim() !== (current.payload.schedule?.location ?? "").trim();
      input.schedule = {
        schedule_id: current.payload.schedule?.schedule_id ?? null,
        title: scheduleTitle.trim(),
        starts_at: start,
        ends_at: end,
        location: location.trim() || null,
        latitude: locationChanged ? null : current.payload.schedule?.latitude ?? null,
        longitude: locationChanged ? null : current.payload.schedule?.longitude ?? null,
        coordinate_system: locationChanged
          ? "unknown"
          : current.payload.schedule?.coordinate_system ?? "unknown",
        location_city: locationChanged ? null : current.payload.schedule?.location_city ?? null,
        location_address: locationChanged
          ? null
          : current.payload.schedule?.location_address ?? null,
        location_source: locationChanged
          ? null
          : current.payload.schedule?.location_source ?? null,
        location_external_id: locationChanged
          ? null
          : current.payload.schedule?.location_external_id ?? null,
      };
    } else if (mode === "create" && !createSchedule) {
      input.schedule = null;
    }
    if (!directSchedule && (directTodo || current.payload.todos?.length || todos.length > 0)) {
      input.todos = todos.map((todo) => ({
        todo_id: todo.todoId,
        title: todo.title.trim(),
        due_at: fromLocalInput(todo.dueAt),
        reminder_at: fromLocalInput(todo.reminderAt),
        status: todo.status,
      }));
    }

    setActionBusy("patch");
    setMessage(null);
    try {
      const next = await patchDraft(current.draft_id, input);
      hydrate(next);
      setMessage("修改已保存到预览，冲突和影响已按服务器最新结果更新。");
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (isRecordChangedError(caught.code)) {
          setMessage("记录已发生变化，请刷新后检查。");
          await load();
        } else {
          setMessage(caught.message || "服务器没有接受这次修改，请重新读取草稿后再试。");
        }
      } else {
        setMessage(caught instanceof Error ? caught.message : "保存失败");
        await load();
      }
    } finally {
      setActionBusy(null);
    }
  };

  const searchPlaceCandidates = async () => {
    const query = placeQuery.trim();
    if (!query) {
      setPlaceError("请输入地点名称或更详细的地址。");
      return;
    }
    setPlaceSearching(true);
    setPlaceError(null);
    setSelectedPlaceToken(null);
    try {
      const result = await searchPlaces(
        query,
        placeCity.trim() || null,
        placeSearchType,
      );
      setPlaceCandidates(result.candidates);
      if (result.candidates.length === 0) {
        setPlaceError("没有找到可用候选，请补充城市或更详细的地址后再试。");
      }
    } catch (caught) {
      setPlaceCandidates([]);
      setPlaceError(caught instanceof Error ? caught.message : "地点查询失败。");
    } finally {
      setPlaceSearching(false);
    }
  };

  const selectPlaceCandidate = async () => {
    if (!current || !selectedPlaceToken) return;
    setActionBusy("place");
    setPlaceError(null);
    try {
      const next = await confirmPlaceSelection(current.draft_id, selectedPlaceToken);
      hydrate(next);
      setMessage("地点已写入服务器预览，路线与冲突已重新计算。");
    } catch (caught) {
      if (caught instanceof ApiError && isRecordChangedError(caught.code)) {
        setPlaceError("记录已发生变化，请刷新后检查。");
        await load();
      } else {
        setPlaceError(
          caught instanceof Error ? caught.message : "这个地点候选不能保存。",
        );
      }
    } finally {
      setActionBusy(null);
    }
  };

  const confirm = async () => {
    if (!current) return;
    if (dirty) {
      setFieldError("请先保存当前修改并重新核对路线与冲突。");
      return;
    }
    if (needsChoice && !selectedMatter) {
      setFieldError("请先选择这条整理要接续哪件事项。");
      return;
    }
    if (hasConflictOptions && !selectedOption) {
      setFieldError("请先选择服务器给出的调整方案。");
      return;
    }
    if (selectedOptionBlocked) {
      setFieldError("所选方案当前不可执行，请重新选择。");
      return;
    }
    if (conflict?.has_conflict && !acknowledge) {
      setFieldError("服务器仍报告冲突，请明确确认风险后再提交。");
      return;
    }
    setBusy(true);
    setOutcome("submitting");
    setFieldError(null);
    try {
      const response = await confirmDraft(
        current.draft_id,
        {
          expected_version: current.version,
          ...(selectedMatter ? { matter_id: selectedMatter } : {}),
          create_schedule: directSchedule || createSchedule,
          sync_to_ios: syncToIos,
          acknowledge_conflict: Boolean(conflict?.has_conflict && acknowledge),
          ...(selectedOption ? { option_id: selectedOption } : {}),
        },
        confirmKey.current,
      );
      setCurrent(response.draft);
      if (!response.applied) {
        setOutcome("failed");
        setMessage("服务器没有确认这次写入，请重新检查草稿后再试。");
        return;
      }
      setOutcome("confirmed");
      onConfirmed(response);
    } catch (caught) {
      if (caught instanceof ApiError) {
        if (caught.code === "option_not_executable") {
          setOutcome("failed");
          setMessage(caught.message || "当前方案不能完整执行，请选择其他方案。");
        } else if (isRecordChangedError(caught.code)) {
          setMessage("记录已发生变化，请刷新后检查。");
          setOutcome("failed");
          await load();
        } else if (caught.code === "conflict_detected") {
          setMessage("仍有未接受的行程风险，请重新检查调整方案。");
          setOutcome("failed");
          await load();
        } else if (
          caught.status >= 500 ||
          caught.code.includes("unknown") ||
          caught.code.includes("timeout")
        ) {
          setOutcome("unknown");
          setMessage(caught.message || "结果未知，请重新读取草稿后再检查。");
        } else {
          setOutcome("failed");
          setMessage(caught.message || "服务器没有接受这次确认，请重新检查草稿。");
        }
      } else {
        setOutcome("unknown");
        setMessage(caught instanceof Error ? caught.message : "结果未知");
      }
    } finally {
      setBusy(false);
    }
  };

  const cancelDraft = async () => {
    if (!current) return;
    setActionBusy("cancel");
    setFieldError(null);
    try {
      const next = await cancelDraftRequest(current.draft_id);
      setCurrent(next);
      setConfirmCancel(false);
      setMessage("这条草稿已取消，不会写入正式安排。");
      onDraftCancelled?.(next);
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "取消草稿失败");
    } finally {
      setActionBusy(null);
    }
  };

  return (
    <Sheet title={sheetTitle(mode)} open={open} onClose={requestClose}>
      <div className="m-stack-12">
        <div className="draft-kind">
          <span className="draft-kind__label">待确认 · {primarySummary(mode)}</span>
          <span className="micro">确认前不会写入正式日程、待办或提醒</span>
        </div>
        {loading ? <p className="caption">正在读取服务器最新草稿…</p> : null}
        {loadError ? <p className="danger-text">{loadError}</p> : null}
        {current ? (
          <>
            {isTerminal ? (
              <div className="notice notice--amber">
                这条草稿状态为「{current.status === "confirmed" ? "已确认" : "已取消"}」，不能再修改或确认。
              </div>
            ) : null}

            {needsChoice ? (
              <div className="notice notice--amber">
                <strong>请选择这条整理要接续哪件事项</strong>
                <p className="caption" style={{ margin: "4px 0 8px" }}>
                  服务器返回了多个候选；选择后会重新绑定版本并计算冲突。
                </p>
                {candidates.map((candidate) => {
                  const value = String(candidate.matter_id ?? "");
                  return (
                    <button
                      key={value}
                      type="button"
                      className={`choice-card${selectedMatter === value ? " choice-card--selected" : ""}`}
                      disabled={actionBusy !== null || busy}
                      onClick={() => void selectMatter(value)}
                    >
                      <span className="choice-card__dot">
                        {selectedMatter === value ? <Check size={12} /> : null}
                      </span>
                      <span className="grow">
                        <span className="choice-card__title">
                          {String(candidate.title ?? "未命名事项")}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            ) : null}

            {matterFieldsVisible ? (
              <div className="card card--tight">
                <p className="micro" style={{ margin: "0 0 10px" }}>
                  {mode === "create" ? "新事项内容" : "只修改需要变化的字段"}
                </p>
                <label className="field">
                  <span className="field__label">事项标题</span>
                  <input
                    className="input"
                    value={title}
                    onChange={(event) => {
                      markManualEdit();
                      setTitle(event.target.value);
                    }}
                    placeholder={mode === "create" ? "例如：准备周五面谈" : undefined}
                  />
                </label>
                <label className="field">
                  <span className="field__label">目标与背景</span>
                  <textarea
                    className="input"
                    rows={3}
                    value={goal}
                    onChange={(event) => {
                      markManualEdit();
                      setGoal(event.target.value);
                    }}
                  />
                </label>
                <label className="field" style={{ marginBottom: 0 }}>
                  <span className="field__label">下一步</span>
                  <input
                    className="input"
                    value={nextStep}
                    onChange={(event) => {
                      markManualEdit();
                      setNextStep(event.target.value);
                    }}
                  />
                </label>
              </div>
            ) : (
              <div className="notice">
                {directSchedule
                  ? "只调整日程。不会因事项标题为空阻止保存，也不会覆盖现有事项标题。"
                  : "只调整待办。截止时间和提醒时间分开保存，不会覆盖事项标题。"}
              </div>
            )}

            {scheduleVisible ? (
              <div className="card card--tight">
                <div className="flex items-center justify-between gap-10">
                  <p className="micro" style={{ margin: 0 }}>
                    {directSchedule ? "这条日程" : "日程预览"}
                  </p>
                  {!directSchedule ? (
                    <label className="flex items-center gap-8">
                      <input
                        type="checkbox"
                        checked={createSchedule}
                        onChange={(event) => {
                          markManualEdit();
                          setCreateSchedule(event.target.checked);
                        }}
                      />
                      <span className="micro">确认时写入正式日程</span>
                    </label>
                  ) : null}
                </div>
                {createSchedule || directSchedule ? (
                  <div className="m-stack-10 mt-10">
                    <label className="field">
                      <span className="field__label">日程标题</span>
                      <input
                        className="input"
                        value={scheduleTitle}
                        onChange={(event) => {
                          markManualEdit();
                          setScheduleTitle(event.target.value);
                        }}
                      />
                    </label>
                    <div className="grid-2">
                      <label className="field">
                        <span className="field__label">开始</span>
                        <input
                          className="input"
                          type="datetime-local"
                          value={startsAt}
                          onChange={(event) => {
                            markManualEdit();
                            setStartsAt(event.target.value);
                          }}
                        />
                      </label>
                      <label className="field">
                        <span className="field__label">结束</span>
                        <input
                          className="input"
                          type="datetime-local"
                          value={endsAt}
                          onChange={(event) => {
                            markManualEdit();
                            setEndsAt(event.target.value);
                          }}
                        />
                      </label>
                    </div>
                    <label className="field" style={{ marginBottom: 0 }}>
                      <span className="field__label">地点</span>
                      <input
                        className="input"
                        value={location}
                        onChange={(event) => {
                          markManualEdit();
                          setLocation(event.target.value);
                          setPlaceCandidates([]);
                          setSelectedPlaceToken(null);
                        }}
                        placeholder="可留空"
                      />
                    </label>
                    <p className="micro" style={{ margin: "4px 0 0" }}>
                      {current.payload.schedule?.latitude !== null &&
                      current.payload.schedule?.latitude !== undefined
                        ? `已确认地点：${current.payload.schedule.location || location}${current.payload.schedule.location_address ? ` · ${current.payload.schedule.location_address}` : ""}${current.payload.schedule.location_city ? ` · ${current.payload.schedule.location_city}` : ""}`
                        : "当前只有文字地点，没有已确认坐标。手工输入地点不会自动生成坐标。"}
                    </p>
                    <div className="place-search">
                      <p className="micro" style={{ margin: 0 }}>查找并确认地点</p>
                      <div className="grid-2">
                        <label className="field">
                          <span className="field__label">名称或地址</span>
                          <input
                            className="input"
                            value={placeQuery}
                            onChange={(event) => setPlaceQuery(event.target.value)}
                            placeholder="例如：静安寺"
                          />
                        </label>
                        <label className="field">
                          <span className="field__label">城市</span>
                          <input
                            className="input"
                            value={placeCity}
                            onChange={(event) => setPlaceCity(event.target.value)}
                            placeholder="例如：上海"
                          />
                        </label>
                      </div>
                      <div className="chip-row">
                        {(["place", "address"] as const).map((type) => (
                          <button
                            key={type}
                            type="button"
                            className="chip chip--sm"
                            aria-pressed={placeSearchType === type}
                            onClick={() => {
                              setPlaceSearchType(type);
                              setPlaceCandidates([]);
                              setSelectedPlaceToken(null);
                            }}
                          >
                            {type === "place" ? "地点" : "地址"}
                          </button>
                        ))}
                        <button
                          type="button"
                          className="btn btn--secondary btn--small"
                          onClick={() => void searchPlaceCandidates()}
                          disabled={placeSearching || actionBusy !== null}
                        >
                          {placeSearching ? "正在查找…" : "查找候选"}
                        </button>
                      </div>
                      {placeCandidates.length > 0 ? (
                        <div className="m-stack-8 mt-10" role="radiogroup" aria-label="地点候选">
                          {placeCandidates.map((candidate) => (
                            <button
                              key={candidate.provider_place_id}
                              type="button"
                              role="radio"
                              aria-checked={selectedPlaceToken === candidate.selection_token}
                              className={`choice-card${selectedPlaceToken === candidate.selection_token ? " choice-card--selected" : ""}`}
                              onClick={() => setSelectedPlaceToken(candidate.selection_token)}
                            >
                              <span className="choice-card__dot">
                                {selectedPlaceToken === candidate.selection_token ? <Check size={12} /> : null}
                              </span>
                              <span className="grow">
                                <span className="choice-card__title">{candidate.name}</span>
                                <span className="choice-card__detail">
                                  {[candidate.city, candidate.address].filter(Boolean).join(" · ") || "地址未提供"}
                                  {" · "}
                                  {candidate.coordinate_system === "gcj02"
                                    ? "高德坐标 GCJ-02"
                                    : candidate.coordinate_system === "wgs84"
                                      ? "WGS-84"
                                      : "坐标系未说明"}
                                </span>
                              </span>
                            </button>
                          ))}
                        </div>
                      ) : null}
                      {selectedPlaceToken ? (
                        <button
                          type="button"
                          className="btn btn--secondary btn--small mt-8"
                          onClick={() => void selectPlaceCandidate()}
                          disabled={actionBusy !== null || busy}
                        >
                          {actionBusy === "place" ? "正在保存地点…" : "使用这个地点并重新计算路线"}
                        </button>
                      ) : null}
                      {placeError ? (
                        <p className="danger-text" style={{ margin: "6px 0 0" }}>
                          {placeError}
                        </p>
                      ) : null}
                    </div>
                  </div>
                ) : (
                  <p className="caption mt-8" style={{ marginBottom: 0 }}>
                    这次只更新事项，不写入草稿里的日程。
                  </p>
                )}
              </div>
            ) : null}

            {todosVisible ? (
              <div className="card card--tight">
                <p className="micro" style={{ margin: "0 0 6px" }}>
                  {directTodo ? "待办字段" : `待办（${todos.length} 项）`}
                </p>
                {todos.length === 0 ? (
                  <p className="caption" style={{ margin: 0 }}>这次草稿没有待办变更。</p>
                ) : (
                  <div className="m-stack-12">
                    {todos.map((todo, index) => (
                      <div key={`${todo.todoId ?? "new"}-${index}`} className="draft-todo">
                        <label className="field">
                          <span className="field__label">待办标题</span>
                          <input
                            className="input"
                            value={todo.title}
                            onChange={(event) => updateTodo(index, { title: event.target.value })}
                          />
                        </label>
                        <div className="grid-2">
                          <label className="field">
                            <span className="field__label">截止时间</span>
                            <input
                              className="input"
                              type="datetime-local"
                              value={todo.dueAt}
                              onChange={(event) => updateTodo(index, { dueAt: event.target.value })}
                            />
                          </label>
                          <label className="field">
                            <span className="field__label">提醒时间</span>
                            <input
                              className="input"
                              type="datetime-local"
                              value={todo.reminderAt}
                              onChange={(event) =>
                                updateTodo(index, { reminderAt: event.target.value })
                              }
                            />
                          </label>
                        </div>
                        <div className="flex items-center justify-between gap-10">
                          <span className="micro">
                            {todo.reminderAt
                              ? `提醒 ${dateTimeLabel(fromLocalInput(todo.reminderAt) || "")}`
                              : "未设置提醒"}
                          </span>
                          {todo.reminderAt ? (
                            <button
                              type="button"
                              className="link-button danger-text"
                              onClick={() => updateTodo(index, { reminderAt: "" })}
                            >
                              清空提醒
                            </button>
                          ) : null}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : null}

            {clearedReminder ? (
              <div className="notice notice--amber">
                确认后会取消这条待办对应的待发提醒，不会删除待办本身。
              </div>
            ) : null}

            {dirty ? (
              <div className="notice notice--amber">
                当前有未保存的修改，旧路线耗时和冲突结果不再作为有效结论。请先保存并重新核对。
              </div>
            ) : null}

            {!dirty && routeUnverified && !conflict?.has_conflict ? (
              <div className="notice notice--amber">
                路程尚未核实。服务器没有发现确定的时间冲突，但不能视为所有风险已排除。
              </div>
            ) : null}

            {!dirty && conflict?.has_conflict ? (
              <div className="notice notice--amber">
                <strong>
                  <AlertTriangle size={14} /> 时间冲突
                </strong>
                <p className="caption" style={{ margin: "4px 0 0" }}>{conflictNote}</p>
                {conflict.conflicts.map((item, index) => (
                  <RouteTextCard
                    key={`${item.schedule_id ?? "conflict"}-${index}`}
                    embedded
                    originLabel={
                      item.relation === "previous"
                        ? item.title
                        : item.relation === "next"
                          ? scheduleTitle || "本次安排"
                          : item.title
                    }
                    destinationLabel={
                      item.relation === "previous"
                        ? scheduleTitle || location || "本次安排"
                        : item.relation === "next"
                          ? item.title
                          : scheduleTitle || location || "本次安排"
                    }
                    departureAt={
                      item.relation === "previous"
                        ? item.ends_at
                        : item.relation === "next"
                          ? fromLocalInput(endsAt)
                          : item.ends_at
                    }
                    arrivalAt={
                      item.relation === "next"
                        ? item.starts_at
                        : fromLocalInput(startsAt)
                    }
                    mode={null}
                    durationSeconds={item.eta_seconds}
                    instruction={conflictReasonText(item.reason, item.eta_status)}
                    state={
                      item.eta_status === "unverified"
                        ? "unverified"
                        : item.relation === "overlap"
                          ? "tight"
                          : "tight"
                    }
                    notice={
                      item.relation === "overlap"
                        ? "这是时间重叠，不是通勤耗时结论。"
                        : `两项安排间隔 ${Math.round(item.gap_seconds / 60)} 分钟。`
                    }
                    mapTarget={{
                      label:
                        current.payload.schedule?.location_address ||
                        location ||
                        null,
                      latitude: current.payload.schedule?.latitude ?? null,
                      longitude: current.payload.schedule?.longitude ?? null,
                      coordinateSystem:
                        current.payload.schedule?.coordinate_system ?? "unknown",
                      address: current.payload.schedule?.location_address ?? null,
                      city: current.payload.schedule?.location_city ?? null,
                    }}
                  />
                ))}
                {conflict.options.length > 0 ? (
                  <div className="m-stack-8 mt-8" role="radiogroup" aria-label="调整方案">
                    {conflict.options.map((option) => (
                      <button
                        key={option.option_id}
                        type="button"
                        role="radio"
                        aria-checked={selectedOption === option.option_id}
                        className={`choice-card${selectedOption === option.option_id ? " choice-card--selected" : ""}`}
                        disabled={actionBusy !== null || busy || !option.executable}
                        onClick={() => void applyOption(option.option_id)}
                      >
                        <span className="choice-card__dot">
                          {selectedOption === option.option_id ? (
                            <Check size={12} />
                          ) : (
                            option.option_id
                          )}
                        </span>
                        <span className="grow">
                          <span className="choice-card__title">
                            {option.label}
                            {option.recommended ? " · 推荐" : ""}
                          </span>
                          <span className="choice-card__detail">{option.detail}</span>
                          {option.affected.length > 0 ? (
                            <span className="choice-card__detail">
                              影响：{affectedScheduleSummary(option.affected[0])}
                            </span>
                          ) : null}
                          {!option.executable ? (
                            <span className="choice-card__detail danger-text">
                              不可执行：{option.unavailable_reason || "请选择其他方案"}
                            </span>
                          ) : null}
                        </span>
                      </button>
                    ))}
                  </div>
                ) : null}
                <label className="flex items-start gap-8 mt-8">
                  <input
                    type="checkbox"
                    checked={acknowledge}
                    onChange={(event) => setAcknowledge(event.target.checked)}
                    style={{ marginTop: 3 }}
                  />
                  <span className="caption" style={{ margin: 0 }}>
                    我已了解仍存在的行程风险，确认继续。
                  </span>
                </label>
              </div>
            ) : null}

            {selectedOption && selectedImpact.length > 0 ? (
              <div className="notice notice--green">
                <strong>所选方案会改变</strong>
                <ul className="compact-list">
                  {selectedImpact.map((line) => <li key={line}>{line}</li>)}
                </ul>
                {selectedAffected.length > 0 ? (
                  <details className="affected-details">
                    <summary>查看修改前后</summary>
                    {selectedAffected.map((item, index) => (
                      <p
                        key={`${item.schedule_id}-${index}`}
                        className="micro"
                        style={{ margin: "6px 0 0" }}
                      >
                        {item.matter_title ? `事项「${item.matter_title}」 · ` : ""}
                        {item.title}：{dateTimeLabel(item.before_starts_at || "")} 至{" "}
                        {dateTimeLabel(item.before_ends_at || "")} →{" "}
                        {dateTimeLabel(item.after_starts_at || "")} 至{" "}
                        {dateTimeLabel(item.after_ends_at || "")}
                      </p>
                    ))}
                  </details>
                ) : null}
                <p className="micro" style={{ margin: "6px 0 0" }}>
                  这些变化已进入服务器预览，仍需点击“确认更新”才生效。
                </p>
              </div>
            ) : null}

            <label className="flex items-center gap-8">
              <input
                type="checkbox"
                checked={syncToIos}
                onChange={(event) => setSyncToIos(event.target.checked)}
              />
              <span className="caption" style={{ margin: 0 }}>
                同时加入 iOS 待同步（不等于已写入苹果日历或提醒）
              </span>
            </label>

            {fieldError ? <p className="danger-text" style={{ margin: 0 }}>{fieldError}</p> : null}
            {message ? <p className="caption" style={{ margin: 0 }}>{message}</p> : null}
            {outcome === "unknown" ? (
              <p className="danger-text" style={{ margin: 0 }}>
                结果未知。保留同一次操作标识，可重新读取草稿或重试；不要换新标识重复写入。
              </p>
            ) : null}

            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => void patch()}
              disabled={busy || actionBusy !== null || isTerminal}
            >
              {actionBusy === "patch" ? "正在保存…" : "保存修改并重新核对"}
            </button>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void confirm()}
              disabled={
                busy ||
                actionBusy !== null ||
                isTerminal ||
                (mode === "create" && !title.trim()) ||
                (directSchedule && (!scheduleTitle.trim() || !startsAt)) ||
                (needsChoice && !selectedMatter) ||
                dirty ||
                selectedOptionBlocked ||
                (hasConflictOptions && !selectedOption)
              }
            >
              {busy ? "正在确认…" : outcome === "unknown" ? "重试同一次确认" : "确认更新"}
            </button>
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => void load()}
              disabled={busy || loading || actionBusy !== null}
            >
              <RefreshCw size={14} /> 重新读取草稿
            </button>

            {current.status === "preview" ? (
              confirmCancel ? (
                <div className="notice notice--amber">
                  <strong>取消这条草稿？</strong>
                  <p className="caption" style={{ margin: "4px 0 8px" }}>
                    取消只影响服务器预览，不会撤销已正式生效的变更。
                  </p>
                  <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="btn btn--secondary btn--small"
                      onClick={() => setConfirmCancel(false)}
                    >
                      继续保留
                    </button>
                    <button
                      type="button"
                      className="btn btn--danger btn--small"
                      onClick={() => void cancelDraft()}
                      disabled={actionBusy !== null || busy}
                    >
                      {actionBusy === "cancel" ? "正在取消…" : "取消草稿"}
                    </button>
                  </div>
                </div>
              ) : (
                <button
                  type="button"
                  className="btn btn--quiet danger-text"
                  onClick={() => setConfirmCancel(true)}
                  disabled={busy || actionBusy !== null}
                >
                  取消这条草稿
                </button>
              )
            ) : null}

            {confirmDiscard ? (
              <div className="notice notice--amber">
                <strong>有未保存的修改</strong>
                <p className="caption" style={{ margin: "4px 0 8px" }}>
                  关闭会丢弃这次编辑；服务器上的草稿不会被修改。
                </p>
                <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
                  <button
                    type="button"
                    className="btn btn--secondary btn--small"
                    onClick={() => setConfirmDiscard(false)}
                  >
                    继续编辑
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger btn--small"
                    onClick={onClose}
                  >
                    放弃修改并关闭
                  </button>
                </div>
              </div>
            ) : null}
            <button
              type="button"
              className="btn btn--quiet"
              onClick={requestClose}
              disabled={busy || actionBusy !== null}
            >
              关闭，不提交
            </button>
          </>
        ) : null}
      </div>
    </Sheet>
  );
}
