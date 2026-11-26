"use client";

import Link from "next/link";

import type { ContentItem } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { useAuth } from "@/components/mobile/providers";
import {
  LoginLink,
  EmptyState,
  ErrorCard,
  LoadingCard,
  Notice,
  PageShell,
  Pill,
} from "@/components/mobile/ui";

const KIND_LABEL: Record<ContentItem["kind"], string> = {
  calendar_event: "日历事件",
  reminder: "提醒事项",
  note: "笔记",
};

export default function ContentDetailPage({ params }: { params: { id: string } }) {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const item = useApiResource<ContentItem>(
    signedIn ? `/api/pa/content/${params.id}` : null,
    { enabled: signedIn },
  );

  const data = item.data;

  return (
    <PageShell title="已同步内容" subtitle={data ? `${KIND_LABEL[data.kind]} · ${data.source_display_name}` : undefined}>
      {item.loading ? <LoadingCard label="正在读取内容…" /> : null}
      {item.error ? <ErrorCard error={item.error} message={errorText(item.error)} onRetry={item.reload} /> : null}
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后查看已同步内容" body="这些内容属于你的账号。">
          <LoginLink className="btn btn--primary">登录</LoginLink>
        </EmptyState>
      ) : null}

      {data ? (
        <>
          <div className="card">
            <div className="flex items-start justify-between gap-10">
              <h2 className="serif" style={{ fontSize: 24, fontWeight: 600, margin: 0 }}>
                {data.title || "（无标题）"}
              </h2>
              <Pill tone="fog">{KIND_LABEL[data.kind]}</Pill>
            </div>
            <div className="mt-12" style={{ display: "grid", gap: 6 }}>
              <Row label="来源" value={data.source_display_name} />
              {data.starts_at ? (
                <Row label="开始" value={formatDateTime(data.starts_at)} />
              ) : null}
              {data.ends_at ? <Row label="结束" value={formatDateTime(data.ends_at)} /> : null}
              {data.due_at ? <Row label="到期" value={formatDateTime(data.due_at)} /> : null}
              {data.location ? <Row label="地点" value={data.location} /> : null}
              {data.status ? <Row label="状态" value={data.status} /> : null}
              <Row label="来源更新" value={formatDateTime(data.source_updated_at)} />
              <Row label="同步时间" value={formatDateTime(data.synced_at)} />
              {data.tags.length > 0 ? <Row label="标签" value={data.tags.join("、")} /> : null}
            </div>
          </div>

          {data.body ? (
            <div className="card mt-14">
              <p className="micro">正文</p>
              <p className="body-text mt-8" style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
                {data.body}
              </p>
            </div>
          ) : (
            <Notice>
              这条内容里面是空的。<Link href="/me/sources" className="link-button">返回列表</Link>
            </Notice>
          )}

          <p className="micro mt-14" style={{ lineHeight: 1.7 }}>
            这是同步过来的副本；要修改请回到手机上的原始记录。
          </p>
          <Link href="/me/sources" className="btn btn--secondary mt-14">
            返回内容来源
          </Link>
        </>
      ) : null}
    </PageShell>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-14">
      <span className="caption" style={{ margin: 0 }}>{label}</span>
      <span style={{ fontSize: 14, textAlign: "right", overflowWrap: "anywhere" }}>{value}</span>
    </div>
  );
}
