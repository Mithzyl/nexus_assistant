"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Brain,
  Check,
  ClipboardCheck,
  FileText,
  Plane,
  RotateCcw,
  Sparkles,
  Ticket,
  Trash2,
} from "lucide-react";

import { todayLabel, relativeTime } from "@/lib/mobile/format";
import {
  useAuth,
  useDemo,
  useLocalRecords,
  useUI,
  type LocalRecord,
} from "@/components/mobile/providers";
import { TodayCommuteCard } from "@/components/mobile/commute-card";
import {
  ConfirmDialog,
  EmptyState,
  Pill,
  SectionHeading,
  Sheet,
} from "@/components/mobile/ui";

function logIcon(target: string | null) {
  if (!target) return <Check size={15} />;
  if (target.includes("coffee") || target.includes("shelf")) return <RotateCcw size={15} />;
  if (target.includes("prepare") || target.includes("movie")) return <Ticket size={15} />;
  if (target.includes("wish")) return <Sparkles size={15} />;
  if (target.includes("project")) return <ClipboardCheck size={15} />;
  if (target.includes("travel")) return <Plane size={15} />;
  if (target.includes("memor")) return <Brain size={15} />;
  if (target.includes("ambiguity")) return <FileText size={15} />;
  return <Check size={15} />;
}

