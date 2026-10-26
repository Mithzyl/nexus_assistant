"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Check, Clock3, MapPin, Pencil } from "lucide-react";

import type {
  WorkConflict,
  WorkMatter,
  WorkSchedule,
} from "@/lib/mobile/work-plan";
import { dateTimeLabel, SYNC_STATUS_LABEL } from "@/lib/mobile/work-plan";
import { Notice, Sheet } from "./ui";

export function PreviewBanner({ children }: { children?: React.ReactNode }) {
  return (
    <Notice title="概念预览 · 仅保存在这台浏览器" tone="amber">
      {children ?? "这里的事项、日程和提醒尚未写入 PA 服务器，也不会出现在 iPhone 系统日历中。关闭预览可回到真实空状态。"}
    </Notice>
  );
}

export function ConflictCard({
  conflict,
  onOpen,
}: {
  conflict: WorkConflict;
  onOpen: () => void;
}) {
  return (
    <button type="button" className="change-card" onClick={onOpen}>
      <span className="change-card__line" aria-hidden="true" />
      <span className="grow" style={{ textAlign: "left" }}>
        <span className="change-card__title">
          <AlertTriangle size={15} /> 时间可能来不及
        </span>
        <span className="change-card__body">
          「{conflict.first.title}」{dateTimeLabel(conflict.first.endsAt)} 结束，
          到「{conflict.second.title}」只有 {Math.max(0, conflict.gapMinutes)} 分钟；
          预览按 {conflict.travelMinutes} 分钟路上时间估算（未调用路线服务）。
        </span>
        <span className="change-card__action">查看调整方案 <span aria-hidden="true">›</span></span>
      </span>
    </button>
  );
}

