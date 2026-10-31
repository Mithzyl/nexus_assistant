"use client";

import { useState } from "react";
import Link from "next/link";
import { Bell, ChevronRight, KeyRound } from "lucide-react";

import type { BarkStatus, BarkTest } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { useAuth } from "@/components/mobile/providers";
import {
  ConfirmDialog,
  EmptyState,
  ErrorCard,
  LoadingCard,
  Notice,
  PageShell,
  Pill,
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

  const bind = async () => {
    if (!key.trim()) {
      setError("请输入 Bark 的 device key。");
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

  const toggleAuto = async () => {
    if (!configured) return;
    setBusy("toggle");
    setError(null);
    try {
      const updated = await apiRequest<BarkStatus>("/api/pa/commute/bark", {
        method: "PATCH",
        body: { auto_reminders_enabled: !auto },
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
    <PageShell title="Bark 提醒" subtitle="在 Bark 客户端接收通勤提醒。">
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后绑定 Bark" body="绑定账号后，通勤提醒会发送到你的 Bark。">
          <Link href="/login" className="btn btn--primary">登录</Link>
        </EmptyState>
      ) : null}

      {bark.loading ? <LoadingCard label="正在读取 Bark 状态…" /> : null}
      {bark.error ? <ErrorCard message={errorText(bark.error)} onRetry={bark.reload} /> : null}

      {signedIn && data ? (
        <>
          <div className={`card ${configured && auto ? "card--green" : configured ? "card--amber" : ""}`}>
            <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
              <Bell size={16} className={configured ? "green" : "faint"} />
              <strong style={{ fontSize: 15 }}>
                {!configured
                  ? "还没有绑定 Bark"
                  : auto
                    ? "已开启自动提醒"
                    : "已绑定 Bark，自动提醒关闭"}
              </strong>
              <span className="grow" />
              <Pill tone={configured && auto ? "green" : configured ? "amber" : "neutral"}>
                {data.state === "enabled"
                  ? "自动提醒"
                  : data.state === "disabled"
                    ? "仅手动测试"
                    : "未配置"}
              </Pill>
            </div>
            <p className="caption" style={{ margin: "8px 0 0" }}>
              {configured
                ? `通勤检查完成后，会通过 ${data.base_url_host} 发送提醒。密钥只保存在账号中，不会再次显示。`
                : "绑定后，通勤检查完成时会发一条提醒到你的 Bark 客户端。"}
            </p>
            {configured && data.available === false ? (
              <p className="caption amber" style={{ margin: "6px 0 0" }}>
                暂时无法发送 Bark 提醒，请稍后再试。
              </p>
            ) : null}
            {data.last_test_at ? (
              <p className="micro" style={{ margin: "6px 0 0" }}>
                上次测试：{data.last_test_status || "未知"} · {formatDateTime(data.last_test_at)}
              </p>
            ) : null}
          </div>

          <div className="card mt-14">
            <SwitchRow
              title="通勤自动提醒"
              detail={
                configured
                  ? "每天最多两条自动提醒；关闭后只停止后续提醒。"
                  : "先绑定 Key 才能开启自动提醒。"
              }
              checked={auto}
              disabled={!configured}
              busy={busy === "toggle"}
              onChange={() => void toggleAuto()}
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
                  需要你明确确认；不会因为打开这个页面就自动发送
                </span>
              </span>
              <ChevronRight size={16} className="chevron" aria-hidden="true" />
            </button>
          </div>

          {testResult ? (
            <div className={`card mt-14 ${testResult.accepted ? "card--green" : "card--amber"}`}>
              <p className="card-title" style={{ fontSize: 15 }}>
                {testResult.status === "accepted"
                  ? "Bark 已接受这次请求"
                  : testResult.status === "failed"
                    ? "测试发送失败"
                    : "测试结果暂不确定"}
              </p>
              <p className="caption" style={{ margin: "4px 0 0" }}>
                {testResult.status === "accepted"
                  ? "接受请求不等于手机已经收到；以 Bark 客户端实际显示为准。"
                  : testResult.message}
              </p>
              <p className="micro" style={{ margin: "6px 0 0" }}>
                {formatDateTime(testResult.tested_at)}
                {testResult.status === "unknown" ? " · 不要自动重发，先核对客户端" : ""}
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
              {configured ? "替换 device key" : "绑定 Bark"}
            </p>
            <p className="caption" style={{ margin: "4px 0 10px" }}>
              密钥只会提交到账号，不会在页面回显。
            </p>
            <label className="field" htmlFor="bark-key">
              <input
                id="bark-key"
                className="input"
                type="password"
                value={key}
                onChange={(event) => setKey(event.target.value)}
                placeholder="粘贴 Bark device key"
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
              {busy === "bind" ? "正在保存…" : configured ? "替换 Key" : "保存绑定"}
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

          <Notice tone="amber" title="不要把这当成送达回执">
            这里只保留最近一次测试结果，实际送达以手机通知为准。
          </Notice>
        </>
      ) : null}

      <ConfirmDialog
        open={confirmUnbind}
        title="解除 Bark 绑定？"
        body="解除后不会再接收通勤提醒；已有通勤计划保留。"
        confirmLabel="解除绑定"
        danger
        busy={busy === "unbind"}
        onCancel={() => setConfirmUnbind(false)}
        onConfirm={() => void unbind()}
      />

      <ConfirmDialog
        open={confirmTest}
        title="发送测试提醒？"
        body="这会通过 Bark 真实发送一条通知。接受请求不代表手机一定收到。"
        confirmLabel="发送测试"
        busy={busy === "test"}
        onCancel={() => setConfirmTest(false)}
        onConfirm={() => void sendTest()}
      />
    </PageShell>
  );
}
