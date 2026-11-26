"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Bell,
  Brain,
  ChevronRight,
  CircleHelp,
  Database,
  FileText,
  FlaskConical,
  Lightbulb,
  Lock,
  LogOut,
  MapPin,
  Search,
  UserRound,
} from "lucide-react";

import { DataConnections } from "@/components/mobile/data-connections";
import { NexusLogin } from "@/components/mobile/nexus-login";
import { useAuth, useUI } from "@/components/mobile/providers";
import { LoginLink, ConfirmDialog, ScreenHeader } from "@/components/mobile/ui";
import { useMatters } from "@/lib/mobile/use-matters";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import type { DataSource } from "@/lib/mobile/api";

const THEMES: Array<{ value: "system" | "light" | "dark"; label: string }> = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

function Row({
  href,
  icon,
  title,
  detail,
  status,
}: {
  href: string;
  icon: React.ReactNode;
  title: string;
  detail: string;
  status?: string;
}) {
  return (
    <Link href={href} className="row">
      <span className="tile tile--sm">{icon}</span>
      <span className="row__body">
        <span className="row__title" style={{ display: "block" }}>
          {title}
        </span>
        <span className="row__detail" style={{ display: "block" }}>
          {detail}
        </span>
      </span>
      {status ? <span className="row__meta">{status}</span> : null}
      <ChevronRight className="chevron" size={16} />
    </Link>
  );
}