export function ConflictSheet({
  conflict,
  onClose,
  onConfirm,
}: {
  conflict: WorkConflict | null;
  onClose: () => void;
  onConfirm: (optionId: string) => void;
}) {
  const [optionId, setOptionId] = useState("shorten");
  useEffect(() => {
    if (conflict) setOptionId(conflict.options[0]?.id ?? "shorten");
  }, [conflict]);
  return (
    <Sheet title="这次安排怎么调整？" open={Boolean(conflict)} onClose={onClose}>
      {conflict ? (
        <div className="m-stack-12">
          <p className="caption" style={{ margin: 0 }}>
            影响「{conflict.matterTitle}」。确认后只修改本机预览，不会写入服务器或 iOS；
            关闭不会提交。
          </p>
          <div className="m-stack-8" role="radiogroup" aria-label="调整方案">
            {conflict.options.map((option) => (
              <button
                key={option.id}
                type="button"
                role="radio"
                aria-checked={optionId === option.id}
                className={`choice-card${optionId === option.id ? " choice-card--selected" : ""}`}
                onClick={() => setOptionId(option.id)}
              >
                <span className="choice-card__dot" aria-hidden="true">
                  {optionId === option.id ? <Check size={12} /> : null}
                </span>
                <span className="grow">
                  <span className="choice-card__title">{option.title}</span>
                  <span className="choice-card__detail">{option.detail}</span>
                </span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="btn btn--primary"
            onClick={() => onConfirm(optionId)}
          >
            按此更新预览安排
          </button>
          <button type="button" className="btn btn--quiet" onClick={onClose}>
            关闭，不做修改
          </button>
        </div>
      ) : null}
    </Sheet>
  );
}

export function ProgressSheet({
  open,
  title,
  onClose,
  onSubmit,
  note = "只更新本机预览，不会自动完成待办，也不会改动正式安排。",
}: {
  open: boolean;
  title: string;
  onClose: () => void;
  onSubmit: (text: string) => void;
  note?: string;
}) {
  const [text, setText] = useState("");
  useEffect(() => {
    if (open) setText("");
  }, [open]);
  return (
    <Sheet title="补充进展" open={open} onClose={onClose}>
      <div className="m-stack-12">
        <p className="caption" style={{ margin: 0 }}>
          {title} · {note}
        </p>
        <label className="field">
          <span className="field__label">这次发生了什么</span>
          <textarea
            className="input"
            rows={5}
            value={text}
            onChange={(event) => setText(event.target.value)}
            placeholder="例如：法务说周四上午发修订版。"
          />
        </label>
        <button
          type="button"
          className="btn btn--primary"
          disabled={!text.trim()}
          onClick={() => {
            if (!text.trim()) return;
            onSubmit(text);
            onClose();
          }}
        >
          保存进展到预览
        </button>
        <button type="button" className="btn btn--quiet" onClick={onClose}>
          取消
        </button>
      </div>
    </Sheet>
  );
}

export function MatterEditSheet({
  matter,
  open,
  onClose,
  onSave,
}: {
  matter: WorkMatter;
  open: boolean;
  onClose: () => void;
  onSave: (patch: { title: string; goal: string; nextAction: string }) => void;
}) {
  const [title, setTitle] = useState(matter.title);
  const [goal, setGoal] = useState(matter.goal);
  const [nextAction, setNextAction] = useState(matter.nextAction);
  useEffect(() => {
    if (open) {
      setTitle(matter.title);
      setGoal(matter.goal);
      setNextAction(matter.nextAction);
    }
  }, [matter, open]);
  return (
    <Sheet title="纠正事项内容" open={open} onClose={onClose}>
      <div className="m-stack-12">
        <label className="field">
          <span className="field__label">事项标题</span>
          <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">目标与背景</span>
          <textarea className="input" rows={3} value={goal} onChange={(event) => setGoal(event.target.value)} />
        </label>
        <label className="field">
          <span className="field__label">下一步动作</span>
          <input className="input" value={nextAction} onChange={(event) => setNextAction(event.target.value)} />
        </label>
        <button
          type="button"
          className="btn btn--primary"
          disabled={!title.trim()}
          onClick={() => {
            if (!title.trim()) return;
            onSave({ title: title.trim(), goal: goal.trim(), nextAction: nextAction.trim() });
            onClose();
          }}
        >
          保存到本机预览
        </button>
        <button type="button" className="btn btn--quiet" onClick={onClose}>
          取消
        </button>
      </div>
    </Sheet>
  );
}

function toLocalInput(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

export function ScheduleEditSheet({
  schedule,
  open,
  onClose,
  onSave,
  heading = "修改预览日程",
  submitLabel = "保存到本机预览",
  note = "保存只改本机预览；涉及正式日程或提醒时，仍需服务器确认后才生效。这里不会显示“已写入 iPhone”。",
}: {
  schedule: WorkSchedule | null;
  open: boolean;
  onClose: () => void;
  onSave: (patch: Pick<WorkSchedule, "title" | "startsAt" | "endsAt" | "location">) => void;
  heading?: string;
  submitLabel?: string;
  note?: string;
}) {
  const [title, setTitle] = useState("");
  const [startsAt, setStartsAt] = useState("");
  const [endsAt, setEndsAt] = useState("");
  const [location, setLocation] = useState("");
  useEffect(() => {
    if (open && schedule) {
      setTitle(schedule.title);
      setStartsAt(toLocalInput(schedule.startsAt));
      setEndsAt(toLocalInput(schedule.endsAt));
      setLocation(schedule.location);
    }
  }, [open, schedule]);
  return (
    <Sheet title={heading} open={open} onClose={onClose}>
      <div className="m-stack-12">
        <label className="field">
          <span className="field__label">标题</span>
          <input className="input" value={title} onChange={(event) => setTitle(event.target.value)} />
        </label>
        <div className="grid-2">
          <label className="field">
            <span className="field__label">开始</span>
            <input
              className="input"
              type="datetime-local"
              value={startsAt}
              onChange={(event) => setStartsAt(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field__label">结束</span>
            <input
              className="input"
              type="datetime-local"
              value={endsAt}
              onChange={(event) => setEndsAt(event.target.value)}
            />
          </label>
        </div>
        <label className="field">
          <span className="field__label">地点</span>
          <input className="input" value={location} onChange={(event) => setLocation(event.target.value)} />
        </label>
        <p className="micro" style={{ margin: 0 }}>{note}</p>
        <button
          type="button"
          className="btn btn--primary"
          disabled={!title.trim() || !startsAt || !endsAt}
          onClick={() => {
            const start = new Date(startsAt);
            const end = new Date(endsAt);
            if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) return;
            onSave({
              title: title.trim(),
              startsAt: start.toISOString(),
              endsAt: end.toISOString(),
              location: location.trim(),
            });
            onClose();
          }}
        >
          {submitLabel}
        </button>
        <button type="button" className="btn btn--quiet" onClick={onClose}>
          取消
        </button>
      </div>
    </Sheet>
  );
}

export function ConfirmSheet({
  open,
  title,
  body,
  confirmLabel,
  onClose,
  onConfirm,
}: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Sheet title={title} open={open} onClose={onClose}>
      <div className="m-stack-12">
        <p className="caption" style={{ margin: 0 }}>{body}</p>
        <button type="button" className="btn btn--danger" onClick={onConfirm}>
          {confirmLabel}
        </button>
        <button type="button" className="btn btn--quiet" onClick={onClose}>
          取消
        </button>
      </div>
    </Sheet>
  );
}

export function ScheduleRow({
  schedule,
  onEdit,
}: {
  schedule: WorkSchedule;
  onEdit: (schedule: WorkSchedule) => void;
}) {
  return (
    <div className="timeline-row">
      <span className="timeline-time">
        {new Date(schedule.startsAt).toLocaleTimeString("zh-CN", {
          hour: "2-digit",
          minute: "2-digit",
          hour12: false,
        })}
      </span>
      <span className="timeline-dot" aria-hidden="true" />
      <span className="grow">
        <span className="card-title" style={{ display: "block", fontSize: 15 }}>
          {schedule.title}
        </span>
        <span className="caption" style={{ display: "block", margin: "2px 0 0" }}>
          {schedule.location || "地点未填"} · {SYNC_STATUS_LABEL[schedule.syncStatus]}
        </span>
      </span>
      <button type="button" className="icon-button" onClick={() => onEdit(schedule)} aria-label={`修改 ${schedule.title}`}>
        <Pencil size={14} />
      </button>
    </div>
  );
}

export function ScheduleMeta({ schedule }: { schedule: WorkSchedule }) {
  return (
    <span className="micro">
      <Clock3 size={12} /> {dateTimeLabel(schedule.startsAt)} · <MapPin size={12} /> {schedule.location || "地点未填"}
    </span>
  );
}