export default function TodayPage() {
  const router = useRouter();
  const { state } = useAuth();
  const demo = useDemo();
  const { records, removeRecord } = useLocalRecords();
  const { openCapture } = useUI();
  const [selectedRecord, setSelectedRecord] = useState<LocalRecord | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const recent = useMemo(() => {
    const localRows = records.slice(0, 4).map((record) => ({
      key: `local-${record.id}`,
      title: record.title,
      detail: `本机保存 · ${relativeTime(record.createdAt)}`,
      target: null as string | null,
      record,
      icon: <FileText size={15} />,
    }));
    const demoRows = demo.enabled
      ? demo.logs.slice(0, 4).map((log) => ({
          key: `demo-${log.id}`,
          title: log.title,
          detail: log.detail,
          target: log.target,
          record: null as LocalRecord | null,
          icon: logIcon(log.target),
        }))
      : [];
    return [...localRows, ...demoRows].slice(0, 5);
  }, [records, demo.enabled, demo.logs]);

  return (
    <div className="m-screen m-stack-24" style={{ paddingBottom: 4 }}>
      <header style={{ padding: "8px var(--m-gutter) 0" }}>
        <div className="flex items-center justify-between gap-10">
          <h1 className="screen-title">今天</h1>
          {demo.enabled ? <Pill tone="amber">演示模式</Pill> : null}
        </div>
        <p className="caption" style={{ margin: "3px 0 0" }}>
          {todayLabel()}
        </p>
      </header>

      <div className="quick-row">
        <button
          type="button"
          className="quick-btn quick-btn--primary"
          onClick={() => openCapture("choose")}
        >
          <span aria-hidden="true">＋</span> 收下内容
        </button>
        <button
          type="button"
          className="quick-btn quick-btn--glass"
          onClick={() => openCapture("say")}
        >
          记一句
        </button>
      </div>

      <TodayCommuteCard />

      {demo.enabled && demo.pendingAmbiguity ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <button
            type="button"
            onClick={() => router.push("/matters/ambiguity")}
            style={{
              width: "100%",
              padding: 14,
              textAlign: "left",
              border: 0,
              borderRadius: 16,
              background: "var(--m-amber-soft)",
            }}
          >
            <span className="micro amber" style={{ display: "block" }}>
              待你确认 · 1
            </span>
            <span
              className="card-title"
              style={{ display: "block", marginTop: 4, fontSize: 17 }}
            >
              这笔 ¥129 退款属于哪件商品？
            </span>
            <span className="caption" style={{ display: "block", marginTop: 4 }}>
              咖啡机和收纳架金额相同，通知缺少订单号。
            </span>
            <span
              className="flex items-center gap-6"
              style={{ marginTop: 8, color: "var(--m-amber)", fontSize: 15, fontWeight: 600 }}
            >
              确认归属 <span aria-hidden="true">›</span>
            </span>
          </button>
        </section>
      ) : null}

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <SectionHeading title="最近整理" />
        {recent.length === 0 ? (
          <EmptyState
            title="还没有整理记录"
            body="交入第一份内容后，结果会出现在这里。"
          />
        ) : (
          <div className="list-card">
            {recent.map((row, index) => (
              <div key={row.key}>
                {index > 0 ? <div className="row-sep" /> : null}
                <button
                  type="button"
                  className="row"
                  onClick={() => {
                    if (row.record) setSelectedRecord(row.record);
                    else if (row.target) router.push(row.target);
                  }}
                >
                  <span className="tile tile--sm">{row.icon}</span>
                  <span className="row__body">
                    <span className="row__title" style={{ display: "block" }}>
                      {row.title}
                    </span>
                    <span className="row__detail" style={{ display: "block" }}>
                      {row.detail}
                    </span>
                  </span>
                  <span className="chevron" aria-hidden="true">
                    ›
                  </span>
                </button>
              </div>
            ))}
          </div>
        )}
      </section>

      {demo.enabled ? (
        <>
          <section style={{ padding: "0 var(--m-gutter)" }}>
            <SectionHeading title="接下来用得上" />
            <div className="list-card">
              <button
                type="button"
                className="row"
                onClick={() => router.push("/matters/prepare")}
              >
                <span className="tile tile--sm">
                  <Ticket size={15} />
                </span>
                <span className="row__body">
                  <span className="row__title" style={{ display: "block" }}>
                    明晚看电影
                  </span>
                  <span className="row__detail" style={{ display: "block" }}>
                    {demo.memories.find((memory) => memory.id === "cinema")?.enabled
                      ? "出门清单 · 影院经验：带薄外套"
                      : "出门清单 · 影院经验已停用"}
                  </span>
                </span>
                <span className="row__meta">19:30</span>
                <span className="chevron">›</span>
              </button>
              <div className="row-sep" />
              <button type="button" className="row" onClick={() => router.push("/matters")}>
                <span className="tile tile--sm">
                  <ClipboardCheck size={15} />
                </span>
                <span className="row__body">
                  <span className="row__title" style={{ display: "block" }}>
                    进行中事务
                  </span>
                  <span className="row__detail" style={{ display: "block" }}>
                    {demo.matters.filter((matter) => matter.status === "active").length} 件 ·
                    订单、退款与安排都在这里
                  </span>
                </span>
                <span className="chevron">›</span>
              </button>
            </div>
          </section>
          <p className="micro text-center" style={{ padding: "4px var(--m-gutter) 0" }}>
            演示模式 · 本地示例数据
          </p>
        </>
      ) : null}

      {!demo.enabled && state === "anonymous" ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <div className="notice">
            未登录时，今天只显示本机记录。登录后可读取服务器上的通勤建议与问答历史。
            <Link href="/login" className="link-button link-button--inline" style={{ display: "inline-flex", marginLeft: 6 }}>
              登录
            </Link>
          </div>
        </section>
      ) : null}

      <Sheet
        title="本机记录"
        open={Boolean(selectedRecord)}
        onClose={() => setSelectedRecord(null)}
      >
        {selectedRecord ? (
          <>
            <p className="card-title">{selectedRecord.title}</p>
            <p className="micro mt-4">
              {selectedRecord.kind === "note" ? "记一句" : "收下的内容"} ·{" "}
              {relativeTime(selectedRecord.createdAt)}
            </p>
            <p className="body-text mt-12" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
              {selectedRecord.text}
            </p>
            <div className="notice mt-16">
              这条内容只保存在当前浏览器。它没有上传到 PA 服务器，也不是事务或账单。
            </div>
            <button
              type="button"
              className="btn btn--danger mt-16"
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2 size={15} /> 删除本机记录
            </button>
          </>
        ) : null}
      </Sheet>

      <ConfirmDialog
        open={confirmDelete}
        title="删除这条本机记录？"
        body="删除后无法恢复。服务器上没有对应副本需要处理。"
        confirmLabel="删除"
        danger
        onCancel={() => setConfirmDelete(false)}
        onConfirm={() => {
          if (selectedRecord) removeRecord(selectedRecord.id);
          setConfirmDelete(false);
          setSelectedRecord(null);
        }}
      />
    </div>
  );
}
