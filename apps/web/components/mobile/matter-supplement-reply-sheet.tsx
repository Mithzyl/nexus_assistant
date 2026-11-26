"use client";

/**
 * Record one user correction or answer against an existing supplement text.
 *
 * The sheet sends both `parent_text_id` and `relation_type`; a bare parent link
 * is the legacy behavior and would not count as a correction or answer.
 * Nothing here changes schedules or todos.
 */

import { useEffect, useRef, useState } from "react";

import type { MatterSupplement, SupplementRelationType } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { newOperationKey, saveMatterSupplement } from "@/lib/mobile/matters-api";
import { Sheet } from "./ui";

const MAX_LENGTH = 4000;

/** The exact text a new save corrects or answers, plus the action to record. */
export interface SupplementReplyTarget {
  textId: string;
  supplementId: string;
  preview: string;
  /** Human label of the original text, such as 助手摘要 or 助手追问. */
  sourceLabel: string;
  relationType: SupplementRelationType;
}

function replyErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "invalid_answer_target") return "这条回答对不上原来的问题，先刷新再试。";
    if (error.code === "idempotency_conflict") return "这次和上次的内容对不上，请重新提交。";
    if (error.code === "matter_not_active") return "这个事项现在不能补充。";
    if (error.status === 404) return "原来的那条内容找不到了，先刷新再试。";
    if (error.status === 422) return "内容不符合要求，检查一下再试。";
    return error.message || "这次没保存上，再试一次。";
  }
  return "这次没保存上，再试一次。";
}

export function MatterSupplementReplySheet({
  open,
  matterId,
  target,
  onClose,
  onSaved,
}: {
  open: boolean;
  matterId: string;
  target: SupplementReplyTarget | null;
  onClose: () => void;
  onSaved: (supplement: MatterSupplement, target: SupplementReplyTarget) => void;
}) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);

  useEffect(() => {
    if (!open) return;
    setText("");
    setError(null);
    pending.current = null;
  }, [open, target?.textId, target?.relationType]);

  const isAnswer = target?.relationType === "answer";
  const title = isAnswer ? "回复助手" : "纠正这段内容";

  /** Save once; a failed request keeps the text and key for a safe retry. */
  const submit = async () => {
    if (!target) return;
    const value = text;
    if (!value.trim()) {
      setError("先写一点内容。");
      return;
    }
    if (value.length > MAX_LENGTH) {
      setError(`最多 ${MAX_LENGTH} 个字。`);
      return;
    }
    const signature = `${target.textId}\u0000${target.relationType}\u0000${value}`;
    if (!pending.current || pending.current.signature !== signature) {
      pending.current = { signature, key: newOperationKey("web-supplement-reply") };
    }
    setSaving(true);
    setError(null);
    try {
      const supplement = await saveMatterSupplement(matterId, {
        text: value,
        idempotency_key: pending.current.key,
        parent_text_id: target.textId,
        relation_type: target.relationType,
      });
      if (!supplement.saved) {
        setError("这次还没保存上，再试一次。");
        return;
      }
      pending.current = null;
      onSaved(supplement, target);
      onClose();
    } catch (caught) {
      setError(replyErrorText(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Sheet title={title} open={open} onClose={onClose}>
      {target ? (
        <div className="m-stack-12">
          <div className="card card--tight supplement-reply-context">
            <span className="micro">{target.sourceLabel}</span>
            <p className="body-text" style={{ margin: "4px 0 0" }}>
              {target.preview}
            </p>
          </div>

          <label className="field">
            <span className="field__label">{isAnswer ? "你的回答" : "你的纠正"}</span>
            <textarea
              className="textarea"
              rows={4}
              value={text}
              maxLength={MAX_LENGTH}
              placeholder={isAnswer ? "写下你的回答" : "写下你的纠正"}
              onChange={(event) => setText(event.target.value)}
            />
          </label>

          <div className="row">
            <span className="micro">{text.length} / {MAX_LENGTH}</span>
          </div>

          {error ? (
            <p className="danger-text" role="alert" style={{ margin: 0 }}>
              {error}
            </p>
          ) : null}

          <button
            type="button"
            className="btn btn--primary"
            disabled={saving || !text.trim()}
            onClick={() => void submit()}
          >
            {saving ? "正在保存…" : isAnswer ? "保存回答" : "保存纠正"}
          </button>
        </div>
      ) : null}
    </Sheet>
  );
}
