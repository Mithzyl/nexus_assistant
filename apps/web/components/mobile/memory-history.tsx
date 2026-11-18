"use client";
import { useEffect, useState } from "react";
import { apiRequest, pa } from "@/lib/mobile/api";
import { MemoryAttribution, type MemorySource } from "./memory-attribution";
import { Sheet } from "./ui";

interface History {
  current_version: number;
  items: { version: number; status: string; text?: string; source?: MemorySource; created_at?: string }[];
  next_offset: number | null;
}

/** Read immutable revisions in a focused sheet with current/past content and original authors distinguished. */
export function MemoryHistory({ entryId, onClose }: { entryId: string; onClose: () => void }) {
  const [data, setData] = useState<History | null>(null);
  const [offset, setOffset] = useState(0), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let alive = true;
    setBusy(true); setError(""); setData(null);
    apiRequest<History>(pa(`/memory/history/${encodeURIComponent(entryId)}?offset=${offset}`))
      .then(value => { if (alive) setData(value); })
      .catch(caught => { if (alive) setError(caught instanceof Error ? caught.message : "历史读取失败"); })
      .finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [entryId, offset, refresh]);
  return <Sheet title="记忆版本历史" open stickyHeader onClose={onClose}>
    <div className="m-stack-14">
      <p className="caption">历史只供查看；纠正会保留原文和已有版本。</p>
      {error ? <div role="alert"><p>{error}</p><button type="button" onClick={() => setRefresh(value => value + 1)}>重新读取历史</button></div>
        : busy ? <p role="status">正在读取历史…</p> : data ? <>
          {!data.items.length ? <p>当前页没有历史记录。</p> : data.items.map(item => <article key={item.version} className="card m-stack-10">
            <h3 className="card-title">{item.version === data.current_version ? "当前内容" : "修改前内容"} · 版本 {item.version}</h3>
            <p className="caption">{({ active: "有效", forgotten: "已忘记", candidate: "待确认" } as Record<string, string>)[item.status] ?? "状态未记录"}</p>
            <p style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{item.text ?? "此版本没有可展示正文"}</p>
            <MemoryAttribution source={item.source} text={item.text} recordedAt={item.created_at} />
          </article>)}
          <div className="flex gap-10">
            <button type="button" disabled={busy || !offset} onClick={() => setOffset(Math.max(0, offset - 20))}>上一页</button>
            <button type="button" disabled={busy || data.next_offset === null} onClick={() => setOffset(data.next_offset!)}>下一页</button>
          </div>
        </> : null}
      <button type="button" className="btn btn--secondary" onClick={onClose}>关闭历史</button>
    </div>
  </Sheet>;
}
