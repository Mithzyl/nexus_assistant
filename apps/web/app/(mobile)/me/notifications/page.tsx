"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/mobile/providers";
import { LoginRequired, PageShell } from "@/components/mobile/ui";
import { browserPushStatus, enableBrowserPush, disableBrowserPush } from "@/lib/mobile/browser-push";

/** Configure real background browser notifications, with explicit permission and error states. */
export default function BrowserNotificationsPage() {
  const { state } = useAuth();
  const [status, setStatus] = useState<Awaited<ReturnType<typeof browserPushStatus>> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  useEffect(() => {
    if (state === "signedIn") browserPushStatus().then(setStatus).catch(() => setError("暂时无法读取通知设置，请刷新重试。"));
  }, [state]);

  /** Change subscriptions only on user action and display enabled only after server persistence. */
  async function change() {
    setBusy(true); setError(""); setProgress("");
    try {
      if (status?.enabled) await disableBrowserPush(); else await enableBrowserPush(setProgress);
      setStatus(await browserPushStatus());
    } catch (caught) { setError(caught instanceof Error ? caught.message : "通知设置失败，请重试。"); }
    finally { setBusy(false); setProgress(""); }
  }

  return <PageShell title="浏览器通知" fallbackHref="/me">
    <section className="m-stack-12" style={{ padding: "0 var(--m-gutter)" }}>
      <p>开启后，待办、日程和通勤到时间时会通过这个浏览器提醒你，关闭网页后也能接收。</p>
      <p className="text-muted">需要浏览器和系统允许通知、设备联网。浏览器完全退出或系统限制后台运行时，通知可能延迟。不会因此完成待办，也不代表已经写入手机日历。</p>
      {state === "loading" ? <p>正在读取账号…</p> : state !== "signedIn" ? (
        <LoginRequired title="登录后开启浏览器通知" body="登录账号后可以管理此浏览器的通知。" />
      ) : status ? <>
        <p role="status">{!status.supported ? "当前浏览器不支持后台推送。" : !status.configured ? "服务端尚未配置浏览器推送。" : status.permission === "denied" ? "通知已被浏览器阻止，请在网站设置中允许。" : status.enabled ? "浏览器通知已开启" : "浏览器通知未开启"}</p>
        <button className="btn btn--primary" disabled={busy || !status.supported || !status.configured || status.permission === "denied"} onClick={() => void change()}>{busy ? "正在保存…" : status.enabled ? "关闭浏览器通知" : "开启浏览器通知"}</button>
      </> : <p>正在读取通知设置…</p>}
      {busy && progress ? <p role="status">{progress}</p> : null}
      {error ? <p role="alert">{error}</p> : null}
    </section>
  </PageShell>;
}
