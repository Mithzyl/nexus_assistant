"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, Check, RefreshCw } from "lucide-react";

import type { DraftConfirmResponse, MatterConflict, MatterDraft } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  applyDraftOption,
  confirmDraft,
  getDraft,
  newOperationKey,
  patchDraft,
  selectDraftMatter,
} from "@/lib/mobile/matters-api";
import { dateTimeLabel } from "@/lib/mobile/work-plan";
import { Sheet } from "./ui";

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

function conflictText(conflict: MatterConflict | null): string | null {
  if (!conflict?.has_conflict) return null;
  const item = conflict.conflicts[0];
  if (!item) return "服务器报告存在时间冲突，请先确认风险。";
  if (item.eta_status === "unverified") return `${item.title}：路程耗时尚未核实，不能判定时间充裕。${item.reason}`;
  return `${item.title}：${item.reason}`;
}

export function MatterDraftSheet({
  draft,
  open,
  onClose,
  onConfirmed,
}: {
  draft: MatterDraft | null;
  open: boolean;
  onClose: () => void;
  onConfirmed: (response: DraftConfirmResponse) => void;
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
  const [syncToIos, setSyncToIos] = useState(false);
  const [acknowledge, setAcknowledge] = useState(false);
  const [selectedMatter, setSelectedMatter] = useState("");
  const [selectedOption, setSelectedOption] = useState<"A" | "B" | "C" | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<"idle" | "submitting" | "unknown" | "confirmed" | "failed">("idle");
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const confirmKey = useRef(newOperationKey("web-confirm"));
  const confirmDraftId = useRef<string | null>(null);

  const hydrate = (next: MatterDraft) => {
    setCurrent(next);
    setTitle(next.payload.title ?? "");
    setGoal(next.payload.goal ?? "");
    setNextStep(next.payload.next_step ?? "");
    setScheduleTitle(next.payload.schedule?.title ?? "");
    setStartsAt(toLocalInput(next.payload.schedule?.starts_at));
    setEndsAt(toLocalInput(next.payload.schedule?.ends_at));
    setLocation(next.payload.schedule?.location ?? "");
    setCreateSchedule(Boolean(next.payload.schedule));
    setSelectedMatter(next.matter_id ?? "");
    setSelectedOption(next.payload.selected_option ?? null);
  };

  const load = async () => {
    if (!draft) return;
    setLoading(true);
    setLoadError(null);
    try {
      hydrate(await getDraft(draft.draft_id));
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
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, draft?.draft_id]);

  const conflict = current?.conflicts ?? null;
  const conflictNote = useMemo(() => conflictText(conflict), [conflict]);
  const isTerminal = current ? current.status !== "preview" : false;
  const dirty = Boolean(
    current &&
      (title.trim() !== (current.payload.title ?? "").trim() ||
        goal.trim() !== (current.payload.goal ?? "").trim() ||
        nextStep.trim() !== (current.payload.next_step ?? "").trim() ||
        scheduleTitle.trim() !== (current.payload.schedule?.title ?? "").trim() ||
        startsAt !== toLocalInput(current.payload.schedule?.starts_at) ||
        endsAt !== toLocalInput(current.payload.schedule?.ends_at) ||
        location.trim() !== (current.payload.schedule?.location ?? "").trim()),
  );
  const requestClose = () => {
    if (busy) return;
    if (dirty) {
      setConfirmDiscard(true);
      return;
    }
    onClose();
  };
  const candidates = current?.candidates ?? [];
  const needsChoice = candidates.length > 0;

  const selectMatter = async (matterId: string) => {
    if (!current) return;
    setActionBusy("select");
    setFieldError(null);
    try {
      const next = await selectDraftMatter(current.draft_id, current.version, matterId);
      hydrate(next);
      setMessage("已绑定所选事项，冲突已重新计算。");
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "选择事项失败");
      await load();
    } finally {
      setActionBusy(null);
    }
  };

  const applyOption = async (optionId: "A" | "B" | "C") => {
    if (!current) return;
    setActionBusy(`option-${optionId}`);
    setFieldError(null);
    try {
      const next = await applyDraftOption(current.draft_id, current.version, optionId);
      hydrate(next);
      setSelectedOption(optionId);
      setAcknowledge(false);
      setMessage("调整方案已写入预览，尚未修改正式安排。请核对后确认。");
    } catch (caught) {
      if (caught instanceof ApiError) setMessage(caught.message || "服务器没有接受这次操作，请重新读取草稿后再试。");
      else setMessage(caught instanceof Error ? caught.message : "应用方案失败");
      await load();
    } finally {
      setActionBusy(null);
    }
  };

  const patch = async () => {
    if (!current) return;
    setFieldError(null);
    if (!title.trim()) {
      setFieldError("标题不能为空。");
      return;
    }
    const start = fromLocalInput(startsAt);
    const end = fromLocalInput(endsAt);
    if (createSchedule) {
      if (!scheduleTitle.trim() || !start) {
        setFieldError("日程标题和开始时间都必须填写。");
        return;
      }
      if (end && new Date(end) <= new Date(start)) {
        setFieldError("日程结束时间必须晚于开始时间。");
        return;
      }
    }
    setActionBusy("patch");
    setMessage(null);
    try {
      const next = await patchDraft(current.draft_id, {
        expected_version: current.version,
        title: title.trim(),
        goal: goal.trim(),
        next_step: nextStep.trim(),
        schedule: createSchedule
          ? {
              schedule_id: current.payload.schedule?.schedule_id ?? null,
              title: scheduleTitle.trim(),
              starts_at: start as string,
              ends_at: end,
              location: location.trim() || null,
            }
          : null,
      });
      hydrate(next);
      setSelectedOption(null);
      setMessage("修改已保存，冲突已按服务器最新结果重新计算。");
    } catch (caught) {
      if (caught instanceof ApiError) setMessage(caught.message || "服务器没有接受这次操作，请重新读取草稿后再试。");
      else setMessage(caught instanceof Error ? caught.message : "保存失败");
      await load();
    } finally {
      setActionBusy(null);
    }
  };

  const confirm = async () => {
    if (!current) return;
    if (needsChoice && !selectedMatter) {
      setFieldError("请先选择这条整理要接续哪件事项。");
      return;
    }
    if (conflict?.has_conflict && !acknowledge) {
      setFieldError("服务器报告冲突，必须明确接受风险后才能确认。");
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
          create_schedule: createSchedule,
          sync_to_ios: syncToIos,
          acknowledge_conflict: Boolean(conflict?.has_conflict && acknowledge),
          ...(selectedOption ? { option_id: selectedOption } : {}),
        },
        confirmKey.current,
      );
      setOutcome("confirmed");
      setCurrent(response.draft);
      onConfirmed(response);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setMessage(caught.message || "服务器没有接受这次操作，请重新读取草稿后再试。");
        if (caught.code === "conflict_detected" || caught.code === "preview_stale" || caught.code === "matter_changed" || caught.code === "option_not_selected") {
          setOutcome("failed");
          await load();
        } else if (caught.status >= 500 || caught.code.includes("unknown") || caught.code.includes("timeout")) {
          setOutcome("unknown");
        } else {
          setOutcome("failed");
        }
      } else {
        setOutcome("unknown");
        setMessage(caught instanceof Error ? caught.message : "结果未知");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <Sheet
      title={current?.kind === "create" ? "整理成事项" : "确认事项变更"}
      open={open}
      onClose={requestClose}
    >
      <div className="m-stack-12">
        <p className="micro" style={{ margin: 0 }}>
          待确认 · 确认前不会写入正式日程或提醒。关闭、返回和切换方案都不等于确认。
        </p>
        {loading ? <p className="caption">正在读取最新草稿…</p> : null}
        {loadError ? <p className="danger-text">{loadError}</p> : null}
        {current ? (
          <>
            {isTerminal ? <div className="notice notice--amber">这条草稿状态为「{current.status}」，不能再修改或确认。</div> : null}
            {needsChoice ? (
              <div className="notice notice--amber">
                <strong>请选择这条整理要接续哪件事项</strong>
                <p className="caption" style={{ margin: "4px 0 8px" }}>服务器返回了多个候选；选择后会重新绑定版本并重新计算冲突。</p>
                {candidates.map((candidate) => {
                  const value = String(candidate.matter_id ?? "");
                  return (
                    <button key={value} type="button" className={`choice-card${selectedMatter === value ? " choice-card--selected" : ""}`} disabled={actionBusy !== null} onClick={() => void selectMatter(value)}>
                      <span className="choice-card__dot">{selectedMatter === value ? <Check size={12} /> : null}</span>
                      <span className="grow"><span className="choice-card__title">{String(candidate.title ?? "未命名事项")}</span></span>
                    </button>
                  );
                })}
              </div>
            ) : null}

            <label className="field"><span className="field__label">标题</span><input className="input" value={title} onChange={(event) => setTitle(event.target.value)} /></label>
            <label className="field"><span className="field__label">目标与背景</span><textarea className="input" rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} /></label>
            <label className="field"><span className="field__label">下一步</span><input className="input" value={nextStep} onChange={(event) => setNextStep(event.target.value)} /></label>

            <div className="card card--tight">
              <label className="flex items-center gap-8">
                <input type="checkbox" checked={createSchedule} onChange={(event) => setCreateSchedule(event.target.checked)} />
                <span className="caption" style={{ margin: 0 }}>确认时写入这条正式日程</span>
              </label>
              {createSchedule ? (
                <div className="m-stack-10 mt-10">
                  <label className="field"><span className="field__label">日程标题</span><input className="input" value={scheduleTitle} onChange={(event) => setScheduleTitle(event.target.value)} /></label>
                  <div className="grid-2">
                    <label className="field"><span className="field__label">开始</span><input className="input" type="datetime-local" value={startsAt} onChange={(event) => setStartsAt(event.target.value)} /></label>
                    <label className="field"><span className="field__label">结束</span><input className="input" type="datetime-local" value={endsAt} onChange={(event) => setEndsAt(event.target.value)} /></label>
                  </div>
                  <label className="field"><span className="field__label">地点</span><input className="input" value={location} onChange={(event) => setLocation(event.target.value)} /></label>
                </div>
              ) : null}
            </div>

            {current.payload.todos?.length ? (
              <div className="card card--tight">
                <p className="micro" style={{ margin: 0 }}>待办（随确认生效）</p>
                {current.payload.todos.map((todo, index) => (
                  <p key={`${todo.todo_id ?? "new"}-${index}`} className="caption" style={{ margin: "4px 0 0" }}>
                    {todo.status === "done" ? "✓ " : "○ "}{todo.title}
                    {todo.due_at ? ` · 截止 ${dateTimeLabel(todo.due_at)}` : ""}
                    {todo.reminder_at ? ` · 提醒 ${dateTimeLabel(todo.reminder_at)}` : ""}
                  </p>
                ))}
              </div>
            ) : null}

            {conflict?.has_conflict ? (
              <div className="notice notice--amber">
                <strong><AlertTriangle size={14} /> 时间冲突</strong>
                <p className="caption" style={{ margin: "4px 0 0" }}>{conflictNote}</p>
                {conflict.conflicts.map((item, index) => (
                  <p key={`${item.schedule_id ?? "conflict"}-${index}`} className="micro" style={{ margin: "4px 0 0" }}>
                    {item.title} · 间隔 {Math.round(item.gap_seconds / 60)} 分钟
                    {item.eta_seconds ? ` · 路程 ${Math.round(item.eta_seconds / 60)} 分钟` : " · 路程未核实"}
                  </p>
                ))}
                <div className="m-stack-8 mt-8">
                  {conflict.options.map((option) => (
                    <button
                      key={option.option_id}
                      type="button"
                      className={`choice-card${selectedOption === option.option_id ? " choice-card--selected" : ""}`}
                      disabled={actionBusy !== null}
                      onClick={() => void applyOption(option.option_id)}
                    >
                      <span className="choice-card__dot">{selectedOption === option.option_id ? <Check size={12} /> : option.option_id}</span>
                      <span className="grow">
                        <span className="choice-card__title">{option.label}{option.recommended ? " · 推荐" : ""}</span>
                        <span className="choice-card__detail">{option.detail}</span>
                      </span>
                    </button>
                  ))}
                </div>
                <p className="micro" style={{ margin: "8px 0 0" }}>选择方案只会更新预览；正式日程要再点“确认更新”。</p>
                <label className="flex items-center gap-8 mt-8">
                  <input type="checkbox" checked={acknowledge} onChange={(event) => setAcknowledge(event.target.checked)} />
                  <span className="caption" style={{ margin: 0 }}>我已了解剩余冲突风险，接受后确认</span>
                </label>
              </div>
            ) : null}

            <label className="flex items-center gap-8">
              <input type="checkbox" checked={syncToIos} onChange={(event) => setSyncToIos(event.target.checked)} />
              <span className="caption" style={{ margin: 0 }}>同时加入 iOS 待同步（不等于已写入系统日历）</span>
            </label>

            {fieldError ? <p className="danger-text" style={{ margin: 0 }}>{fieldError}</p> : null}
            {message ? <p className="caption" style={{ margin: 0 }}>{message}</p> : null}
            {outcome === "unknown" ? <p className="danger-text" style={{ margin: 0 }}>结果未知：保留同一次操作标识，可重新读取草稿或重试；不要换新键重复创建。</p> : null}

            <button type="button" className="btn btn--secondary" onClick={() => void patch()} disabled={busy || actionBusy !== null || isTerminal}>
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
                (needsChoice && !selectedMatter) ||
                Boolean(conflict?.has_conflict && !acknowledge) ||
                Boolean(conflict?.has_conflict && (conflict.options?.length ?? 0) > 0 && !selectedOption)
              }
            >
              {busy ? "正在确认…" : outcome === "unknown" ? "重试同一次确认" : "确认更新"}
            </button>
            <button type="button" className="btn btn--quiet" onClick={() => void load()} disabled={busy || loading}>
              <RefreshCw size={14} /> 重新读取草稿
            </button>
            {confirmDiscard ? (
              <div className="notice notice--amber">
                <strong>有未保存的修改</strong>
                <p className="caption" style={{ margin: "4px 0 8px" }}>关闭会丢弃这次编辑；服务器上的草稿没有被修改。</p>
                <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
                  <button type="button" className="btn btn--secondary btn--small" onClick={() => setConfirmDiscard(false)}>继续编辑</button>
                  <button type="button" className="btn btn--danger btn--small" onClick={onClose}>放弃修改并关闭</button>
                </div>
              </div>
            ) : null}
            <button type="button" className="btn btn--quiet" onClick={requestClose} disabled={busy}>关闭，不提交</button>
          </>
        ) : null}
      </div>
    </Sheet>
  );
}