export default function MePage() {
  const router = useRouter();
  const { state, user, expiresAt, error, renewing, renewMessage, logout, renew } = useAuth();
  const signedIn = state === "signedIn";
  const matters = useMatters(signedIn, user?.user_id ?? null);
  const sources = useApiResource<DataSource[]>(signedIn ? "/api/pa/data-sources" : null, { enabled: signedIn });
  const { theme, setTheme, toast } = useUI();
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [busyLogout, setBusyLogout] = useState(false);
  const [logoutNote, setLogoutNote] = useState<string | null>(null);

  const accountTitle = (() => {
    if (state === "loading") return "正在确认登录…";
    if (signedIn && user) return user.email;
    if (state === "offline") return "连接断开了";
    if (state === "anonymous") return user ? user.display_name : "未登录";
    return "未登录";
  })();

  const expiresSoon = Boolean(expiresAt && Date.parse(expiresAt) - Date.now() <= 86_400_000);

  const accountDetail = (() => {
    if (state === "loading") return "正在同步账号状态…";
    if (signedIn) return "";
    if (state === "offline") return "";
    return "登录后，问答和通勤提醒会跟着账号走。";
  })();

  const doLogout = async () => {
    setBusyLogout(true);
    const result = await logout();
    setBusyLogout(false);
    setConfirmLogout(false);
    setLogoutNote(
      result.revoked
        ? "已退出，并结束当前设备的登录状态。"
        : "已在本设备退出登录",
    );
    toast(result.revoked ? "已退出登录" : "这台设备已退出，账号侧未确认");
    router.push("/today");
  };

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 24 }}>
      <ScreenHeader title="我的" subtitle="账号和设置" />

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          账号与同步
        </h2>
        <div className="card">
          <div className="flex items-start gap-12">
            <UserRound size={40} className="fog" strokeWidth={1.3} style={{ flex: "0 0 auto" }} />
            <div className="grow">
              <p className="card-title">{accountTitle}</p>
              {accountDetail ? <p className="caption">{accountDetail}</p> : null}
              {signedIn && expiresSoon ? (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  登录快过期了
                  {renewing ? " · 正在延长…" : ""}
                </p>
              ) : null}
              {renewMessage && !renewing && signedIn ? (
                <p className="micro amber" style={{ margin: "4px 0 0" }}>
                  {renewMessage === "已续期" ? "已延长登录。" : renewMessage}
                </p>
              ) : null}
              {state === "anonymous" && error ? (
                <p className="micro amber" style={{ margin: "4px 0 0" }}>
                  {error}
                </p>
              ) : null}
              {logoutNote ? (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  {logoutNote}
                </p>
              ) : null}
            </div>
          </div>

          <div className="flex items-center gap-12 mt-12" style={{ flexWrap: "wrap" }}>
            {state === "anonymous" ? (
              <LoginLink className="btn btn--primary btn--small">
                登录
              </LoginLink>
            ) : null}
            {state === "offline" ? (
              <LoginLink className="btn btn--secondary btn--small">重新登录</LoginLink>
            ) : null}
            {signedIn ? (
              <>
                {expiresSoon || renewing ? <button type="button" className="link-button" onClick={() => void renew()} disabled={renewing}>
                  {renewing ? "正在延长…" : "延长登录"}
                </button> : null}
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setConfirmLogout(true)}
                >
                  <LogOut size={13} /> 退出登录
                </button>
              </>
            ) : null}
          </div>
          {signedIn ? <details className="mt-10"><summary>账号设置</summary><NexusLogin link /></details> : null}
        </div>
      </section>

      <DataConnections
        key={user?.user_id ?? "signed-out"}
        signedIn={signedIn}
        mattersLoading={matters.loading}
        mattersError={Boolean(matters.error)}
        matters={matters.items}
        sourcesLoading={sources.loading}
        sourcesError={Boolean(sources.error)}
        sources={sources.data ?? []}
        onRetry={() => {
          matters.refresh();
          sources.reload();
        }}
        onSourcesChanged={sources.reload}
      />

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          内容与导入
        </h2>
        <div className="group">
          <Row
            href="/me/sources"
            icon={<Database size={14} strokeWidth={1.5} />}
            title="内容来源"
            detail="已同步的日历、提醒与笔记"
          />
          <Row
            href="/me/sources/import"
            icon={<FileText size={14} strokeWidth={1.5} />}
            title="导入笔记"
            detail="导入文本或 Markdown"
          />
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          个人记忆与偏好
        </h2>
        <div className="group">
          <Row
            href="/me/memories"
            icon={<Brain size={14} strokeWidth={1.5} />}
            title="个人记忆"
            detail="查看已保存的记忆与待确认内容"
          />
          <Row
            href="/me/lesson"
            icon={<Lightbulb size={14} strokeWidth={1.5} />}
            title="本机笔记"
            detail="只保存你主动写下的内容"
          />
          <Row
            href="/me/memory-search"
            icon={<Search size={14} strokeWidth={1.5} />}
            title="搜记忆"
            detail="按关键词查找个人记忆"
          />
          <Row
            href="/me/commute"
            icon={<MapPin size={14} strokeWidth={1.5} />}
            title="通勤设置"
            detail="常用地点与出发时间"
          />
          <Row href="/me/notifications" icon={<Bell size={18} />} title="浏览器通知" detail="待办、日程和通勤的到期推送" />
          <Row
            href="/me/commute/bark"
            icon={<Bell size={14} strokeWidth={1.5} />}
            title="手机锁屏提醒"
            detail="在手机接收通勤、助手追问和到期提醒"
          />
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          外观与显示
        </h2>
        <div className="group" style={{ padding: 10 }}>
          <div className="chip-row">
            {THEMES.map((option) => (
              <button
                key={option.value}
                type="button"
                className="chip chip--sm"
                aria-pressed={theme === option.value}
                onClick={() => setTheme(option.value)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          更多与关于
        </h2>
        <div className="group">
          <Row
            href="/me/about"
            icon={<CircleHelp size={14} strokeWidth={1.5} />}
            title="关于生活助手"
            detail="版本、能力与使用边界"
          />
          <Row
            href="/me/privacy"
            icon={<Lock size={14} strokeWidth={1.5} />}
            title="隐私说明"
            detail="我们如何保存与删除你的内容"
          />
          <Row
            href="/me/lab"
            icon={<FlaskConical size={14} strokeWidth={1.5} />}
            title="实验室与高级选项"
            detail="概念预览、演示模式与旧版看板"
          />
        </div>
      </section>

      <p className="micro" style={{ padding: "4px var(--m-gutter) 0" }}>
        生活助手 · 版本 0.1.0
      </p>

      <ConfirmDialog
        open={confirmLogout}
        title="退出登录？"
        body="退出会清除本机的问答状态。"
        confirmLabel="退出登录"
        danger
        busy={busyLogout}
        onCancel={() => setConfirmLogout(false)}
        onConfirm={() => void doLogout()}
      />
    </div>
  );
}
