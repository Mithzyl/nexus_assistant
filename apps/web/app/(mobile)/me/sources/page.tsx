"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, CalendarDays, FileText, NotebookPen } from "lucide-react";

import type { ContentItem, DataSource } from "@/lib/mobile/api";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { formatDateTime } from "@/lib/mobile/format";
import { useAuth } from "@/components/mobile/providers";
import {
  EmptyState,
  ErrorCard,
  LoadingCard,
  PageShell,
  Pill,
} from "@/components/mobile/ui";

const TYPE_LABEL: Record<DataSource["source_type"], string> = {
  calendar: "日历",
  reminders: "提醒事项",
  notes: "笔记",
};

const KIND_LABEL: Record<ContentItem["kind"], string> = {
  calendar_event: "日历",
  reminder: "提醒",
  note: "笔记",
};

function sourceIcon(type: DataSource["source_type"]) {
  if (type === "calendar") return <CalendarDays size={15} />;
  if (type === "reminders") return <Archive size={15} />;
  return <NotebookPen size={15} />;
}

export default function SourcesPage() {
  const { state } = useAuth();
  const router = useRouter();
  const signedIn = state === "signedIn";
  const [tab, setTab] = useState<"sources" | "content">("sources");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [kind, setKind] = useState<"" | ContentItem["kind"]>("");

  const sources = useApiResource<DataSource[]>(
    signedIn ? "/api/pa/data-sources" : null,
    { enabled: signedIn },
  );

  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(query.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [query]);

  const contentPath = (() => {
    const params = new URLSearchParams({ limit: "50" });
    if (debounced) params.set("query", debounced);
    if (kind) params.set("kind", kind);
    return `/api/pa/content?${params.toString()}`;
  })();
  const content = useApiResource<{ items: ContentItem[]; next_cursor: string | null }>(
    signedIn && tab === "content" ? contentPath : null,
    { enabled: signedIn && tab === "content" },
  );

  return (
    <div style={{ paddingBottom: 24 }}>
      <PageShell
        title="内容来源"
        subtitle="只读取已同步到服务器的副本；浏览器不能读取系统日历或提醒事项。"
        action={
          <button
            type="button"
            className="page-bar__action"
            onClick={() => router.push("/me/sources/import")}
          >
            导入
          </button>
        }
      >
        {!signedIn && state !== "loading" ? (
          <EmptyState
            title="登录后才能查看内容来源"
            body="来源、同步状态和搜索都需要服务器上的账号。"
          >
            <Link href="/login" className="btn btn--primary">
              登录 PA 账号
            </Link>
          </EmptyState>
        ) : null}

        {signedIn ? (
          <>
            <div className="chip-row" role="tablist" aria-label="内容视图">
              <button
                type="button"
                role="tab"
                className="chip"
                aria-pressed={tab === "sources"}
                onClick={() => setTab("sources")}
              >
                来源
              </button>
              <button
                type="button"
                role="tab"
                className="chip"
                aria-pressed={tab === "content"}
                onClick={() => setTab("content")}
              >
                云端内容
              </button>
            </div>

            {tab === "sources" ? (
              <div className="m-stack-10 mt-14">
                {sources.loading ? <LoadingCard label="正在读取内容来源…" /> : null}
                {sources.error ? (
                  <ErrorCard message={errorText(sources.error)} onRetry={sources.reload} />
                ) : null}
                {!sources.loading && !sources.error && sources.data?.length === 0 ? (
                  <EmptyState
                    title="还没有内容来源"
                    body="iPhone 上的日历、提醒事项和笔记只有在你主动选择并上传后才会出现在这里。浏览器只能读取已有副本。"
                  >
                    <Link href="/me/sources/import" className="btn btn--secondary">
                      导入一条文本笔记
                    </Link>
                  </EmptyState>
                ) : null}
                {(sources.data ?? []).map((source) => (
                  <button
                    key={source.source_id}
                    type="button"
                    className="card"
                    style={{ width: "100%", textAlign: "left" }}
                    onClick={() => router.push(`/me/sources/${source.source_id}`)}
                  >
                    <span className="card-head">
                      <span className="tile tile--sm">{sourceIcon(source.source_type)}</span>
                      <span className="grow">
                        <span className="card-title" style={{ display: "block", fontSize: 16 }}>
                          {source.display_name}
                        </span>
                        <span className="caption" style={{ display: "block", marginTop: 2 }}>
                          {TYPE_LABEL[source.source_type]} · {source.item_count} 条副本
                          {source.last_sync_at
                            ? ` · 最近同步 ${formatDateTime(source.last_sync_at)}`
                            : " · 还没有同步记录"}
                        </span>
                      </span>
                      <span className="chevron">›</span>
                    </span>
                    <span className="flex items-center gap-8 mt-10" style={{ flexWrap: "wrap" }}>
                      <Pill tone={source.sync_enabled ? "fog" : "amber"}>
                        {source.sync_enabled ? "继续上传" : "已停止上传"}
                      </Pill>
                      <Pill tone={source.assistant_enabled ? "green" : "neutral"}>
                        {source.assistant_enabled ? "助手可读" : "助手不可读"}
                      </Pill>
                      <span className="grow" />
                      <span className="micro">{source.upload_fields.length} 个字段</span>
                    </span>
                    {source.last_error_code ? (
                      <span className="micro red" style={{ display: "block", marginTop: 8 }}>
                        最近一次同步有错误：{source.last_error_code}
                      </span>
                    ) : null}
                  </button>
                ))}
                {signedIn && (sources.data?.length ?? 0) > 0 ? (
                  <p className="micro" style={{ lineHeight: 1.6 }}>
                    停止同步只停止后续上传，不删除已有副本；助手授权只决定云端助手能否读取，不影响你在这里查看。
                  </p>
                ) : null}
              </div>
            ) : (
              <div className="mt-14">
                <label className="field" htmlFor="content-query">
                  <span className="field__label">搜索云端内容</span>
                  <input
                    id="content-query"
                    className="input"
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="按标题或正文搜索"
                  />
                </label>
                <div className="chip-row" style={{ marginBottom: 12 }}>
                  {(
                    [
                      ["", "全部"],
                      ["calendar_event", "日历"],
                      ["reminder", "提醒"],
                      ["note", "笔记"],
                    ] as Array<["" | ContentItem["kind"], string]>
                  ).map(([value, label]) => (
                    <button
                      key={value || "all"}
                      type="button"
                      className="chip chip--sm"
                      aria-pressed={kind === value}
                      onClick={() => setKind(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>

                {content.loading ? <LoadingCard label="正在读取云端内容…" /> : null}
                {content.error ? (
                  <ErrorCard message={errorText(content.error)} onRetry={content.reload} />
                ) : null}
                {!content.loading && !content.error && content.data?.items.length === 0 ? (
                  <EmptyState
                    title={debounced ? "没有匹配的内容" : "还没有已同步的内容"}
                    body={
                      debounced
                        ? "换一个关键词，或切换类型再试。"
                        : "上传副本后可以在这里搜索和查看详情。"
                    }
                  />
                ) : null}
                <div className="list-card">
                  {(content.data?.items ?? []).map((item, index) => (
                    <div key={item.item_id}>
                      {index > 0 ? <div className="row-sep" /> : null}
                      <button
                        type="button"
                        className="row"
                        onClick={() => router.push(`/me/sources/content/${item.item_id}`)}
                      >
                        <span className="tile tile--sm">
                          {item.kind === "note" ? <FileText size={15} /> : <CalendarDays size={15} />}
                        </span>
                        <span className="row__body">
                          <span className="row__title" style={{ display: "block", fontSize: 15 }}>
                            {item.title || "（无标题）"}
                          </span>
                          <span className="row__detail" style={{ display: "block" }}>
                            {KIND_LABEL[item.kind]} · {item.source_display_name} ·{" "}
                            {item.starts_at
                              ? formatDateTime(item.starts_at)
                              : item.due_at
                                ? `到期 ${formatDateTime(item.due_at)}`
                                : formatDateTime(item.source_updated_at)}
                          </span>
                        </span>
                        <span className="chevron">›</span>
                      </button>
                    </div>
                  ))}
                </div>
                <p className="micro mt-12" style={{ lineHeight: 1.6 }}>
                  搜索只覆盖当前账号、当前有效范围、未删除且字段仍获授权的副本。未出现的结果不代表被删除。
                </p>
              </div>
            )}
          </>
        ) : null}
      </PageShell>
    </div>
  );
}
