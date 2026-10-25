"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, FileText } from "lucide-react";

import { EVIDENCE } from "@/lib/mobile/demo";
import { useDemo } from "@/components/mobile/providers";
import { EmptyState, Notice, PageShell } from "@/components/mobile/ui";

export default function AmbiguityPage() {
  const demo = useDemo();
  const router = useRouter();
  const [choice, setChoice] = useState<"coffee" | "shelf" | "">("");
  const resolved = demo.ambiguityResolved;

  if (!demo.enabled) {
    return (
      <PageShell title="待确认">
        <EmptyState
          title="确认归属需要本地示例"
          body="正常模式下没有待确认的退款通知；开启演示模式后可查看这类交互。"
        />
      </PageShell>
    );
  }

  if (resolved) {
    return (
      <PageShell title="待确认">
        <div className="card card--green">
          <div className="flex items-center gap-8">
            <Check size={16} className="green" />
            <span className="micro green">理好了</span>
          </div>
          <h2 className="serif mt-8" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
            关联好了，仍在等到账依据。
          </h2>
          <p className="caption mt-8" style={{ marginBottom: 0 }}>
            这条 ¥129 退款通知已放进「{resolved === "coffee" ? "便携咖啡机" : "收纳架"}」的购买后续。
            首页待确认已处理；它不会被标记为到账。
          </p>
        </div>
        <button
          type="button"
          className="btn btn--primary mt-14"
          onClick={() => router.push(`/matters/${resolved}`)}
        >
          查看这件事务
        </button>
        <Link href="/matters/evidence/ambiguous" className="link-button mt-6">
          <FileText size={14} /> 查看这条通知的原文
        </Link>
        <p className="micro mt-16">
          关联只改变“它属于哪件事”，不等于退款完成。到账仍需要明确的回执或银行依据。
        </p>
      </PageShell>
    );
  }

  return (
    <PageShell title="待确认" subtitle="退款通知 · 金额 ¥129">
      <div className="card card--amber">
        <span className="micro amber">两件退款金额相同</span>
        <h2 className="serif mt-6" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
          两件金额一样，这次不替你猜。
        </h2>
        <p className="body-text muted mt-8" style={{ marginBottom: 0 }}>
          通知没有订单号或商家名称。它属于哪件正在退款的商品？
        </p>
      </div>

      <div className="mt-14" style={{ display: "grid", gap: 8 }} role="radiogroup" aria-label="选择退款归属">
        {(
          [
            ["coffee", "便携咖啡机 · ¥129", "晨间生活店 · 10月17日申请"],
            ["shelf", "收纳架 · ¥129", "家居小店 · 10月20日申请"],
          ] as const
        ).map(([value, title, detail]) => (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={choice === value}
            onClick={() => setChoice(value)}
            style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 10,
              padding: 12,
              textAlign: "left",
              border: `0.5px solid ${choice === value ? "var(--m-fog)" : "var(--m-line)"}`,
              borderRadius: 12,
              background: choice === value ? "var(--m-mist)" : "var(--m-card)",
            }}
          >
            <span
              aria-hidden="true"
              style={{
                width: 18,
                height: 18,
                marginTop: 2,
                borderRadius: "50%",
                border: `1.5px solid ${choice === value ? "var(--m-fog)" : "var(--m-faint)"}`,
                background: choice === value ? "var(--m-fog)" : "transparent",
                flex: "0 0 auto",
              }}
            />
            <span>
              <span className="card-title" style={{ display: "block", fontSize: 15 }}>
                {title}
              </span>
              <span className="caption" style={{ display: "block", fontSize: 12 }}>
                {detail}
              </span>
            </span>
          </button>
        ))}
      </div>

      <Notice tone="amber" title="确认只关联，不标记到账">
        这条通知不是到账证明，不会把退款标成完成。相同金额也不自动合并事务。
      </Notice>

      <button
        type="button"
        className="btn btn--primary mt-14"
        disabled={!choice}
        onClick={() => {
          if (choice) demo.resolveAmbiguity(choice);
        }}
      >
        确认关联，不标记到账
      </button>
      <button
        type="button"
        className="btn btn--secondary mt-10"
        onClick={() => router.push("/today")}
      >
        还不确定，稍后再说
      </button>
      <Link href="/matters/evidence/ambiguous" className="link-button mt-6">
        <FileText size={14} /> 先核对这条通知的原文
      </Link>
      <div className="notice mt-16">
        <strong>原文</strong>
        <p className="evidence mt-4" style={{ marginBottom: 0 }}>{EVIDENCE.ambiguous.text}</p>
      </div>
    </PageShell>
  );
}
