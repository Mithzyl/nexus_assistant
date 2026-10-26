"use client";

import Link from "next/link";
import { ArrowDownRight, FlaskConical, Plus, RefreshCw } from "lucide-react";

import { useAuth, useUI } from "@/components/mobile/providers";
import { EmptyState, ErrorCard, LoadingCard, ScreenHeader } from "@/components/mobile/ui";
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
        subtitle="需要持续推进的事，以及下一步"
        action={
          <button type="button" className="icon-button" onClick={() => openCapture("choose")} aria-label="交给助手">
            <Plus size={18} />
          </button>
        }
      />

      <div style={{ padding: "0 var(--m-gutter)" }}>
        <div className="flex items-center justify-between gap-10">
          <Link href="/me/lab" className="link-button">
            <FlaskConical size={14} /> 实验室预览
          </Link>
          <button type="button" className="link-button" onClick={matters.refresh} disabled={matters.loading}>
            <RefreshCw size={13} /> 刷新
          </button>
        </div>
      </div>

      {!signedIn ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState title="登录后读取服务器事项" body="事项、日程和提醒都按当前账号保存在 PA 服务器；未登录不会显示示例。">
            <Link href="/login" className="btn btn--secondary">登录 PA 账号</Link>
          </EmptyState>
        </div>
      ) : matters.loading && matters.items.length === 0 ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在读取事项…" />
        </div>
      ) : matters.error ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <ErrorCard message={matters.error.message} onRetry={matters.refresh} />
        </div>
      ) : matters.items.length === 0 ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="还没有事项"
            body="在问助手交代一件要持续跟进的事，或从实验室查看概念预览。正式事项只来自服务器。"
          >
            <div className="m-stack-8">
              <Link href="/ask" className="btn btn--secondary">去问助手</Link>
              <Link href="/me/lab" className="btn btn--quiet">查看实验室预览</Link>
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
          <p className="micro text-center" style={{ padding: "0 var(--m-gutter)" }}>
            来自 PA 服务器 · 当前已读取 {matters.items.length} 条；没有把本地预览混入这里。
          </p>
        </>
      )}
    </div>
  );
}
