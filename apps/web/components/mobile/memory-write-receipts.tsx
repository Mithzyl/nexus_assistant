"use client";

import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";

import { apiRequest, pa, ApiError } from "@/lib/mobile/api";
import { MemoryHistory } from "./memory-history";

interface Receipt {
  tool_call_id: string;
  entry_id: string;
  applied_version: number;
  current_version: number;
  status: string;
  can_undo: boolean;
}

interface WritePage {
  items: Receipt[];
}

/** Show a memory change only when a durable receipt exists. */
export function MemoryWriteReceipts({ runId }: { runId: string }) {
  const [rows, setRows] = useState<Receipt[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState<string | null>(null);
  const lock = useRef(false);
  const operation = useRef<{ signature: string; key: string } | null>(null);

  const load = async () => {
    const page = await apiRequest<WritePage>(
      pa(`/memory/writes/${encodeURIComponent(runId)}`),
    );
    setRows(page.items);
  };

  useEffect(() => {
    let alive = true;
    apiRequest<WritePage>(pa(`/memory/writes/${encodeURIComponent(runId)}`))
      .then((page) => {
        if (alive) setRows(page.items);
      })
      .catch(() => {
        if (alive) setError("记忆状态暂时无法读取。");
      });
    return () => {
      alive = false;
    };
  }, [runId]);

  const undo = async (row: Receipt) => {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    const signature = `${row.entry_id}:${row.applied_version}`;
    if (operation.current?.signature !== signature) {
      operation.current = { signature, key: crypto.randomUUID() };
    }
    try {
      await apiRequest(pa("/memory/commands"), {
        method: "POST",
        body: {
          action: "undo",
          entry_id: row.entry_id,
          expected_version: row.applied_version,
          key: operation.current.key,
        },
      });
      operation.current = null;
      setNotice("已撤销这次记忆改动；聊天和事项不受影响。");
      await load();
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        operation.current = null;
        await load().catch(() => {});
      } else {
        setError("这次撤销没有完成，可以再试一次。");
      }
    } finally {
      lock.current = false;
      setBusy(false);
    }
  };

  if (rows.length === 0 && !notice) return null;

  const undoable = rows.find(
    (row) => row.can_undo && row.applied_version === row.current_version,
  );
  const activeCount = rows.filter((row) => row.status === "active").length;
  const candidateCount = rows.filter((row) => row.status === "candidate").length;
  const forgottenCount = rows.filter((row) => row.status === "forgotten").length;
  const summary =
    activeCount > 0
      ? `已记住 ${activeCount} 条内容`
      : candidateCount > 0
        ? `有 ${candidateCount} 条记忆待确认`
        : forgottenCount > 0
          ? `已忘记 ${forgottenCount} 条内容`
          : `已更新 ${rows.length} 条记忆`;

  return (
    <div className="ask-memory">
      <span className="ask-memory__summary">
        <Sparkles size={12} strokeWidth={1.5} aria-hidden="true" />
        {summary}
      </span>
      {undoable ? (
        <button type="button" className="ask-memory__link" disabled={busy} onClick={() => void undo(undoable)}>
          {busy ? "撤销中…" : "撤销"}
        </button>
      ) : null}
      {rows[0] ? (
        <button type="button" className="ask-memory__link" onClick={() => setHistory(rows[0].entry_id)}>
          查看
        </button>
      ) : null}
      {notice ? <span className="ask-memory__notice">{notice}</span> : null}
      {error ? (
        <>
          <span className="ask-memory__notice">{error}</span>
          <button type="button" className="ask-memory__link" onClick={() => void load().catch(() => setError("记忆状态暂时无法读取。"))}>
            重试
          </button>
        </>
      ) : null}
      {history ? <MemoryHistory entryId={history} onClose={() => setHistory(null)} /> : null}
    </div>
  );
}
