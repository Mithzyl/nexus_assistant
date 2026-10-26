"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowUp,
  Cloud,
  Copy,
  WifiOff,
  XCircle,
} from "lucide-react";

import type { AskItem } from "@/components/mobile/ask-store";
import { isVisibleChat, useAskStore } from "@/components/mobile/ask-store";
import { Markdown } from "@/components/mobile/markdown";
import { useAuth } from "@/components/mobile/providers";
import { LoadingCard, ScreenHeader } from "@/components/mobile/ui";
import { formatDateTime } from "@/lib/mobile/format";
import { readSession, writeSession } from "@/lib/mobile/storage";

const SUGGESTIONS = [
  "咖啡机退款到哪一步了？",
  "明晚看电影要带什么？",
  "柳江边散步安排在哪天？",
  "旅行准备到哪一步了？",
];

function StatusCard({
  item,
  onRetry,
  onCancel,
  onRefresh,
  onReuse,
  onContinue,
  onRecheck,
  copied,
  onCopy,
}: {
  item: AskItem;
  onRetry: () => void;
  onCancel: () => void;
  onRefresh: () => void;
  onReuse: () => void;
  onContinue: () => void;
  onRecheck: () => void;
  copied: boolean;
  onCopy: () => void;
}) {
  // A pre-separation local record has no trustworthy body until the server
  // classifies it. Never render its question, answer or retry action.
  if (item.classification === "pending") {
    return (
      <div className="card card--tight">
        <p className="micro" style={{ margin: 0 }}>
          正在向服务器核对此前保存的记录；确认属于聊天后才会显示正文。
        </p>
        <button type="button" className="link-button mt-6" onClick={onRecheck}>
          重新核对
        </button>
      </div>
    );
  }

  if (item.classification === "unknown") {
    return (
      <div className="card card--amber">
        <p className="card-title amber" style={{ fontSize: 15 }}>
          这条本机记录尚未确认
        </p>
        <p className="caption" style={{ margin: "4px 0 0" }}>
          服务器暂时无法确认它是否属于聊天。为安全起见，这里不显示正文，也不会把它作为新问题再次提交。
        </p>
        <button type="button" className="link-button mt-6" onClick={onRecheck}>
          重新核对
        </button>
      </div>
    );
  }

  if (item.phase === "submitting") {
    return (
      <div className="card card--tight">
        <div className="ask-status">
          <span className="spinner" />
          正在提交给服务器…
        </div>
      </div>
    );
  }

  if (item.phase === "submissionUncertain") {
    return (
      <div className="card card--amber">
        <p className="card-title amber" style={{ fontSize: 15 }}>
          提交结果暂不确定
        </p>
        <p className="caption" style={{ margin: "4px 0 0" }}>
          请求可能已经到达服务器，也可能没有。重试会复用同一个请求键，不会创建第二次执行。
        </p>
        <button type="button" className="link-button mt-6" onClick={onRetry}>
          重试同一次提交
        </button>
      </div>
    );
  }

  if (item.phase === "queued" || item.phase === "running") {
    return (
      <div className="card card--tight">
        <div className="ask-status">
          <span className="spinner" />
          {item.phase === "queued" ? "已提交，等待服务器开始" : "服务器正在处理"}
        </div>
        <p className="caption" style={{ margin: "6px 0 0" }}>
          最终回答由服务器保存；离开页面不会取消执行。
        </p>
        <div className="ask-actions">
          {item.serverId ? (
            <button type="button" className="link-button" onClick={onCancel}>
              取消
            </button>
          ) : null}
          <button type="button" className="link-button" onClick={onRefresh}>
            刷新状态
          </button>
        </div>
      </div>
    );
  }

  if (item.phase === "succeeded") {
    return (
      <div className="card">
        {item.answer ? (
          <Markdown source={item.answer} />
        ) : (
          <>
            <p className="card-title amber" style={{ fontSize: 15 }}>
              服务器报告已完成，但没有返回可读回答。
            </p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              可以重新查询；如果仍然为空，这属于服务器结果异常。
            </p>
          </>
        )}
        <div className="flex items-center gap-10 mt-12" style={{ flexWrap: "wrap" }}>
          <span className="micro grow">
            完成于 {formatDateTime(item.completedAt) || "服务器未给出时间"}
          </span>
          {item.answer ? (
            <button type="button" className="link-button link-button--inline" onClick={onCopy}>
              <Copy size={12} /> {copied ? "已复制" : "复制回答"}
            </button>
          ) : null}
          <button type="button" className="link-button link-button--inline" onClick={onRefresh}>
            刷新
          </button>
        </div>
      </div>
    );
  }

  if (item.phase === "failed") {
    return (
      <div className="card card--red">
        <p className="card-title red" style={{ fontSize: 15 }}>
          这次没有完成
        </p>
        <p className="caption red" style={{ margin: "4px 0 0", opacity: 0.92 }}>
          {item.errorMessage || "服务器未能完成这次处理。"}
        </p>
        <div className="ask-actions">
          <button type="button" className="link-button" style={{ color: "var(--m-red)" }} onClick={onReuse}>
            用同一问题重新提问
          </button>
          <button type="button" className="link-button" style={{ color: "var(--m-red)" }} onClick={onRefresh}>
            刷新
          </button>
        </div>
      </div>
    );
  }

  if (item.phase === "cancelled") {
    return (
      <div className="card card--tight">
        <div className="ask-status">
          <XCircle size={17} className="muted" />
          <span className="muted">已取消，服务器确认后不会再产生回答。</span>
        </div>
      </div>
    );
  }

  return (
    <div className="card card--amber">
      <p className="card-title amber" style={{ fontSize: 15 }}>
        处理状态暂不确定
      </p>
      <p className="caption" style={{ margin: "4px 0 0" }}>
        服务器仍在核对结果。可以刷新查询；不要把它当作安全重试的信号。
      </p>
      {item.pollingPaused ? (
        <button type="button" className="link-button mt-6" onClick={onContinue}>
          继续核对
        </button>
      ) : (
        <button type="button" className="link-button mt-6" onClick={onRefresh}>
          刷新核对
        </button>
      )}
    </div>
  );
}

