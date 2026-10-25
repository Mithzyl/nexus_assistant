"use client";

import Link from "next/link";

import { EVIDENCE } from "@/lib/mobile/demo";
import { useDemo } from "@/components/mobile/providers";
import { EmptyState, PageShell } from "@/components/mobile/ui";

export default function EvidencePage({ params }: { params: { id: string } }) {
  const demo = useDemo();
  const evidence = EVIDENCE[params.id];

  if (!demo.enabled || !evidence) {
    return (
      <PageShell title="原始依据">
        <EmptyState
          title="没有找到这份依据"
          body="原始依据属于本地演示数据；正常模式不会展示虚构原文。"
        >
          <Link href="/matters" className="btn btn--secondary">返回事务</Link>
        </EmptyState>
      </PageShell>
    );
  }

  return (
    <PageShell title="原始依据" subtitle={evidence.meta}>
      <div className="card">
        <h2 className="serif" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
          {evidence.title}
        </h2>
        <pre className="code-block mt-12" aria-label="原始依据全文">
          {evidence.text}
        </pre>
      </div>
      <div className="notice mt-14">
        这是演示内保存的原文，不代表连接了银行、订单、票务或行程服务。
      </div>
      <Link href="/matters" className="btn btn--secondary mt-14">
        返回刚才的事项
      </Link>
    </PageShell>
  );
}
