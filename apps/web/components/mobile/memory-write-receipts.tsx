"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { ApiError, apiRequest, pa } from "@/lib/mobile/api";
import { newOperationKey } from "@/lib/mobile/matters-api";
import { MemoryHistory } from "./memory-history";

interface MemoryOperationReceipt {
  operation_id: string;
  status: "awaiting_answer" | "ready" | "succeeded" | "failed" | "cancelled";
  result: { entry_id?: string; applied_version?: number; status?: string } | null;
  can_undo?: boolean;
}

const statusText: Record<MemoryOperationReceipt["status"], string> = {
  awaiting_answer: "记忆改动待处理",
  ready: "记忆改动待处理",
  succeeded: "已更新记忆",
  failed: "记忆改动未完成",
  cancelled: "已取消记忆改动",
};

/** Read a receipt the backend associated with this chat and track any queued write. */
export function MemoryWriteReceipts({ operationId }: { operationId: string }) {
  const [receipt, setReceipt] = useState<MemoryOperationReceipt | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<string | null>(null);
  const undoKey = useRef<string | null>(null);
  const undoing = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;

    /** Stop polling at a final result or after five deferred checks. */
    const load = async (remaining: number) => {
      try {
        const next = await apiRequest<MemoryOperationReceipt>(
          pa(`/memory/operations/${encodeURIComponent(operationId)}`),
          { signal: controller.signal },
        );
        if (controller.signal.aborted) return;
        setReceipt(next);
        if (remaining > 0 && (next.status === "awaiting_answer" || next.status === "ready")) {
          timer = setTimeout(() => void load(remaining - 1), 2000);
        }
      } catch {
        if (!controller.signal.aborted) setError("暂时无法读取记忆处理状态。");
      }
    };
    setError("");
    void load(5);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [operationId, refresh]);

  /** Undo the exact applied revision. Assistant checks it again before saving. */
  const undo = async () => {
    const entryId = receipt?.result?.entry_id;
    const version = receipt?.result?.applied_version;
    if (!entryId || !version || !receipt?.can_undo || undoing.current) return;
    undoing.current = true;
    setBusy(true);
    setError("");
    undoKey.current ??= newOperationKey("memory-undo");
    try {
      await apiRequest(pa("/memory/commands"), {
        method: "POST",
        body: {
          action: "undo",
          entry_id: entryId,
          expected_version: version,
          key: undoKey.current,
        },
      });
      setNotice("已撤销这次记忆改动。");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setReceipt({ ...receipt, can_undo: false });
        setError("记忆已有变化，请查看当前版本。");
      } else {
        setError("这次撤销没有完成，可以再试一次。");
      }
    } finally {
      undoing.current = false;
      setBusy(false);
    }
  };

  const entryId = receipt?.status === "succeeded" ? receipt.result?.entry_id : undefined;
  const pending = receipt?.status === "awaiting_answer" || receipt?.status === "ready";
  const summary = receipt?.status === "succeeded" && receipt.result?.status === "candidate"
    ? "记忆待确认"
    : receipt ? statusText[receipt.status] : "正在查询记忆处理状态";

  return (
    <div className="ask-memory">
      <span className="ask-memory__summary">
        <Sparkles size={12} strokeWidth={1.5} aria-hidden="true" />
        {notice || (error && !receipt ? error : summary)}
      </span>
      {receipt?.can_undo && !notice ? (
        <button type="button" className="ask-memory__link" disabled={busy} onClick={() => void undo()}>
          {busy ? "撤销中…" : "撤销"}
        </button>
      ) : null}
      {entryId ? (
        <button type="button" className="ask-memory__link" onClick={() => setHistory(entryId)}>查看</button>
      ) : null}
      {error && receipt ? <span className="ask-memory__notice">{error}</span> : null}
      {pending || error ? (
        <button type="button" className="ask-memory__link" onClick={() => setRefresh(value => value + 1)}>
          刷新状态
        </button>
      ) : null}
      {history ? <MemoryHistory entryId={history} onClose={() => setHistory(null)} /> : null}
    </div>
  );
}
