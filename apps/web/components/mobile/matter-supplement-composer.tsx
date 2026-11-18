"use client";

/**
 * Save one independent matter supplement before the assistant analyzes it.
 *
 * The textarea keeps the user's exact words. A failed request keeps both the
 * text and its idempotency key so a retry cannot create a second record; a new
 * submission starts a new key. Corrections and answers use a separate sheet
 * because they must carry a target and an explicit relation type.
 */

import { useRef, useState } from "react";

import type { MatterSupplement } from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import { newOperationKey, saveMatterSupplement } from "@/lib/mobile/matters-api";

const MAX_LENGTH = 4000;

/** Turn known save errors into instructions the user can act on. */
function saveErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "matter_changed") return "事项刚有变化，已经刷新，再保存一次。";
    if (error.code === "idempotency_conflict") return "这次和上次的内容对不上，请重新提交。";
    if (error.code === "matter_not_active") return "这个事项现在不能补充。";
    if (error.status === 404) return "这个事项不存在，或你没有权限。";
    if (error.status === 422) return "内容不符合要求，检查一下再试。";
    return error.message || "这次没保存上，再试一次。";
  }
  return "这次没保存上，再试一次。";
}

export function MatterSupplementComposer({
  matterId,
  onSaved,
  onMatterChanged,
}: {
  matterId: string;
  onSaved: (supplement: MatterSupplement) => void;
  /** Reload the matter after a concurrent edit so the next save uses its version. */
  onMatterChanged?: () => void;
}) {
  const [text, setText] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pending = useRef<{ signature: string; key: string } | null>(null);

  /** Save once; identical retries reuse the key, edited text gets a new one. */
  const submit = async () => {
    // Keep the user's exact words; only the empty check looks at whitespace.
    const value = text;
    if (!value.trim()) {
      setError("先写一点内容。");
      return;
    }
    if (value.length > MAX_LENGTH) {
      setError(`最多 ${MAX_LENGTH} 个字。`);
      return;
    }
    if (!pending.current || pending.current.signature !== value) {
      pending.current = { signature: value, key: newOperationKey("web-supplement") };
    }
    setSaving(true);
    setError(null);
    try {
      const supplement = await saveMatterSupplement(matterId, {
        text: value,
        idempotency_key: pending.current.key,
      });
      if (!supplement.saved) {
        setError("这次还没保存上，再试一次。");
        return;
      }
      pending.current = null;
      setText("");
      onSaved(supplement);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "matter_changed") onMatterChanged?.();
      setError(saveErrorText(caught));
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="card" aria-label="补充内容">
      <h2 className="section-title" style={{ fontSize: 17, margin: 0 }}>
        补充内容
      </h2>
      <p className="micro" style={{ margin: "4px 0 0" }}>
        说明、备注或进展都可以记在这里。
      </p>

      <textarea
        className="textarea"
        rows={3}
        value={text}
        maxLength={MAX_LENGTH}
        placeholder="记一条说明、备注或进展"
        onChange={(event) => setText(event.target.value)}
        style={{ marginTop: 10 }}
      />

      <div className="supplement-composer-foot">
        <span className="micro">{text.length} / {MAX_LENGTH}</span>
        <button
          type="button"
          className="btn btn--primary btn--small"
          onClick={() => void submit()}
          disabled={saving}
        >
          {saving ? "正在保存…" : "保存补充"}
        </button>
      </div>
      {error ? (
        <p className="danger-text" style={{ margin: "8px 0 0" }} role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
