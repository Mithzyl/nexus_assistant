"use client";

import Link from "next/link";
import { ChevronRight, FileText, Ticket } from "lucide-react";

import { useDemo } from "@/components/mobile/providers";
import { EmptyState, Notice, PageShell } from "@/components/mobile/ui";

export default function PreparePage() {
  const demo = useDemo();
  const cinema = demo.memories.find((memory) => memory.id === "cinema");
  const memoryOn = Boolean(cinema?.enabled);
  const items = [
    ...(memoryOn
      ? [{ id: "coat", title: "带薄外套", detail: `来自你的经验：${cinema?.text}` }]
      : []),
    { id: "ticket", title: "备好电影票据", detail: "出门前确认取票信息" },
    { id: "seat", title: "核对座位", detail: "3号厅 · 7排8座" },
  ];
  const prepared = items.filter((item) => demo.checks.includes(item.id)).length;

  if (!demo.enabled) {
    return (
      <PageShell title="出门准备">
        <EmptyState
          title="出门清单只在演示模式中提供"
          body="出门清单在演示模式中提供；开启演示模式后可以查看示例清单。"
        />
      </PageShell>
    );
  }

  return (
    <PageShell title="出门准备" subtitle="明天 · 10月24日">
      <div className="card">
        <div className="flex items-start gap-10">
          <span className="tile">
            <Ticket size={15} />
          </span>
          <div>
            <p className="card-title" style={{ fontSize: 15 }}>19:30 · 流浪地球 3</p>
            <p className="caption" style={{ margin: "2px 0 0" }}>
              万达柳南店 · 3号厅 · 7排8座
            </p>
          </div>
        </div>
      </div>

      <div className="flex items-center justify-between gap-10 mt-16">
        <h2 className="section-title" style={{ fontSize: 18 }}>准备清单</h2>
        <span className="micro">
          已准备 {prepared}/{items.length}
        </span>
      </div>
      <div className="progress-track mt-8">
        <span style={{ width: `${(prepared / items.length) * 100}%` }} />
      </div>

      <div className="card mt-12" style={{ display: "grid", gap: 6 }}>
        {items.map((item) => {
          const checked = demo.checks.includes(item.id);
          return (
            <label className="check" key={item.id}>
              <input
                type="checkbox"
                checked={checked}
                onChange={() => demo.toggleCheck(item.id)}
              />
              <span>
                <span style={{ display: "block", fontSize: 14 }}>{item.title}</span>
                <span className="caption" style={{ display: "block", fontSize: 12 }}>
                  {item.detail}
                </span>
                {item.id === "coat" ? (
                  <Link href="/me/memories" className="link-button link-button--inline" style={{ fontSize: 12 }}>
                    来自 10月12日的经验 <ChevronRight size={11} />
                  </Link>
                ) : null}
                {item.id === "ticket" ? (
                  <Link href="/matters/movie" className="link-button link-button--inline" style={{ fontSize: 12 }}>
                    查看票据 <ChevronRight size={11} />
                  </Link>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>

      {!memoryOn ? (
        <Notice tone="amber" title="影院经验已停用">
          停用的经验不会再生成清单条目。
          <Link href="/me/memories" className="link-button link-button--inline" style={{ marginLeft: 6, color: "var(--m-amber)" }}>
            查看并重新启用
          </Link>
        </Notice>
      ) : null}

      <div className="card card--mist mt-14">
        <div className="flex items-start gap-10">
          <FileText size={16} className="fog" style={{ marginTop: 2 }} />
          <div>
            <p className="card-title" style={{ fontSize: 15 }}>让这次，帮到下一次</p>
            <p className="caption" style={{ margin: "2px 0 0" }}>结束后留一句自己的体验。</p>
          </div>
        </div>
        <Link href="/me/lesson" className="btn btn--secondary mt-12">
          结束后随手记一条
        </Link>
      </div>

      <p className="micro mt-8">由你手动记录体验，不会判断你是否已经观影。</p>

      {prepared === items.length ? (
        <div className="notice notice--green mt-12">
          <strong>清单已完成</strong>
          <p style={{ margin: "4px 0 0" }}>勾选只记录在本地，需要你自己带好东西。</p>
        </div>
      ) : null}
    </PageShell>
  );
}
