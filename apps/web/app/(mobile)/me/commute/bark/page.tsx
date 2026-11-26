"use client";

import { useState } from "react";
import { Bell, ChevronRight, KeyRound } from "lucide-react";

import type { BarkStatus, BarkTest } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { useAuth } from "@/components/mobile/providers";
import {
  LoginLink,
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  PageShell,
  SwitchRow,
} from "@/components/mobile/ui";

export default function BarkPage() {
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const bark = useApiResource<BarkStatus>(signedIn ? "/api/pa/commute/bark" : null, {
    enabled: signedIn,
  });
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmUnbind, setConfirmUnbind] = useState(false);
  const [confirmTest, setConfirmTest] = useState(false);
  const [testResult, setTestResult] = useState<BarkTest | null>(null);

  const data = bark.data;
  const configured = Boolean(data?.configured);
  const auto = Boolean(data?.auto_reminders_enabled);
  const assistantNotifications = Boolean(data?.assistant_notifications_enabled);
  const linksAvailable = Boolean(data?.web_links_available);

  const bind = async () => {
    if (!key.trim()) {
      setError("请填写推送设备密钥。");
      return;
    }
    setBusy("bind");
    setError(null);
    setTestResult(null);
    try {
      const updated = await apiRequest<BarkStatus>("/api/pa/commute/bark/binding", {
        method: "POST",
        body: { device_key: key.trim() },
      });
      bark.setData(updated);
      setKey("");
      setError(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "绑定未能完成。");
    } finally {
      setBusy(null);
    }
  };

  const unbind = async () => {
    setBusy("unbind");
    setError(null);
    try {
      const updated = await apiRequest<BarkStatus>("/api/pa/commute/bark/binding", {
        method: "DELETE",
      });
      bark.setData(updated);
      setConfirmUnbind(false);
      setTestResult(null);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "解绑未能完成。");
    } finally {
      setBusy(null);
    }
  };

  /** Update one Bark subscription without changing the other switch. */
  const updateNotifications = async (setting: "auto_reminders_enabled" | "assistant_notifications_enabled", enabled: boolean) => {
    if (!configured) return;
    setBusy(setting);
    setError(null);
    try {
      const updated = await apiRequest<BarkStatus>("/api/pa/commute/bark", {
        method: "PATCH",
        body: { [setting]: enabled },
      });
      bark.setData(updated);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "设置未能保存。");
    } finally {
      setBusy(null);
    }
  };

  const sendTest = async () => {
    setConfirmTest(false);
    setBusy("test");
    setError(null);
    setTestResult(null);
    try {
      const result = await apiRequest<BarkTest>("/api/pa/commute/bark/test", {
        method: "POST",
        body: { confirm_send: true },
      });
      setTestResult(result);
      await bark.reload();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "测试发送未能完成。");
    } finally {
      setBusy(null);
    }
  };

  return (
    <PageShell title="手机锁屏提醒">
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后设置手机锁屏提醒" body="绑定设备后，可选择这个账号要接收的提醒。">
          <LoginLink className="btn btn--primary">登录</LoginLink>
        </EmptyState>
      ) : null}

      {bark.loading ? <LoadingCard label="正在读取提醒设置…" /> : null}
      {bark.error ? <ErrorCard error={bark.error} message={errorText(bark.error)} onRetry={bark.reload} /> : null}

      {signedIn && data ? (
        <>
          <div className={`card ${auto || assistantNotifications ? "card--green" : configured ? "card--amber" : ""}`}>
            <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
              <Bell size={16} className={configured ? "green" : "faint"} />
              <strong style={{ fontSize: 15 }}>
                {!configured
                  ? "还没有绑定手机提醒"
                  : auto || assistantNotifications
                    ? "已开启自动提醒"
                    : "已绑定手机，自动提醒关闭"}
              </strong>
            </div>

            {configured && data.available === false ? (
              <p className="caption amber" style={{ margin: "6px 0 0" }}>
                暂时发不出手机提醒，请稍后再试。
              </p>
            ) : null}
            {data.last_test_at ? (
              <p className="micro" style={{ margin: "6px 0 0" }}>
                上次测试：{data.last_test_status ? ({ ok: "已发送", sent: "已发送", success: "已发送", failed: "没发出去", error: "没发出去" } as Record<string, string>)[data.last_test_status] ?? "已记录" : "还没测过"} · {formatDateTime(data.last_test_at)}
              </p>
            ) : null}
          </div>

          <div className="card mt-14">
            <SwitchRow
              title="通勤自动提醒"
              detail={
                configured
                  ? "前一晚发预告、早上发确认，路况有大变化时再提醒。"
                  : "先填写推送设备密钥。"
              }
              checked={auto}
              disabled={!configured || busy !== null}
              busy={busy === "auto_reminders_enabled"}
              onChange={() => void updateNotifications("auto_reminders_enabled", !auto)}
            />
            <hr className="divider" />
            <SwitchRow
              title="助手追问与到期提醒"
              detail={linksAvailable
                ? "点击追问可回到原问题，点击待办或日程提醒可打开「今天」。"
                : "暂时没法点通知直接跳回原页面。"}
              checked={assistantNotifications}
              disabled={!configured || (!linksAvailable && !assistantNotifications) || busy !== null}
              busy={busy === "assistant_notifications_enabled"}
              onChange={() => void updateNotifications("assistant_notifications_enabled", !assistantNotifications)}
            />
            <hr className="divider" />
            <button
              type="button"
              className="row"
              onClick={() => setConfirmTest(true)}
              disabled={!configured || busy === "test"}
              style={{ paddingLeft: 0, paddingRight: 0, opacity: configured ? 1 : 0.5 }}
            >
              <span className="row__body">
                <span className="row__title" style={{ display: "block", fontSize: 15 }}>
                  {busy === "test" ? "正在发送测试…" : "发送一条测试提醒"}
                </span>
                <span className="row__detail" style={{ display: "block" }}>
                  向已绑定的手机发一条测试通知
                </span>
              </span>
              <ChevronRight size={16} className="chevron" aria-hidden="true" />
            </button>
          </div>

          {testResult ? (
            <div className={`card mt-14 ${testResult.accepted ? "card--green" : "card--amber"}`}>
              <p className="card-title" style={{ fontSize: 15 }}>
                {testResult.status === "accepted"
                  ? "已经发出去啦，看看手机锁屏"
                  : testResult.status === "failed"
                    ? "发送失败了"
                    : "不确定有没有发成功，先看一眼手机"}
              </p>

              <p className="micro" style={{ margin: "6px 0 0" }}>
                {formatDateTime(testResult.tested_at)}
              </p>
            </div>
          ) : null}

          {error ? (
            <p className="caption red" role="alert" style={{ margin: "12px 0 0" }}>
              {error}
            </p>
          ) : null}

          <div className="card mt-16">
            <p className="card-title" style={{ fontSize: 15 }}>
              <KeyRound size={14} style={{ display: "inline", verticalAlign: "-2px" }} />{" "}
              {configured ? "更换推送设备密钥" : "绑定手机提醒"}
            </p>
            <p className="caption" style={{ margin: "4px 0 10px" }}>
              在手机的推送应用里复制设备密钥。
            </p>
            <label className="field" htmlFor="bark-key">
              <input
                id="bark-key"
                className="input"
                type="password"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="粘贴推送设备密钥"
                autoComplete="off"
                spellCheck={false}
              />
            </label>
            <button
              type="button"
              className="btn btn--primary"
              onClick={() => void bind()}
              disabled={busy === "bind"}
            >
              {busy === "bind" ? "正在保存…" : configured ? "更换密钥" : "保存绑定"}
            </button>
            {configured ? (
              <button
                type="button"
                className="btn btn--danger mt-10"
                onClick={() => setConfirmUnbind(true)}
                disabled={busy === "unbind"}
              >
                解除绑定
              </button>
            ) : null}
          </div>

        </>
      ) : null}

      <ConfirmDialog
        open={confirmUnbind}
        title="解除手机提醒绑定？"
        body="解除后，这部手机将不再收到这些提醒。"
        confirmLabel="解除绑定"
        danger
        busy={busy === "unbind"}
        onCancel={() => setConfirmUnbind(false)}
        onConfirm={() => void unbind()}
      />

      <ConfirmDialog
        open={confirmTest}
        title="发送测试提醒？"
        body="向已绑定的手机发送一条通知。"
        confirmLabel="发送测试"
        busy={busy === "test"}
        onCancel={() => setConfirmTest(false)}
        onConfirm={() => void sendTest()}
      />
    </PageShell>
  );
}
