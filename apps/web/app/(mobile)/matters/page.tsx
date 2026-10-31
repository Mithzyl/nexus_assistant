"use client";

import Link from "next/link";
import { ArrowDownRight, Plus, RefreshCw } from "lucide-react";

import { useAuth, useUI } from "@/components/mobile/providers";
import { EmptyState, ErrorCard, LoadingCard, LoginRequired, ScreenHeader } from "@/components/mobile/ui";
import { useMatters } from "@/lib/mobile/use-matters";
import type { MatterSummary } from "@/lib/mobile/api";

function MatterCard({ matter }: { matter: MatterSummary }) {
  return (
    <Link href={`/matters/${matter.matter_id}`} className="card matter-card">
      <span className="card-title" style={{ display: "block" }}>{matter.title}</span>
      <span className="matter-card__progress">
        {matter.latest_progress || "还没有最新进展。"}
      </span>
      <span className="matter-card__next">
        <ArrowDownRight size={13} />
        <span>{matter.next_step || "下一步待定"}</span>
        {matter.open_todos > 0 ? <span className="matter-card__time">{matter.open_todos} 项待办</span> : null}
      </span>
    </Link>
  );
}

export default function MattersPage() {
  const { state, user } = useAuth();
  const { openCapture } = useUI();
  const signedIn = state === "signedIn";
  const matters = useMatters(signedIn, user?.user_id ?? null);

  const groups: Array<{ status: "active" | "waiting" | "archived"; label: string }> = [
    { status: "active", label: "正在推进" },
    { status: "waiting", label: "等待外部反馈" },
    { status: "archived", label: "已完成／归档" },
  ];

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 28 }}>
      <ScreenHeader
        title="事项"
        subtitle="把长期的事拆成下一步"
        action={
          <div className="flex items-center gap-6">
            <button
              type="button"
              className="icon-button"
              onClick={matters.refresh}
              disabled={matters.loading}
              aria-label="刷新事项"
              title="刷新事项"
            >
              <RefreshCw size={17} strokeWidth={1.5} />
            </button>
            <button
              type="button"
              className="icon-button"
              onClick={() => openCapture("choose")}
              aria-label="新建事项"
              title="新建事项"
            >
              <Plus size={18} strokeWidth={1.5} />
            </button>
          </div>
        }
      />

      {!signedIn ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoginRequired
            title="登录后同步事项"
            body="登录账号后，事项、日程和提醒会同步到这里，方便你持续跟进。"
          />
        </div>
      ) : matters.loading && matters.items.length === 0 ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在同步事项…" />
        </div>
      ) : matters.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={matters.error.message} onRetry={matters.refresh} />
        </div>
      ) : matters.items.length === 0 ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="还没有事项"
            body="把要做的事交给助手，之后可以在这里持续跟进。"
          >
            <div className="m-stack-8">
              <Link href="/ask" className="btn btn--primary">交给助手整理</Link>
              <button type="button" className="btn btn--quiet" onClick={() => openCapture("choose")}>
                新建一条事项
              </button>
            </div>
          </EmptyState>
        </div>
      ) : (
        <>
          {groups.map((group) => {
            const rows = matters.items.filter((matter) => matter.status === group.status);
            return (
              <section key={group.status} className="stack-12">
                <div className="flex items-center justify-between gap-12" style={{ padding: "0 var(--m-gutter)" }}>
                  <h2 className="section-title" style={{ marginBottom: 0 }}>{group.label}</h2>
                  <span className="micro">{rows.length}</span>
                </div>
                {rows.length === 0 ? (
                  <p className="micro" style={{ padding: "0 var(--m-gutter)", margin: "4px 0 0" }}>这一组暂无事项。</p>
                ) : (
                  <div className="m-stack-10" style={{ padding: "0 var(--m-gutter)" }}>
                    {rows.map((matter) => <MatterCard key={matter.matter_id} matter={matter} />)}
                  </div>
                )}
              </section>
            );
          })}
          {matters.nextCursor ? (
            <div style={{ padding: "0 var(--m-gutter)" }}>
              <button type="button" className="btn btn--secondary" onClick={matters.loadMore} disabled={matters.loadingMore}>
                {matters.loadingMore ? "正在加载…" : "加载更多"}
              </button>
            </div>
          ) : null}
          <p className="micro" style={{ padding: "0 var(--m-gutter)" }}>
            已同步 {matters.items.length} 条事项
          </p>
        </>
      )}
    </div>
  );
}