export default function AskPage() {
  const { state, user, renewing, renewMessage } = useAuth();
  const ask = useAskStore();
  const [draft, setDraft] = useState("");
  const [composerError, setComposerError] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const restored = useRef(false);

  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    const captured = readSession<string>("pa.web.ask.draft", "");
    if (captured) {
      setDraft(captured);
      writeSession("pa.web.ask.draft", "");
    } else {
      setDraft(readSession<string>("pa.web.ask.composer", ""));
    }
  }, []);

  useEffect(() => {
    writeSession("pa.web.ask.composer", draft);
  }, [draft]);

  useEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 132)}px`;
  }, [draft]);

  const newest = ask.items[0];

  useEffect(() => {
    if (!newest) return;
    const distanceToBottom =
      document.documentElement.scrollHeight - (window.scrollY + window.innerHeight);
    if (distanceToBottom < 360) {
      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
    }
    // Only new answers/recoveries should pull the view down, not every keystroke.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newest?.phase, newest?.answer]);

  const canSend = draft.trim().length > 0 && state === "signedIn" && !ask.submitting;

  const send = async (text?: string) => {
    const value = (text ?? draft).trim();
    if (!value) return;
    if (state !== "signedIn") {
      setComposerError("登录后才能向服务器提问，输入已保留。");
      return;
    }
    if (value.length > 4000) {
      setComposerError("问题最多 4000 个字符。");
      return;
    }
    setComposerError(null);
    setDraft("");
    writeSession("pa.web.ask.composer", "");
    const created = await ask.submit(value);
    if (!created) {
      setDraft(value);
    }
    window.setTimeout(() => {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }, 80);
  };

  const copyAnswer = async (item: AskItem) => {
    if (!item.answer) return;
    try {
      await navigator.clipboard.writeText(item.answer);
      setCopiedId(item.id);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch {
      setComposerError("复制失败，可以长按选择回答文字。");
    }
  };

  const timeline = [...ask.items].reverse();

  return (
    <div className="m-screen" style={{ paddingBottom: 220 }}>
      <ScreenHeader title="问助手" subtitle="连接 PA 服务器 · 每次提问独立处理" />

      <section style={{ padding: "0 var(--m-gutter)" }}>
        {state === "signedIn" && user ? (
          <div className="flex items-center gap-6" style={{ marginBottom: 16 }}>
            <Cloud size={14} className="fog" />
            <span className="caption" style={{ margin: 0 }}>
              已登录 {user.display_name} · 回答来自 PA 服务器
            </span>
          </div>
        ) : null}

        {state === "offline" ? (
          <div className="flex items-center gap-6" style={{ marginBottom: 16, color: "var(--m-amber)" }}>
            <WifiOff size={14} />
            <span className="caption" style={{ margin: 0, color: "var(--m-amber)" }}>
              离线：保留本机问答记录，暂时不能发送。
            </span>
            <button type="button" className="link-button link-button--inline" onClick={() => void ask.refreshHistory()}>
              重试
            </button>
          </div>
        ) : null}

        {state === "anonymous" ? (
          <div className="card card--amber" style={{ marginBottom: 16 }}>
            <p className="card-title amber" style={{ fontSize: 15 }}>
              登录后才能向服务器提问
            </p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              未登录时仍可浏览本机已有内容；输入会保留。
            </p>
            <Link href="/login" className="link-button mt-6">
              登录 PA 账号
            </Link>
          </div>
        ) : null}

        {renewMessage ? (
          <p className="micro" style={{ marginBottom: 12 }}>
            {renewing ? "正在向服务器续期…" : renewMessage}
          </p>
        ) : null}

        {ask.historyError ? (
          <p className="micro" style={{ marginBottom: 12, color: "var(--m-red)" }}>
            {ask.historyError}
          </p>
        ) : null}
        {ask.verificationError ? (
          <p className="micro" style={{ marginBottom: 12, color: "var(--m-amber)" }}>
            {ask.verificationError}
          </p>
        ) : null}
      </section>

      {timeline.length === 0 ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <div className="card">
            <p className="card-title" style={{ fontSize: 18 }}>
              问一句，交给服务器处理。
            </p>
            <p className="body-text muted mt-8">
              提问会提交到 PA 后端，由服务器等待并保存最终回答。这里不会伪造打字机效果，也不会连接上游服务。
            </p>
            <div className="mt-12" style={{ display: "grid", gap: 8 }}>
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => void send(suggestion)}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    gap: 8,
                    minHeight: 44,
                    padding: "10px 12px",
                    border: 0,
                    borderRadius: 12,
                    background: "color-mix(in srgb, var(--m-mist) 65%, transparent)",
                    color: "var(--m-fog-deep)",
                    fontSize: 14,
                    textAlign: "left",
                  }}
                >
                  {suggestion}
                  <span className="faint" aria-hidden="true">↗</span>
                </button>
              ))}
            </div>
            {ask.loadingHistory ? (
              <p className="micro mt-12" style={{ marginBottom: 0 }}>
                正在读取历史记录…
              </p>
            ) : null}
          </div>
        </section>
      ) : (
        <div className="ask-stream">
          {timeline.map((item) => {
            const concealed = !isVisibleChat(item);
            return (
              <div key={item.id}>
                <div
                  className="bubble-user"
                  style={
                    concealed
                      ? { fontStyle: "italic", opacity: 0.72 }
                      : undefined
                  }
                >
                  {concealed
                    ? item.classification === "pending"
                      ? "正在核对此前保存的记录…"
                      : "这条本机记录暂不显示内容"
                    : item.question}
                </div>
                <div className="mt-10">
                  <StatusCard
                    item={item}
                    copied={copiedId === item.id}
                    onCopy={() => void copyAnswer(item)}
                    onRetry={() => void ask.retrySubmission(item.id)}
                    onCancel={() => void ask.cancel(item.id)}
                    onRefresh={() => {
                      if (item.pollingPaused) ask.hidePaused(item.id);
                      void ask.refreshOne(item.id);
                    }}
                    onContinue={() => {
                      ask.hidePaused(item.id);
                      void ask.refreshOne(item.id);
                    }}
                    onRecheck={() => void ask.recheckItem(item.id)}
                    onReuse={() => {
                      if (!isVisibleChat(item)) return;
                      setDraft(item.question);
                      inputRef.current?.focus();
                      window.scrollTo({ top: document.body.scrollHeight, behavior: "smooth" });
                    }}
                  />
                </div>
              </div>
            );
          })}
          {ask.loadingHistory ? <LoadingCard label="正在同步服务器历史…" /> : null}
          <div ref={endRef} />
        </div>
      )}

      <section style={{ padding: "0 var(--m-gutter)", marginTop: 28 }}>
        <div style={{ display: "grid", gap: 6 }}>
          <span className="micro">关于这版回答</span>
          <p className="caption" style={{ margin: 0 }}>
            每次提问独立发送，当前接口没有会话上下文，不会自动带上上一轮。回答与完成时间来自 PA 服务器保存的结果；这里不展示上游服务名、工作流或调试信息。
          </p>
          <p className="caption" style={{ margin: 0 }}>
            日历、提醒事项和笔记目前只读取已同步到服务器的副本；浏览器不能声称获得了系统读取权限。
          </p>
        </div>
      </section>

      {composerError ? (
        <p className="composer-hint" style={{ position: "fixed", bottom: "calc(112px + env(safe-area-inset-bottom))", left: "50%", transform: "translateX(-50%)", width: "100%", maxWidth: 480, zIndex: 36 }} role="alert">
          {composerError}
        </p>
      ) : null}

      <div className="composer">
        <textarea
          ref={inputRef}
          className="composer__field"
          rows={1}
          value={draft}
          onChange={(event) => {
            setDraft(event.target.value);
            setComposerError(null);
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              if (canSend) void send();
            }
          }}
          placeholder={state === "signedIn" ? "问一件已经记下的事" : "登录后才能向服务器提问"}
          aria-label="向助手提问"
          maxLength={4000}
        />
        <button
          type="button"
          className="composer__send"
          disabled={!canSend}
          onClick={() => void send()}
          aria-label="发送问题"
        >
          {ask.submitting ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <ArrowUp size={17} strokeWidth={2.4} />}
        </button>
      </div>
    </div>
  );
}
