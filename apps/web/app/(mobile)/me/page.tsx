"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  Brain,
  ChevronRight,
  CircleHelp,
  Lock,
  MapPin,
  Plus,
  RefreshCw,
  SquarePen,
  Sun,
  Archive,
  UserRound,
  LogOut,
  FlaskConical,
  Database,
  Smartphone,
  CalendarDays,
} from "lucide-react";

import { useAuth, useDemo, useUI } from "@/components/mobile/providers";
import { ConfirmDialog, ScreenHeader, SwitchRow } from "@/components/mobile/ui";
import { formatDateTime } from "@/lib/mobile/format";
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
  const demo = useDemo();
  const matters = useMatters(signedIn, user?.user_id ?? null);
  const sources = useApiResource<DataSource[]>(signedIn ? "/api/pa/data-sources" : null, { enabled: signedIn });
  const { openCapture, theme, setTheme, toast } = useUI();
  const [confirmLogout, setConfirmLogout] = useState(false);
  const [busyLogout, setBusyLogout] = useState(false);
  const [logoutNote, setLogoutNote] = useState<string | null>(null);

  const enabledMemories = demo.memories.filter((memory) => memory.enabled).length;
  const lastSync = (sources.data ?? [])
    .map((source) => source.last_sync_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1) ?? null;
  const iosSources = (sources.data ?? []).filter(
    (source) => source.source_type === "calendar" || source.source_type === "reminders",
  );
  const appCalendarState = (() => {
    if (!signedIn) return { detail: "登录后读取内置日程和提醒。", status: "需登录" };
    if (matters.loading && matters.items.length === 0) {
      return { detail: "正在向 PA 服务器读取应用内日程。", status: "读取中" };
    }
    if (matters.error) {
      return {
        detail: "这次读取失败；不代表应用内日程未接通，可稍后重试。",
        status: "读取失败",
      };
    }
    return {
      detail:
        matters.items.length > 0
          ? `应用内日程与提醒可用，当前读取 ${matters.items.length} 个事项。`
          : "应用内日程与提醒可用，当前没有正式事项。",
      status: "可用",
    };
  })();
  const iosConnectionState = (() => {
    if (!signedIn) return { detail: "登录后读取苹果端来源连接状态。", status: "需登录" };
    if (sources.loading && !sources.data) {
      return { detail: "正在读取苹果端来源状态。", status: "读取中" };
    }
    if (sources.error) {
      return {
        detail: "这次来源状态读取失败；不代表苹果端尚未连接。",
        status: "读取失败",
      };
    }
    if (iosSources.length > 0) {
      return {
        detail: `${iosSources.length} 个来自苹果端的日历或提醒来源；上传副本不等于写回系统应用。`,
        status: "已连接",
      };
    }
    return {
      detail: "服务器暂无苹果端日历或提醒来源。上传副本不等于写回系统应用。",
      status: "尚未连接",
    };
  })();

  const accountTitle = (() => {
    if (state === "loading") return "正在验证会话…";
    if (signedIn && user) return user.display_name;
    if (state === "offline") return user ? `${user.display_name}（离线）` : "登录状态暂无法验证";
    if (state === "anonymous") return user ? user.display_name : "未登录";
    return "未登录";
  })();

  const accountDetail = (() => {
    if (state === "loading") return "正在向 PA 服务器确认登录状态。";
    if (signedIn) return "已登录 PA 服务器。";
    if (state === "offline") return "网络不可用，保留上次登录身份与本地记录。";
    return "登录 PA 本地账号后，问答与通勤提醒由服务器处理。";
  })();

  const doLogout = async () => {
    setBusyLogout(true);
    const result = await logout();
    setBusyLogout(false);
    setConfirmLogout(false);
    setLogoutNote(
      result.revoked
        ? "已退出，并撤销了当前设备的服务器会话。"
        : "本机已退出；服务器撤销未能确认，重新登录后旧凭据会自然过期。",
    );
    toast(result.revoked ? "已退出登录" : "本机已退出，服务器撤销未确认");
    router.push("/today");
  };

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 24 }}>
      <ScreenHeader
        title="我的"
        subtitle="账号、来源、通勤与隐私都在这里"
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

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <div className="card">
          <div className="flex items-start gap-12">
            <UserRound size={40} className="fog" strokeWidth={1.3} style={{ flex: "0 0 auto" }} />
            <div className="grow">
              <p className="card-title">{accountTitle}</p>
              <p className="caption" style={{ margin: "2px 0 0" }}>
                {accountDetail}
              </p>
              {signedIn && user ? (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  {user.email}
                </p>
              ) : null}
              {signedIn && expiresAt ? (
                <p className="micro" style={{ margin: "4px 0 0" }}>
                  会话有效期至 {formatDateTime(expiresAt)}
                  {renewing ? " · 正在续期…" : ""}
                </p>
              ) : null}
              {renewMessage && !renewing && signedIn ? (
                <p className="micro amber" style={{ margin: "4px 0 0" }}>
                  {renewMessage === "已续期" ? "已向服务器确认续期。" : renewMessage}
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
              <Link href="/login" className="btn btn--primary btn--small">
                登录
              </Link>
            ) : null}
            {state === "offline" ? (
              <button
                type="button"
                className="btn btn--secondary btn--small"
                onClick={() => {
                  void renew();
                  toast("正在重新核对登录状态");
                }}
              >
                <RefreshCw size={13} /> 验证会话
              </button>
            ) : null}
            {signedIn ? (
              <>
                <button
                  type="button"
                  className="link-button"
                  onClick={() => void renew()}
                  disabled={renewing}
                >
                  <RefreshCw size={13} /> {renewing ? "续期中…" : "立即续期"}
                </button>
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
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          数据与同步
        </h2>
        <div className="group">
          <Row
            href="/matters"
            icon={<CalendarDays size={14} />}
            title="内置日程与提醒"
            detail={appCalendarState.detail}
            status={appCalendarState.status}
          />
          <Row
            href="/me/sources"
            icon={<Smartphone size={14} />}
            title="iOS 客户端"
            detail={iosConnectionState.detail}
            status={iosConnectionState.status}
          />
          <Row
            href="/me/sources"
            icon={<Database size={14} />}
            title="内容副本最近同步"
            detail={lastSync ? `最近一次上传批次：${formatDateTime(lastSync)}` : "还没有上传过内容副本"}
            status={sources.loading ? "读取中" : lastSync ? "已上传" : "无记录"}
          />
        </div>
        <p className="micro" style={{ margin: "8px 0 0" }}>
          应用内日程与苹果端连接状态分别读取；提醒只表示服务器有记录，尚未确认送达。
        </p>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          通勤与提醒
        </h2>
        <div className="group">
          <Row
            href="/me/commute"
            icon={<MapPin size={14} />}
            title="通勤设置"
            detail="固定地点与时间；检查与提醒由服务器完成"
          />
          <Row
            href="/me/commute/bark"
            icon={<Sun size={14} />}
            title="Bark 提醒"
            detail="绑定账号后接收服务端通勤提醒；Bark 不是 Web Push"
          />
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          你的内容
        </h2>
        <div className="group">
          <Row
            href="/me/sources"
            icon={<Archive size={14} />}
            title="内容来源"
            detail="已同步的日历、提醒与笔记副本"
          />
          <Row
            href="/me/sources/import"
            icon={<SquarePen size={14} />}
            title="导入笔记"
            detail="文本或 Markdown，主动导入后才会上传"
          />
          <Row
            href="/me/memories"
            icon={<Brain size={14} />}
            title="个人记忆"
            detail={
              demo.enabled
                ? `${enabledMemories} 条生效 · ${demo.memories.length} 条保存（演示）`
                : "服务器暂无记忆管理接口"
            }
          />
          <Row
            href="/me/lesson"
            icon={<SquarePen size={14} />}
            title="记住一点经验"
            detail="主动写下的，不靠模型推测"
          />
          <Row
            href="/me/memory-search"
            icon={<CircleHelp size={14} />}
            title="记忆检索（遗留接口）"
            detail="只读代理；可用性取决于上游服务"
          />
        </div>
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          外观与演示
        </h2>
        <div className="group" style={{ padding: "6px 14px" }}>
          <div style={{ padding: "8px 0" }}>
            <p className="card-title" style={{ fontSize: 15 }}>
              外观
            </p>
            <p className="caption" style={{ margin: "2px 0 8px" }}>
              默认跟随系统；深色模式使用同一套暖白与雾蓝的暗色值。
            </p>
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
          <hr className="divider" />
          <SwitchRow
            title="演示模式（本地示例）"
            detail="使用本机事项/日程预览、个人经验与旧版消费示例；开启后页面明确标注，关闭时不混入真实页面。"
            checked={demo.enabled}
            onChange={() => {
              demo.setEnabled(!demo.enabled);
              toast(demo.enabled ? "已关闭演示模式" : "已开启演示模式，仅使用本地示例");
            }}
          />
        </div>
        {demo.enabled ? (
          <div className="notice notice--amber mt-8">
            旧版消费演示刷新后恢复；计划接续预览保存在本机，删除后不再恢复。两者都不会写入 PA 服务器，也不代表真实事项。
          </div>
        ) : null}
      </section>

      <section style={{ padding: "0 var(--m-gutter)" }}>
        <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
          内容与隐私
        </h2>
        <div className="group">
          <Row
            href="/me/lab"
            icon={<FlaskConical size={14} />}
            title="实验室"
            detail="消费售后、愿望清单与计划接续预览；概念预览，不进入正式数据"
          />
          <Row
            href="/me/privacy"
            icon={<Lock size={14} />}
            title="内容与隐私"
            detail="哪些在服务器、哪些只在本机浏览器"
          />
          <Row
            href="/me/about"
            icon={<CircleHelp size={14} />}
            title="关于生活助手"
            detail="当前能力、原生限制与演示范围"
          />
          <Row
            href="/board"
            icon={<Archive size={14} />}
            title="旧版看板"
            detail="保留原有桌面看板入口"
          />
        </div>
      </section>

      <p className="micro text-center" style={{ padding: "4px var(--m-gutter) 0", lineHeight: 1.7 }}>
        {signedIn
          ? "已连接 PA 服务器。Bark 与通勤提醒由服务端调度，不依赖浏览器定时器。"
          : "未登录 PA 服务器。问答与通勤提醒需要登录后才由服务器处理。"}
      </p>

      <ConfirmDialog
        open={confirmLogout}
        title="退出登录？"
        body="本机会清理当前账号的问答内存状态与登录 Cookie；服务器上的账号数据仍保留。"
        confirmLabel="退出登录"
        danger
        busy={busyLogout}
        onCancel={() => setConfirmLogout(false)}
        onConfirm={() => void doLogout()}
      />
    </div>
  );
}
