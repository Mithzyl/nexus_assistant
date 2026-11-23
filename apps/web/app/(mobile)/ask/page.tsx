"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowRight,
  ArrowUp,
  Cloud,
  CheckCircle2,
  Copy,
  WifiOff,
  XCircle,
} from "lucide-react";

import { MemoryWriteReceipts } from "@/components/mobile/memory-write-receipts";
import type { AskItem } from "@/components/mobile/ask-store";
import { isVisibleChat, useAskStore } from "@/components/mobile/ask-store";
import { Markdown } from "@/components/mobile/markdown";
import { useAuth, useUI } from "@/components/mobile/providers";
import { LoadingCard, LoginRequired, ScreenHeader } from "@/components/mobile/ui";
import { MemoryCandidates, type SelectedMemory } from "@/components/mobile/account-memory";
import { MatterDraftSheet } from "@/components/mobile/matter-draft-sheet";
import { ApiOperationCard } from "@/components/mobile/api-operation-card";
import type {
  IntakePendingContext,
  IntakeStage,
  IntakeScheduleCandidate,
  MatterDraft,
  MatterIntakeResult,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  confirmSummary,
  dismissIntakeReply,
  getIntakeResult,
  getIntakeStatus,
  streamIntakeMatter,
  IntakeStreamError,
  listDrafts,
  listIntakeResults,
  newOperationKey,
  requestTodayRefresh,
  TODAY_REFRESH_EVENT,
} from "@/lib/mobile/matters-api";
import { mergeIntakeHistory } from "@/lib/mobile/matter-draft.mjs";
import { formatDateTime } from "@/lib/mobile/format";
import {
  accountKey,
  readJson,
  readSession,
  writeJson,
  writeSession,
} from "@/lib/mobile/storage";

interface IntakeAnswer {
  id: string;
  localRunId?: string | null;
  question: string;
  answer: string;
  createdAt: string;
  source?: "chat" | "follow_up";
  pendingContext?: IntakePendingContext | null;
}

interface IntakeRequestKey {
  text: string;
  key: string;
  continuationRunId: string | null;
  ignorePendingReply?: boolean;
  dismissedReplyRunId?: string | null;
}

const intakeStageLabel: Partial<Record<IntakeStage, string>> = {
  queued: "已记下，等待后台整理",
  understanding: "正在理解需求与相关记忆…",
  fetching_schema: "正在读取接口说明…",
  executing_tool: "正在查询或校验保存…",
  generating_reply: "正在整理回复…",
};

/** Prefer the persisted stage while keeping a fallback for older APIs. */
function progressLabel(stage: IntakeStage | null | undefined): string {
  return (stage && intakeStageLabel[stage]) || "助手正在处理…";
}

