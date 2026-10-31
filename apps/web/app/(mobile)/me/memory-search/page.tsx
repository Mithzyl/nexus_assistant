"use client";

import { useState } from "react";
import Link from "next/link";
import { Search } from "lucide-react";

import type { MemoryResults } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { uuid } from "@/lib/mobile/format";
import { useAuth } from "@/components/mobile/providers";
import { EmptyState, Notice, PageShell, Pill } from "@/components/mobile/ui";
import { PrimaryButton } from "@/components/mobile/buttons";

export default function MemorySearchPage() {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<MemoryResults | null>(null);

  const search = async (offset = 0) => {
    const trimmed = query.trim();
    if (!trimmed) {
      setError("先写一句要检索的内容。");
      return;
    }
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const data = await apiRequest<MemoryResults>("/api/pa/memory/search", {
        method: "POST",
        body: {
          query_text: trimmed,
          memory_types: [],
          limit: 10,
          idempotency_key: uuid(),
          offset,
        },
      });
      setResults(data);
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(
          caught.status === 401
            ? "登录状态已失效，请重新登录。"
            : caught.status >= 500
              ? "上游记忆服务不可用。这不等于“没有记录”，请稍后再试。"
              : caught.message,
        );
      } else {
        setError("请求未能完成。");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell
      title="记忆检索"
      subtitle="检索当前账号下的个人记忆，待确认内容会单独标记。"
    >
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后检索个人记忆" body="只检索当前账号下的个人记忆。">
          <Link href="/login" className="btn btn--primary">登录</Link>
        </EmptyState>
      ) : null}

      {signedIn ? (
        <>
          <label className="field" htmlFor="memory-query">
            <span className="field__label">检索内容</span>
            <input
              id="memory-query"
              className="input"
              value={query}
              maxLength={2000}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="例如：江边散步时要注意什么"
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.nativeEvent.isComposing) {
                  event.preventDefault();
                  void search();
                }
              }}
            />
          </label>
          <PrimaryButton onClick={() => void search()} busy={busy} disabled={busy}>
            <Search size={14} /> {busy ? "正在检索…" : "检索"}
          </PrimaryButton>

          {error ? (
            <div className="card card--red mt-14">
              <p className="card-title red" style={{ fontSize: 15 }}>检索没有完成</p>
              <p className="caption red" style={{ margin: "4px 0 0" }}>{error}</p>
            </div>
          ) : null}

          {results ? (
            results.items.length === 0 ? (
              <EmptyState
                title="上游返回了空结果"
                body="空结果只表示这次检索没有命中；不能据此推断记忆被删除或从未存在。"
              />
            ) : (
              <div className="m-stack-10 mt-14">
                {results.items.map((hit) => (
                  <div className="card" key={hit.memory_id}>
                    <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
                      <Pill tone="fog">{hit.memory_type || "记忆"}</Pill>
                      <Pill tone="fog">{hit.status === "active" ? "已生效" : hit.status === "candidate" ? "待确认" : hit.status || "状态未知"}</Pill>
                      {typeof hit.score === "number" ? (
                        <span className="micro">相关度 {hit.score.toFixed(2)}</span>
                      ) : null}
                    </div>
                    <p className="body-text mt-8" style={{ marginBottom: 0 }}>
                      {hit.content}
                    </p>
                    {hit.source_reference ? (
                      <p className="micro" style={{ margin: "8px 0 0" }}>
                        来源：{hit.source_reference}
                      </p>
                    ) : null}
                    {hit.app_data ? (
                      <details className="mt-8"><summary>查看保存的数据</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(hit.app_data.fields, null, 2)}</pre></details>
                    ) : null}
                  </div>
                ))}
              </div>
            )
          ) : null}

          {results?.next_cursor ? <PrimaryButton onClick={() => void search(Number(results.next_cursor))} disabled={busy}>继续检索下一页</PrimaryButton> : null}
          <Notice title="关于检索">
            按关键词匹配记忆正文；待确认内容在你确认后，才会参与后续建议。
          </Notice>
          <Link href="/me/memories" className="link-button mt-6">
            查看个人记忆
          </Link>
        </>
      ) : null}
    </PageShell>
  );
}
