"use client";

import Link from "next/link";

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
  /** Refresh notification settings without requesting a new permission. */
  async function load() {
    setError("");
    try {
      setStatus(await browserPushStatus());
    } catch {
      setError("没加载出来");
    }
  }

  useEffect(() => {
    if (state === "signedIn") void load();
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
      {state === "loading" ? <p>正在读取账号…</p> : state !== "signedIn" ? (
        <LoginRequired title="登录后开启浏览器通知" body="登录账号后可以管理此浏览器的通知。" />
      ) : status ? <>
        <p role="status">{!status.supported ? "当前这个浏览器不支持接收通知" : !status.configured ? "浏览器通知暂时用不了" : status.permission === "denied" ? "通知权限被关掉了，请去浏览器的网站设置里打开" : status.enabled ? "浏览器通知已开启" : "浏览器通知未开启"}</p>
        {status.supported && status.configured && status.permission !== "denied" ? (
          <button className="btn btn--primary" disabled={busy} onClick={() => void change()}>{busy ? "正在保存…" : status.enabled ? "关闭浏览器通知" : "开启浏览器通知"}</button>
        ) : <Link className="link-button" href="/me/commute/bark">改用手机锁屏提醒</Link>}
      </> : !error ? <p>正在读取通知设置…</p> : null}
      {busy && progress ? <p role="status">{progress}</p> : null}
      {error ? <div role="alert">{error} {!status ? <button type="button" className="link-button" onClick={() => void load()}>再试一次</button> : null}</div> : null}
    </section>
  </PageShell>;
}