/** Turn a server schedule candidate into a continuation message without IDs. */
function scheduleCandidatePrompt(candidate: IntakeScheduleCandidate): string {
  const when = formatDateTime(candidate.starts_at);
  const end = candidate.ends_at ? formatDateTime(candidate.ends_at) : null;
  return `选择「${candidate.title}」${when ? `（${when}${end ? ` 至 ${end}` : ""})` : ""}${
    candidate.location ? `，地点：${candidate.location}` : ""
  }`;
}

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
          正在核对刚才的记录，确认后就能看到。
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
          这条还没确认归属
        </p>
        <p className="caption" style={{ margin: "4px 0 0" }}>
          还没能确认它是不是问答，先不显示正文。
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
          正在提交…
        </div>
      </div>
    );
  }

  if (item.phase === "submissionUncertain") {
    return (
      <div className="card card--amber">
        <p className="card-title amber" style={{ fontSize: 15 }}>
          还没收到结果
        </p>
        <p className="caption" style={{ margin: "4px 0 0" }}>
          连接中断了；再点一次会在原请求上继续，不会重复提交。
        </p>
        <button type="button" className="link-button mt-6" onClick={onRetry}>
          继续这次提交
        </button>
      </div>
    );
  }

  if (item.phase === "queued" || item.phase === "running") {
    return (
      <div className="card card--tight">
        <div className="ask-status">
          <span className="spinner" />
          {item.phase === "queued" ? "已提交，助手马上开始" : "助手正在处理"}
        </div>
        <p className="caption" style={{ margin: "6px 0 0" }}>
          回答会保存到账号，离开页面也会继续。
        </p>
        <div className="ask-actions">
          {item.serverId ? (
            <button type="button" className="link-button" onClick={onCancel}>
              取消
            </button>
          ) : null}
          <button type="button" className="link-button" onClick={onRefresh}>
            刷新进度
          </button>
        </div>
      </div>
    );
  }

  if (item.phase === "succeeded") {
    return (
      <div className="card ask-message">
        <div className="ask-message__meta">
          <span className="ask-message__ok" aria-label="直接回答" title="直接回答">
            <CheckCircle2 size={13} />
          </span>
          <span className="ask-message__time">{formatDateTime(item.completedAt) || "时间未知"}</span>
        </div>
        <div className="ask-message__body">
          {item.answer ? (
            <Markdown source={item.answer} />
          ) : (
            <>
              <p className="card-title amber" style={{ fontSize: 15 }}>
                这次没有回答内容
              </p>
              <p className="caption" style={{ margin: "4px 0 0" }}>
                改一下再发一次。
              </p>
            </>
          )}
          <div className="ask-message__actions">
            {item.answer ? (
              <button type="button" className="ask-message__action" onClick={onCopy}>
                <Copy size={12} /> {copied ? "已复制" : "复制"}
              </button>
            ) : null}
            <button type="button" className="ask-message__action" onClick={onRefresh}>
              刷新
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (item.phase === "failed") {
    return (
      <div className="card ask-message ask-message--failed">
        <div className="ask-message__meta">
          <span className="ask-message__time">{formatDateTime(item.completedAt) || "时间未知"}</span>
        </div>
        <div className="ask-message__body">
          <p className="ask-failure__title">这次没处理完</p>
          <p className="ask-failure__body">
            你刚才那条「{item.question}」我收到了，但这次没能跑完。内容我先存着。
          </p>
          <div className="ask-failure__actions">
            <button type="button" className="btn btn--secondary btn--small" onClick={onReuse}>
              重新编辑
            </button>
            <button type="button" className="ask-message__action" onClick={onRefresh}>
              刷新
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (item.phase === "cancelled") {
    return (
      <div className="card card--tight">
        <div className="ask-status">
          <XCircle size={17} className="muted" />
          <span className="muted">已取消，不会再产生回答。</span>
        </div>
      </div>
    );
  }

  return (
    <div className="card card--amber">
      <p className="card-title amber" style={{ fontSize: 15 }}>
        还在确认结果
      </p>
      <p className="caption" style={{ margin: "4px 0 0" }}>
        结果还在确认，可以刷新看看。
      </p>
      {item.pollingPaused ? (
        <button type="button" className="link-button mt-6" onClick={onContinue}>
          继续确认
        </button>
      ) : (
        <button type="button" className="link-button mt-6" onClick={onRefresh}>
          刷新
        </button>
      )}
    </div>
  );
}

/** Restore intake replies and cards, placing clarification choices after their supporting card. */
export default function AskPage() {
  const { state, user, renewing, renewMessage } = useAuth();
  const ask = useAskStore();
  const [draft, setDraft] = useState("");
  const [composerError, setComposerError] = useState<string | null>(null);
  const [selectedMemories, setSelectedMemories] = useState<SelectedMemory[]>([]);
  const [memoryRefresh, setMemoryRefresh] = useState(0);
  const [intakeDraft, setIntakeDraft] = useState<MatterDraft | null>(null);
  const [pendingDrafts, setPendingDrafts] = useState<MatterDraft[]>([]);
  const [liveTurn, setLiveTurn] = useState<{ question: string; answer: string } | null>(null);
  const [intakeAnswers, setIntakeAnswers] = useState<IntakeAnswer[]>([]);
  const [serverIntakeResults, setServerIntakeResults] = useState<MatterIntakeResult[]>([]);
  const [processing, setProcessing] = useState(false);
  const [trackedKey, setTrackedKey] = useState<string | null>(null);
  const [trackedResult, setTrackedResult] = useState<MatterIntakeResult | null>(null);
  const [linkedReply, setLinkedReply] = useState<MatterIntakeResult | null>(null);
  const recovering = !processing && trackedResult?.outcome === "processing";
  const [supplementQuestion, setSupplementQuestion] = useState<string | null>(null);
  const [clarificationContext, setClarificationContext] =
    useState<IntakePendingContext>({});
  const [operationFailure, setOperationFailure] = useState<string | null>(null);
  const [continuationRunId, setContinuationRunId] = useState<string | null>(null);
  const [replyExpiresAt, setReplyExpiresAt] = useState<string | null>(null);
  const [dismissedReply, setDismissedReply] = useState<string | boolean>(false);
  const ignorePendingReply = Boolean(dismissedReply);
  const [dismissingReply, setDismissingReply] = useState(false);
  const [replyClock, setReplyClock] = useState(() => Date.now());
  const [recoveryRunId, setRecoveryRunId] = useState<string | null>(null);
  const [intakeHistoryLoading, setIntakeHistoryLoading] = useState(false);
  const intakeKey = useRef<IntakeRequestKey | null>(null);
  const hydratedAnswerKey = useRef<string | null>(null);
  const intakeHistorySeq = useRef(0);
  const { toast } = useUI();
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const latestIntakeRef = useRef<HTMLDivElement>(null);
  const restoredComposerKey = useRef<string | null>(null);
  const intakeAnswerStorageKey = accountKey("pa.web.intakeAnswers", user?.user_id ?? null);
  const composerStorageKey = accountKey("pa.web.ask.composer", user?.user_id ?? null);
  const intakeRequestStorageKey = accountKey("pa.web.ask.intakeRequest", user?.user_id ?? null);
  const dismissedReplyStorageKey = accountKey("pa.web.ask.ignorePendingReply", user?.user_id ?? null);
  const latestReply = serverIntakeResults.find((item) => item.reply_state === "awaiting_reply");
  const currentReply = continuationRunId
    ? serverIntakeResults.find((item) => item.local_run_id === continuationRunId)
    : latestReply;
  const closedReply = currentReply?.reply_state && currentReply.reply_state !== "awaiting_reply";
  const expiresAt = continuationRunId ? replyExpiresAt : currentReply?.reply_expires_at;
  const isExpired = Boolean(expiresAt && Date.parse(expiresAt) <= replyClock);
  const automaticReplyRunId = !closedReply && !isExpired && !ignorePendingReply
    ? continuationRunId ?? (currentReply?.reply_state === "awaiting_reply" ? (currentReply.continuation_run_id ?? currentReply.local_run_id) : null)
    : null;
  const activeReplyRunId = linkedReply?.local_run_id ?? automaticReplyRunId;
  const activeReplyQuestion = linkedReply?.clarification_summary ?? linkedReply?.clarify ??
    (activeReplyRunId ? supplementQuestion ?? currentReply?.clarification_summary ?? currentReply?.clarify : null);
  const activeReplyContext = linkedReply?.pending_context ??
    (continuationRunId ? clarificationContext : currentReply?.pending_context ?? {});

  useEffect(() => {
    const now = Date.now();
    setReplyClock(now);
    if (!expiresAt || Date.parse(expiresAt) <= now) return;
    const timer = window.setTimeout(() => setReplyClock(Date.now()), Date.parse(expiresAt) - now + 25);
    return () => window.clearTimeout(timer);
  }, [expiresAt]);

  useEffect(() => {
    restoredComposerKey.current = null;
    const captured = readSession<string>("pa.web.ask.draft", "");
    if (captured) {
      setDraft(captured);
      writeSession("pa.web.ask.draft", "");
    } else {
      setDraft(readSession<string>(composerStorageKey, ""));
    }
    const timer = window.setTimeout(() => {
      restoredComposerKey.current = composerStorageKey;
    }, 0);
    return () => window.clearTimeout(timer);
  }, [composerStorageKey, user?.user_id]);

  useEffect(() => {
    if (restoredComposerKey.current !== composerStorageKey) return;
    writeSession(composerStorageKey, draft);
  }, [composerStorageKey, draft]);

  useEffect(() => {
    hydratedAnswerKey.current = null;
    const stored = readJson<IntakeAnswer[]>(intakeAnswerStorageKey, []);
    setIntakeAnswers(stored);
    const timer = window.setTimeout(() => {
      hydratedAnswerKey.current = intakeAnswerStorageKey;
    }, 0);
    return () => window.clearTimeout(timer);
  }, [intakeAnswerStorageKey]);

  useEffect(() => {
    if (hydratedAnswerKey.current !== intakeAnswerStorageKey) return;
    writeJson(intakeAnswerStorageKey, intakeAnswers.slice(0, 20));
  }, [intakeAnswerStorageKey, intakeAnswers]);

  const refreshServerIntake = useCallback(async (signal?: AbortSignal) => {
    if (state !== "signedIn") return;
    const requestId = ++intakeHistorySeq.current;
    setIntakeHistoryLoading(true);
    const [draftResult, historyResult] = await Promise.allSettled([
      listDrafts("preview", null, signal),
      listIntakeResults(20, signal),
    ]);
    if (!signal?.aborted && requestId === intakeHistorySeq.current) {
      if (draftResult.status === "fulfilled") {
        setPendingDrafts(draftResult.value);
      }
      if (historyResult.status === "fulfilled") {
        setServerIntakeResults(historyResult.value);
      }
      setIntakeHistoryLoading(false);
    }
  }, [state]);

  useEffect(() => {
    const controller = new AbortController();
    intakeHistorySeq.current += 1;
    setServerIntakeResults([]);
    setReplyExpiresAt(null);
    setDismissedReply(state === "signedIn" && readSession<string | boolean>(dismissedReplyStorageKey, false));
    setPendingDrafts([]);
    setIntakeDraft(null);
    setSupplementQuestion(null);
    setClarificationContext({});
    setOperationFailure(null);
    setContinuationRunId(null);
    setRecoveryRunId(null);
    setLinkedReply(null);
    setTrackedResult(null);
    intakeKey.current = state === "signedIn"
      ? readSession<IntakeRequestKey | null>(intakeRequestStorageKey, null)
      : null;
    setTrackedKey(intakeKey.current?.key ?? null);
    if (state === "signedIn") {
      void refreshServerIntake(controller.signal);
    } else {
      setIntakeHistoryLoading(false);
    }
    return () => controller.abort();
  }, [dismissedReplyStorageKey, intakeRequestStorageKey, refreshServerIntake, state, user?.user_id]);

  useEffect(() => {
    if (state !== "signedIn") return;
    const requested = new URLSearchParams(window.location.search).get("reply");
    if (!requested) return;
    const controller = new AbortController();
    getIntakeResult(requested, controller.signal).then((result) => {
      if (!controller.signal.aborted && (result.reply_state === "awaiting_reply" || result.reply_state === "expired")) {
        setLinkedReply(result);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setComposerError("找不到可回复的随手记。");
    });
    return () => controller.abort();
  }, [state, user?.user_id]);

  useEffect(() => {
    if (!trackedKey || state !== "signedIn") return;
    const controller = new AbortController();
    let timer: number | undefined;
    const poll = async () => {
      let again = true;
      try {
        const result = await getIntakeStatus(trackedKey, controller.signal);
        if (controller.signal.aborted) return;
        setTrackedResult(result);
        if (result.outcome !== "processing") {
          again = false;
          if (!processing && intakeKey.current?.key === trackedKey) {
            setRecoveryRunId(result.local_run_id);
            if (["answer", "needs_clarification", "draft"].includes(result.outcome)) {
              const submitted = intakeKey.current.text;
              setDraft((value) => value.trim() === submitted ? "" : value);
              if (readSession<string>(composerStorageKey, "").trim() === submitted) writeSession(composerStorageKey, "");
              intakeKey.current = null;
              writeSession(intakeRequestStorageKey, null);
              setDismissedReply(false);
              writeSession(dismissedReplyStorageKey, false);
              setOperationFailure(null);
              setLinkedReply(null);
              requestTodayRefresh();
            } else {
              setOperationFailure(result.outcome === "unknown"
                ? "暂未确认保存结果。原文还在，可以查看原结果。"
                : "这次处理未完成，原文还在。");
            }
            setServerIntakeResults((items) => [
              result, ...items.filter((item) => item.local_run_id !== result.local_run_id),
            ].slice(0, 20));
            setTrackedKey(null);
            setTrackedResult(null);
            await refreshServerIntake();
          }
        }
      } catch (caught) {
        // A disconnected initial request may never have reached the server.
        // Keep its saved key and text so an explicit retry can reuse both.
        if (caught instanceof ApiError && caught.status === 404 && !processing) again = false;
      } finally {
        if (again && !controller.signal.aborted) timer = window.setTimeout(poll, 600);
      }
    };
    void poll();
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [composerStorageKey, dismissedReplyStorageKey, intakeRequestStorageKey, processing, refreshServerIntake, state, trackedKey]);

  const hasPendingCapture = serverIntakeResults.some(
    (item) => item.entry_mode === "async_capture" && item.outcome === "processing",
  );
  useEffect(() => {
    if (!hasPendingCapture || state !== "signedIn") return;
    const interval = window.setInterval(() => void refreshServerIntake(), 2000);
    return () => window.clearInterval(interval);
  }, [hasPendingCapture, refreshServerIntake, state]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible" && state === "signedIn") {
        void refreshServerIntake();
      }
    };
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    window.addEventListener(TODAY_REFRESH_EVENT, refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
      window.removeEventListener(TODAY_REFRESH_EVENT, refresh);
    };
  }, [refreshServerIntake, state]);

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

  const canSend = draft.trim().length > 0 && state === "signedIn" && !processing && !recovering &&
    !dismissingReply && (!intakeHistoryLoading || serverIntakeResults.length > 0);

  /** Dismiss the displayed clarification and treat the next typed submission as a new request. */
  const dismissReply = async () => {
    if (!activeReplyRunId || dismissingReply || processing) return;
    setDismissingReply(true);
    setComposerError(null);
    try {
      await dismissIntakeReply(activeReplyRunId);
      setDismissedReply(activeReplyRunId);
      setLinkedReply(null);
      if (new URLSearchParams(window.location.search).has("reply")) window.history.replaceState(window.history.state, "", "/ask");
      writeSession(dismissedReplyStorageKey, activeReplyRunId);
      setContinuationRunId(null);
      setReplyExpiresAt(null);
      setSupplementQuestion(null);
      setClarificationContext({});
      await refreshServerIntake();
    } catch (caught) {
      setComposerError(caught instanceof Error ? caught.message : "暂时无法取消这条追问。");
    } finally {
      setDismissingReply(false);
    }
  };

  /** Submit through the existing intake and retain the original draft until a usable result arrives. */
  const send = useCallback(async (
    text?: string,
    continuationOverride?: string | null,
  ) => {
    const original = text ?? draft;
    const value = original.trim();
    const retry = continuationOverride === undefined && intakeKey.current?.text === value
      ? intakeKey.current
      : null;
    const expiredBeforeSend = Boolean(!linkedReply && expiresAt && Date.parse(expiresAt) <= Date.now());
    const activeContinuation = retry ? retry.continuationRunId
      : continuationOverride !== undefined ? continuationOverride
        : expiredBeforeSend ? null : activeReplyRunId;
    const skipPendingReply = retry ? Boolean(retry.ignorePendingReply) : !activeContinuation && ignorePendingReply;
    const dismissedReplyRunId = retry ? retry.dismissedReplyRunId
      : skipPendingReply && typeof dismissedReply === "string" ? dismissedReply : null;
    if (!value || processing || recovering || dismissingReply || (intakeHistoryLoading && !serverIntakeResults.length)) return;
    if (state !== "signedIn") {
      setComposerError("登录后就能交给助手，内容先留着。");
      return;
    }
    if (value.length > 4000) {
      setComposerError("这段有点长，最多 4000 个字。");
      return;
    }
    setComposerError(null);
    setSupplementQuestion(null);
    setClarificationContext({});
    setOperationFailure(null);
    setProcessing(true);
    setLiveTurn({ question: value, answer: "" });
    setDraft(original);
    writeSession(composerStorageKey, original);
    if (
      !intakeKey.current ||
      intakeKey.current.text !== value ||
      intakeKey.current.continuationRunId !== activeContinuation ||
      Boolean(intakeKey.current.ignorePendingReply) !== skipPendingReply ||
      (intakeKey.current.dismissedReplyRunId ?? null) !== (dismissedReplyRunId ?? null)
    ) {
      intakeKey.current = {
        text: value,
        key: newOperationKey("web-intake"),
        continuationRunId: activeContinuation,
        ignorePendingReply: skipPendingReply,
        dismissedReplyRunId,
      };
    }
    const requestKey = intakeKey.current;
    setTrackedResult(null);
    setTrackedKey(requestKey.key);
    writeSession(intakeRequestStorageKey, requestKey);
    try {
      const result = await streamIntakeMatter(
        value,
        requestKey.key,
        activeContinuation,
        selectedMemories,
        (answer) => setLiveTurn({ question: value, answer }),
        skipPendingReply,
        dismissedReplyRunId,
      );
      if (
        (result.outcome === "answer" && result.answer) ||
        result.outcome === "needs_clarification" ||
        (result.outcome === "draft" && result.draft)
      ) {
        if (ignorePendingReply) {
          setDismissedReply(false);
          writeSession(dismissedReplyStorageKey, false);
        }
        setTrackedKey(null);
        setTrackedResult(null);
        if (linkedReply) {
          setLinkedReply(null);
          window.history.replaceState(window.history.state, "", "/ask");
          requestTodayRefresh();
        }
        const closedParent = result.parent_run_id;
        if (closedParent) {
          setServerIntakeResults((items) => items.map((item) => item.local_run_id === closedParent && item.reply_state
            ? { ...item, reply_state: "resolved" }
            : item));
        }
        setDraft((current) => current === original ? "" : current);
        if (readSession(composerStorageKey, "") === original) {
          writeSession(composerStorageKey, "");
        }
        if (intakeKey.current === requestKey) intakeKey.current = null;
        if (readSession<IntakeRequestKey | null>(intakeRequestStorageKey, null)?.key === requestKey.key) {
          writeSession(intakeRequestStorageKey, null);
        }
      }
      if (result.outcome === "answer" || result.draft) setSelectedMemories([]);
      if (result.outcome === "answer") {
        if (!result.answer) {
          setOperationFailure(
            result.message || "助手这次没答上来，内容还留着。",
          );
          return;
        }
        setIntakeAnswers((current) => [
          ...current,
          {
            id: `${Date.now()}-${value.slice(0, 20)}`,
            localRunId: result.local_run_id,
            question: value,
            answer: result.answer as string,
            createdAt: new Date().toISOString(),
            source: result.source ?? "chat",
            pendingContext: result.pending_context,
          },
        ].slice(-20));
        setContinuationRunId(null);
        setClarificationContext({});
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
      if (result.outcome === "needs_clarification") {
        setClarificationContext(result.pending_context ?? {});
        setSupplementQuestion(
          result.clarify || result.message || "再补一点信息就好。",
        );
        setContinuationRunId(result.reply_state && result.reply_state !== "awaiting_reply" ? null : result.local_run_id);
        setReplyExpiresAt(result.reply_expires_at ?? null);
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
      if (result.outcome === "processing" || result.outcome === "unknown") {
        setOperationFailure(
          "结果还在确认。内容留着，不会重复提交。",
        );
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
      if (result.outcome === "draft" && result.draft) {
        setIntakeDraft(result.draft);
        setContinuationRunId(null);
        setClarificationContext({});
        setRecoveryRunId(result.local_run_id);
        toast("已生成变更草稿，还没写入正式安排");
        void refreshServerIntake();
        return;
      }
      if (!result.draft) {
        setOperationFailure(
          result.message || "这次没整理出草稿，内容还留着。",
        );
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
    } catch (caught) {
      if (caught instanceof IntakeStreamError) {
        if (caught.localRunId) setRecoveryRunId(caught.localRunId);
        if (caught.workflowError.outcome_is_known) setComposerError(caught.message);
        else setOperationFailure(caught.message);
        void refreshServerIntake();
        return;
      }
      if (
        caught instanceof ApiError &&
        (caught.status >= 500 ||
          caught.code.includes("timeout") ||
          caught.code.includes("unknown"))
      ) {
        setOperationFailure(
          "结果还在确认。可以点“查看原结果”，或继续这次提问。",
        );
        return;
      }
      setComposerError(caught instanceof Error ? caught.message : "这次没发出去，稍后再试。");
      void refreshServerIntake();
    } finally {
      setProcessing(false);
      setLiveTurn(null);
    }
  }, [
    activeReplyRunId, composerStorageKey, dismissingReply, dismissedReply, dismissedReplyStorageKey, draft,
    expiresAt, ignorePendingReply, intakeHistoryLoading, intakeRequestStorageKey, linkedReply, processing, recovering,
    refreshServerIntake, selectedMemories, serverIntakeResults.length, state, toast,
  ]);

  const recoverResult = async () => {
    setOperationFailure(null);
    try {
      const result = intakeKey.current
        ? await getIntakeStatus(intakeKey.current.key)
        : recoveryRunId ? await getIntakeResult(recoveryRunId) : null;
      if (!result) {
        setOperationFailure(
          "还没找到这次的结果，原内容还留着。",
        );
        return;
      }
      setRecoveryRunId(result.local_run_id);
      if (result.outcome === "draft" && result.draft) {
        setIntakeDraft(result.draft);
        setContinuationRunId(null);
        setClarificationContext({});
        toast("已读取待确认结果");
      } else if (result.outcome === "needs_clarification") {
        setClarificationContext(result.pending_context ?? {});
        setSupplementQuestion(result.clarify || "再补一点信息就好。");
        setContinuationRunId(result.reply_state && result.reply_state !== "awaiting_reply" ? null : result.local_run_id);
        setReplyExpiresAt(result.reply_expires_at ?? null);
      } else if (result.outcome === "answer") {
        setContinuationRunId(null);
        setClarificationContext({});
        toast("回答已恢复到历史");
      } else if (result.outcome === "processing" || result.outcome === "unknown") {
        setOperationFailure("结果还在确认，稍后再看看。");
      } else {
        setOperationFailure("这次没有可继续的结果，内容还在。");
      }
      await refreshServerIntake();
    } catch (caught) {
      setOperationFailure(
        caught instanceof Error ? caught.message : "暂时查不到原结果。",
      );
    }
  };

  const copyAnswer = async (item: AskItem) => {
    if (!item.answer) return;
    try {
      await navigator.clipboard.writeText(item.answer);
      setCopiedId(item.id);
      window.setTimeout(() => setCopiedId(null), 1600);
    } catch {
      setComposerError("复制没成功，可以长按选中文字。");
    }
  };

  const timeline = [...ask.items].reverse();
  const unifiedHistory = mergeIntakeHistory(
    serverIntakeResults.filter((item) => !(liveTurn && trackedResult?.local_run_id === item.local_run_id && item.outcome === "processing"))
      .map((item) => trackedResult?.local_run_id === item.local_run_id && item.outcome === "processing" ? trackedResult : item)
      .filter((item) => item.outcome !== "draft" || item.confirm_status === "confirmed" || (item.target_lifecycle && item.target_lifecycle !== "active")),
    intakeAnswers,
  );

  useEffect(() => {
    if (liveTurn) latestIntakeRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [liveTurn?.question, liveTurn?.answer]);

  const latestIntake = unifiedHistory[unifiedHistory.length - 1];
  const latestIntakeId = latestIntake && ("local_run_id" in latestIntake ? latestIntake.local_run_id : latestIntake.localRunId || latestIntake.id);
  useEffect(() => {
    if (!latestIntakeId) return;
    latestIntakeRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [latestIntakeId, latestIntake?.answer, unifiedHistory.length]);

  return (
    <div className="m-screen" style={{ paddingBottom: 220 }}>
      <ScreenHeader title="问助手" subtitle="说一件事、一个问题或最新进展" />

      <section style={{ padding: "0 var(--m-gutter)" }}>
        {state === "signedIn" && user ? (
          <div className="flex items-center gap-6" style={{ marginBottom: 16 }}>
            <Cloud size={14} className="fog" />
            <span className="caption" style={{ margin: 0 }}>
              已登录 {user.display_name} · 问答会同步到账号
            </span>
          </div>
        ) : null}

        {state === "offline" ? (
          <div className="flex items-center gap-6" style={{ marginBottom: 16, color: "var(--m-amber)" }}>
            <WifiOff size={14} />
            <span className="caption" style={{ margin: 0, color: "var(--m-amber)" }}>
              当前离线，可以继续查看已有问答。
            </span>
            <button type="button" className="link-button link-button--inline" onClick={() => void ask.refreshHistory()}>
              重试
            </button>
          </div>
        ) : null}

        {state === "anonymous" ? (
          <div style={{ marginBottom: 16 }}>
            <LoginRequired
              title="登录生活助手"
              body="跨设备同步你的问答与处理结果。"
            />
          </div>
        ) : null}

        {renewMessage ? (
          <p className="micro" style={{ marginBottom: 12 }}>
            {renewing ? "正在续期…" : renewMessage}
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

      {state === "signedIn" ? <section style={{padding: "0 var(--m-gutter)", marginBottom: 16}}><MemoryCandidates refresh={memoryRefresh} /></section> : null}

      {pendingDrafts.length > 0 ? (
        <section style={{ padding: "0 var(--m-gutter)", marginBottom: 16 }}>
          <div className="card card--amber">
            <p className="card-title amber" style={{ fontSize: 15 }}>
              有 {pendingDrafts.length} 条待确认变更
            </p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              从账号同步，重新打开也不会重复出现。
            </p>
            <div className="m-stack-8 mt-10">
              {pendingDrafts.slice(0, 3).map((draftItem) => (
                <button key={draftItem.draft_id} type="button" className="btn btn--secondary" onClick={() => setIntakeDraft(draftItem)}>
                  {draftItem.payload.title ||
                    draftItem.payload.schedule?.title ||
                    draftItem.payload.todos?.[0]?.title ||
                    "待确认整理"}
                  {draftItem.candidates?.length ? " · 需要选择事项" : ""}
                </button>
              ))}
            </div>
          </div>
        </section>
      ) : null}

      {unifiedHistory.length > 0 ? (
        <section className="ask-stream" style={{ marginBottom: 16 }}>
          {unifiedHistory.map((item, index) => {
            const serverItem = "request_text" in item;
            const question = serverItem ? item.request_text : item.question;
            const answer = serverItem ? item.answer : item.answer;
            const clarify = serverItem ? item.clarify : null;
            const pendingContext = (
              serverItem ? item.pending_context : item.pendingContext ?? null
            ) as IntakePendingContext | null;
            const serverRunId = serverItem ? (item.continuation_run_id ?? item.local_run_id) : null;
            const scheduleCandidates: IntakeScheduleCandidate[] =
              pendingContext?.schedule_candidates ?? [];
            const hasContinuation = serverItem && (item.reply_state
              ? item.reply_state === "resolved" || item.reply_state === "cancelled"
              : serverIntakeResults.some((result) => result.parent_run_id === item.local_run_id));
            const outcome = serverItem ? item.outcome : "answer";
            const lifecycle = serverItem ? item.target_lifecycle : null;
            const confirmed = serverItem && item.confirm_status === "confirmed";
            const inactivePreview = serverItem && item.confirm_status === "unconfirmed" && lifecycle && lifecycle !== "active";
            const isFailure = outcome === "failed" || outcome === "unknown";
            const historyNote = confirmed
              ? lifecycle === "archived" ? `所属事项「${item.target_title}」现已归档`
                : lifecycle === "deleted" ? "所属事项后来已被删除，相关安排不再保留"
                : lifecycle === "source_revoked" ? "相关资料来源已取消授权，当前不再展示详情"
                : `已更新至「${item.target_title ?? "事项"}」`
              : inactivePreview ? `该调整当时未确认，且${lifecycle === "source_revoked" ? "相关资料来源已取消授权" : `所属事项现已${lifecycle === "archived" ? "归档" : "删除"}`}` : null;
            const createdAt = serverItem ? item.created_at : item.createdAt;
            const key = serverItem ? item.local_run_id : item.id;
            const source = serverItem ? item.source ?? "chat" : item.source ?? "chat";
            const isFollowUp = source === "follow_up";
            return (
            <div key={key} ref={!liveTurn && index === unifiedHistory.length - 1 ? latestIntakeRef : undefined}>
              {isFollowUp ? null : <div className="bubble-user">{question}</div>}
              <div className={`card ask-message mt-10${isFailure ? " ask-message--failed" : ""}`}>
                <div className="ask-message__meta">
                  {!isFailure && outcome === "answer" ? (
                    <span className="ask-message__ok" aria-label="直接回答" title="直接回答">
                      <CheckCircle2 size={13} />
                    </span>
                  ) : null}
                  <span className="ask-message__time">{formatDateTime(createdAt)}</span>
                </div>
                <div className="ask-message__body">
                  {historyNote ? <p className="ask-message__note">{historyNote}</p> : null}

                  {isFailure ? (
                    <div className="ask-failure">
                      <p className="ask-failure__title">{isFollowUp ? "这次跟进没处理完" : "这次没处理完"}</p>
                      <p className="ask-failure__body">
                        {isFollowUp
                          ? "这次跟进没跑完，内容已经存好，晚点再看看。"
                          : `你刚才那条「${question}」我收到了，但这次没能跑完。内容我先存着。`}
                      </p>
                      <div className="ask-failure__actions">
                        <button
                          type="button"
                          className="btn btn--secondary btn--small"
                          onClick={() => {
                            if (!isFollowUp) setDraft(question);
                            inputRef.current?.focus();
                          }}
                        >
                          重新编辑
                        </button>
                        <details className="ask-stored">
                          <summary>查看已存内容</summary>
                          <p>原话还留着，可以改了再发。</p>
                          {serverItem && item.error?.details_reference_id ? (
                            <p>问题编号：{item.error.details_reference_id}</p>
                          ) : null}
                        </details>
                      </div>
                    </div>
                  ) : null}

                  {answer ? <Markdown source={answer} /> : null}
                  {clarify ? <p className="body-text" style={{ margin: 0 }}>{clarify}</p> : null}
                  {serverRunId && pendingContext ? <ApiOperationCard context={pendingContext} /> : null}
                  {outcome === "needs_clarification" && !hasContinuation && serverRunId && pendingContext?.choices?.length ? <div className="chip-row mt-8">{pendingContext.choices.map((choice) => <button type="button" className="chip" key={choice} disabled={processing || dismissingReply} onClick={() => void send(choice, serverRunId)}>{choice}</button>)}</div> : null}
                  {outcome === "processing" && !answer ? (
                    <p className="ask-message__note" role="status">{serverItem ? progressLabel(item.stage) : "助手正在处理…"}</p>
                  ) : null}
                  {pendingContext?.missing_information?.length ? (
                    <div className="chip-row mt-8">
                      {pendingContext.missing_information.map((item) => (
                        <span key={item} className="chip chip--sm">{item}</span>
                      ))}
                    </div>
                  ) : null}
                  {!answer && !clarify && !historyNote && !isFailure && outcome !== "processing" ? (
                    <p className="ask-message__note">这次没有后续内容了。</p>
                  ) : null}

                  {state === "signedIn" && pendingContext?.memory_operation_ids?.map((operationId) => (
                    <MemoryWriteReceipts key={operationId} operationId={operationId} />
                  ))}

                  {(answer || (historyNote && serverItem && item.history_target_id)) ? (
                    <div className="ask-message__actions">
                      {answer ? (
                        <button
                          type="button"
                          className="ask-message__action"
                          onClick={() => void navigator.clipboard.writeText(answer)}
                        >
                          复制
                        </button>
                      ) : null}
                      {historyNote && serverItem && item.history_target_id ? (
                        <Link href={`/matters/${item.history_target_id}`} className="ask-message__action">
                          {lifecycle === "archived" ? "查看归档" : "查看事项"}
                        </Link>
                      ) : null}
                    </div>
                  ) : null}
                </div>
              </div>
              {scheduleCandidates.length > 0 ? (
                <div className="m-stack-8 mt-8" aria-label="选择要修改的日程">
                  {scheduleCandidates.map((candidate) => (
                    <button
                      key={candidate.schedule_id}
                      type="button"
                      className="choice-card"
                      disabled={processing || dismissingReply || hasContinuation}
                      onClick={() => {
                        setContinuationRunId(serverRunId);
                        setClarificationContext(pendingContext ?? {});
                        void send(
                          scheduleCandidatePrompt(candidate),
                          serverRunId,
                        );
                      }}
                    >
                      <span className="choice-card__dot" aria-hidden="true" />
                      <span className="grow">
                        <span className="choice-card__title">{candidate.title}</span>
                        <span className="choice-card__detail">
                          {formatDateTime(candidate.starts_at)}
                          {candidate.ends_at ? ` 至 ${formatDateTime(candidate.ends_at)}` : ""}
                          {candidate.location ? ` · ${candidate.location}` : ""}
                        </span>
                        <span className="choice-card__detail">
                          {hasContinuation
                            ? "已经接着处理了，这条选项不用再点。"
                            : "选好目标后，助手会继续处理。"}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            );
          })}
          {intakeHistoryLoading ? <LoadingCard label="正在同步历史…" /> : null}
        </section>
      ) : null}

      {liveTurn ? <section className="ask-stream" aria-live="polite" ref={latestIntakeRef}>
        <div className="bubble-user">{liveTurn.question}</div>
        <div className="card mt-10">
          <span className="micro">{liveTurn.answer ? "正在整理回复…" : progressLabel(trackedResult?.stage)}</span>
          {liveTurn.answer ? <Markdown source={liveTurn.answer} /> : <span className="spinner" />}
        </div>
      </section> : null}

      {timeline.length === 0 && unifiedHistory.length === 0 ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <div className="card">
            <p className="card-title" style={{ fontSize: 18 }}>
              直接说，交给助手整理
            </p>
            <p className="body-text muted mt-8">
              想问什么、想改什么，直接说就好。需要补充的信息，助手会再问你。
            </p>
            <div className="mt-12" style={{ display: "grid", gap: 8 }}>
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  onClick={() => {
                    setDraft(suggestion);
                    void send(suggestion);
                  }}
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
                  <ArrowRight size={16} strokeWidth={1.5} className="faint" aria-hidden="true" />
                </button>
              ))}
            </div>
            {ask.loadingHistory ? (
              <p className="micro mt-12" style={{ marginBottom: 0 }}>
                正在同步历史…
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
                      : "这条暂存还没有内容"
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
          {ask.loadingHistory ? <LoadingCard label="正在同步历史…" /> : null}
          <div ref={endRef} />
        </div>
      )}


      {composerError ? (
        <p className="composer-hint" style={{ position: "fixed", bottom: "calc(132px + env(safe-area-inset-bottom))", left: "50%", transform: "translateX(-50%)", width: "100%", maxWidth: 480, zIndex: 36 }} role="alert">
          {composerError}
        </p>
      ) : null}

      {activeReplyQuestion ? (
        <div className="composer-operation" role="status">
          <strong>正在回复追问</strong>
          <p>{activeReplyQuestion}</p>
          {activeReplyContext.missing_information?.length ? (
            <div className="chip-row" style={{ marginBottom: 8 }}>
              {activeReplyContext.missing_information.map((item) => (
                <span key={item} className="chip chip--sm">{item}</span>
              ))}
            </div>
          ) : null}
          {activeReplyContext.schedule_candidates?.length ? (
            <div className="m-stack-8" aria-label="选择要修改的日程">
              {activeReplyContext.schedule_candidates.map((candidate) => (
                <button
                  key={candidate.schedule_id}
                  type="button"
                  className="choice-card"
                  disabled={processing || dismissingReply}
                  onClick={() => {
                    const runId = activeReplyRunId;
                    setSupplementQuestion(null);
                    setClarificationContext({});
                    void send(scheduleCandidatePrompt(candidate), runId);
                  }}
                >
                  <span className="choice-card__dot" aria-hidden="true" />
                  <span className="grow">
                    <span className="choice-card__title">{candidate.title}</span>
                    <span className="choice-card__detail">
                      {formatDateTime(candidate.starts_at)}
                      {candidate.ends_at ? ` 至 ${formatDateTime(candidate.ends_at)}` : ""}
                      {candidate.location ? ` · ${candidate.location}` : ""}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          ) : null}
          {linkedReply && activeReplyContext.choices?.length ? (
            <div className="chip-row">
              {activeReplyContext.choices.map((choice) => (
                <button type="button" className="chip" key={choice} disabled={processing || recovering}
                  onClick={() => void send(choice, linkedReply.local_run_id)}>{choice}</button>
              ))}
            </div>
          ) : null}
          <span>
            下方会接续刚才的事项和已填写内容发送，不需要重述背景。
          </span>
          <button
            type="button"
            className="link-button mt-6"
            aria-label="取消回复追问"
            disabled={processing || dismissingReply}
            onClick={() => void dismissReply()}
          >
            {dismissingReply ? "正在取消…" : "× 取消"}
          </button>
        </div>
      ) : null}

      {operationFailure ? (
        <div className="composer-operation composer-operation--amber" role="alert">
          <strong>
            {operationFailure.includes("正在核实") ? "正在核实结果" : "还没拿到确定结果"}
          </strong>
          <p>{operationFailure}</p>
          <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
            <button type="button" className="btn btn--secondary btn--small" onClick={() => void recoverResult()}>
              查询原结果
            </button>
            <button
              type="button"
              className="btn btn--quiet btn--small"
              onClick={() => void send(draft)}
              disabled={!draft.trim() || processing || dismissingReply || (intakeHistoryLoading && !serverIntakeResults.length)}
            >
              继续原操作
            </button>
          </div>
        </div>
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
          placeholder={state === "signedIn" ? "说一件事、一个问题或最新进展" : "登录后才能交给助手"}
          aria-label="交给助手"
          maxLength={4000}
        />
        <button
          type="button"
          className="composer__send"
          disabled={!canSend}
          onClick={() => void send()}
          aria-label="发送"
        >
          {processing ? <span className="spinner" style={{ width: 15, height: 15 }} /> : <ArrowUp size={17} strokeWidth={2.4} />}
        </button>
      </div>

      <MatterDraftSheet
        draft={intakeDraft}
        open={Boolean(intakeDraft)}
        onClose={() => setIntakeDraft(null)}
        onConfirmed={(response) => {
          setMemoryRefresh(x => x + 1);
          setPendingDrafts((rows) => rows.filter((row) => row.draft_id !== response.draft.draft_id));
          setIntakeDraft(null);
          setDraft("");
          intakeKey.current = null;
          toast(confirmSummary(response));
          requestTodayRefresh();
          void refreshServerIntake();
        }}
        onDraftCancelled={(cancelled) => {
          setPendingDrafts((rows) => rows.filter((row) => row.draft_id !== cancelled.draft_id));
          setIntakeDraft(null);
          toast("草稿已取消");
          requestTodayRefresh();
          void refreshServerIntake();
        }}
      />
    </div>
  );
}
