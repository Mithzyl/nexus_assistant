"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowDownRight,
  Check,
  ClipboardCheck,
  Plus,
  Sparkles,
  Ticket,
  Wallet,
} from "lucide-react";

import { MATTER_KIND_LABEL, REFUND_STAGE_LABEL, type MatterKind } from "@/lib/mobile/demo";
import { useDemo, useUI } from "@/components/mobile/providers";
import { EmptyState, Pill, ScreenHeader } from "@/components/mobile/ui";

type Filter = "active" | "done" | "all";

function kindIcon(kind: MatterKind) {
  if (kind === "refund") return <Wallet size={15} />;
  if (kind === "ticket") return <Ticket size={15} />;
  if (kind === "wish") return <Sparkles size={15} />;
  if (kind === "project") return <ClipboardCheck size={15} />;
  if (kind === "travel") return <ArrowDownRight size={15} />;
  if (kind === "order") return <ClipboardCheck size={15} />;
  return <Check size={15} />;
}

export default function MattersPage() {
  const router = useRouter();
  const demo = useDemo();
  const { openCapture, toast } = useUI();
  const [filter, setFilter] = useState<Filter>("active");

  const matters = demo.enabled
    ? demo.matters.filter((matter) =>
        filter === "all" ? true : matter.status === filter,
      )
    : [];

  return (
    <div className="m-screen" style={{ paddingBottom: 8 }}>
      <ScreenHeader
        title="事务"
        subtitle="同一件事，汇集订单、通知和下一步"
        action={
          <button
            type="button"
            className="icon-button"
            onClick={() => openCapture("choose")}
            aria-label="交给助手"
          >
            <Plus size={18} />
          </button>
        }
      />

      {demo.enabled ? (
        <>
          <div className="chip-row" style={{ padding: "4px var(--m-gutter) 14px" }}>
            {(
              [
                ["active", "进行中"],
                ["done", "已完成"],
                ["all", "全部"],
              ] as Array<[Filter, string]>
            ).map(([value, label]) => (
              <button
                key={value}
                type="button"
                className="chip"
                aria-pressed={filter === value}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="m-stack-10" style={{ padding: "0 var(--m-gutter)" }}>
            {matters.length === 0 ? (
              <EmptyState
                title="这里还没有事务"
                body={
                  filter === "done"
                    ? "处理完的退款、看过的电影会出现在这里。"
                    : "收下一份订单或通知后，它会出现在这里。"
                }
              />
            ) : (
              matters.map((matter) => (
                <button
                  key={matter.id}
                  type="button"
                  className="card"
                  onClick={() => router.push(`/matters/${matter.id}`)}
                  style={{ width: "100%", textAlign: "left" }}
                >
                  <span className="card-head">
                    <span className="tile tile--sm">{kindIcon(matter.kind)}</span>
                    <span className="grow">
                      <span className="card-title" style={{ display: "block" }}>
                        {matter.title}
                      </span>
                      <span className="caption" style={{ display: "block", marginTop: 3 }}>
                        {matter.subtitle}
                      </span>
                    </span>
                    {matter.amount ? (
                      <span
                        className="tabular"
                        style={{ fontSize: 15, fontWeight: 600, whiteSpace: "nowrap" }}
                      >
                        ¥{matter.amount}
                      </span>
                    ) : null}
                  </span>

                  <span
                    className="flex items-center gap-8 mt-10"
                    style={{ flexWrap: "wrap" }}
                  >
                    <Pill tone="fog">{MATTER_KIND_LABEL[matter.kind]}</Pill>
                    {matter.refundStage ? (
                      <Pill tone={matter.refundStage === "received" ? "green" : "amber"}>
                        {REFUND_STAGE_LABEL[matter.refundStage]}
                      </Pill>
                    ) : null}
                    <span className="grow" />
                    <span className="micro">{matter.dateText}</span>
                  </span>

                  {matter.nextStep ? (
                    <>
                      <hr className="divider mt-10" />
                      <span className="flex items-center gap-6 mt-8">
                        <ArrowDownRight size={12} className="fog" />
                        <span className="caption grow" style={{ margin: 0 }}>
                          {matter.nextStep}
                        </span>
                        <span className="chevron">›</span>
                      </span>
                    </>
                  ) : null}
                </button>
              ))
            )}
          </div>

          <p className="micro text-center mt-16" style={{ padding: "0 var(--m-gutter)" }}>
            演示模式 · 本地示例数据，不代表服务器上的真实事务
          </p>
        </>
      ) : (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="服务器还没有事务接口"
            body="当前 PA 后端只提供账号、问答、内容来源和通勤；退款、订单、票据、心愿等整理结果尚未开放接口。正常模式不会用示例冒充服务器数据。"
          >
            <div style={{ display: "grid", gap: 8 }}>
              <Link href="/me/sources" className="btn btn--secondary">
                查看已同步的内容来源
              </Link>
              <Link href="/ask" className="btn btn--secondary">
                向助手提问
              </Link>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => {
                  demo.setEnabled(true);
                  toast("已开启演示模式，仅使用本地示例");
                }}
              >
                显式开启演示模式了解交互
              </button>
            </div>
          </EmptyState>

          <div className="notice mt-16">
            <strong>为什么不直接显示列表？</strong>
            <p style={{ margin: "4px 0 0" }}>
              “商家已发起退款”不等于“已到账”，金额相同也不代表同一订单。这些状态需要服务器保存事实，浏览器不会凭空推断。
            </p>
          </div>
        </div>
      )}
    </div>
  );
}
