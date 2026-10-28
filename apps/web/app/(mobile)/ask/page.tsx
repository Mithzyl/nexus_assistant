"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { useAuth, useUI } from "@/components/mobile/providers";
import { LoadingCard, ScreenHeader } from "@/components/mobile/ui";
import { MatterDraftSheet } from "@/components/mobile/matter-draft-sheet";
import type {
  IntakePendingContext,
  IntakeScheduleCandidate,
  MatterDraft,
  MatterIntakeResult,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  confirmSummary,
  getIntakeResult,
  intakeMatter,
  listDrafts,
  listIntakeResults,
  newOperationKey,
  requestTodayRefresh,
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
  const [intakeDraft, setIntakeDraft] = useState<MatterDraft | null>(null);
  const [pendingDrafts, setPendingDrafts] = useState<MatterDraft[]>([]);
  const [intakeAnswers, setIntakeAnswers] = useState<IntakeAnswer[]>([]);
  const [serverIntakeResults, setServerIntakeResults] = useState<MatterIntakeResult[]>([]);
  const [processing, setProcessing] = useState(false);
  const [supplementQuestion, setSupplementQuestion] = useState<string | null>(null);
  const [clarificationContext, setClarificationContext] =
    useState<IntakePendingContext>({});
  const [operationFailure, setOperationFailure] = useState<string | null>(null);
  const [continuationRunId, setContinuationRunId] = useState<string | null>(null);
  const [recoveryRunId, setRecoveryRunId] = useState<string | null>(null);
  const [intakeHistoryLoading, setIntakeHistoryLoading] = useState(false);
  const intakeKey = useRef<{
    text: string;
    key: string;
    continuationRunId: string | null;
  } | null>(null);
  const hydratedAnswerKey = useRef<string | null>(null);
  const intakeHistorySeq = useRef(0);
  const { toast } = useUI();
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const restoredComposerKey = useRef<string | null>(null);
  const intakeAnswerStorageKey = accountKey("pa.web.intakeAnswers", user?.user_id ?? null);
  const composerStorageKey = accountKey("pa.web.ask.composer", user?.user_id ?? null);

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
    setPendingDrafts([]);
    setIntakeDraft(null);
    setSupplementQuestion(null);
    setClarificationContext({});
    setOperationFailure(null);
    setContinuationRunId(null);
    setRecoveryRunId(null);
    intakeKey.current = null;
    if (state === "signedIn") {
      void refreshServerIntake(controller.signal);
    } else {
      setIntakeHistoryLoading(false);
    }
    return () => controller.abort();
  }, [refreshServerIntake, state, user?.user_id]);

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

  const canSend = draft.trim().length > 0 && state === "signedIn" && !processing;

  const send = async (
    text?: string,
    continuationOverride?: string | null,
  ) => {
    const value = (text ?? draft).trim();
    const activeContinuation =
      continuationOverride === undefined ? continuationRunId : continuationOverride;
    if (!value) return;
    if (processing) return;
    if (state !== "signedIn") {
      setComposerError("登录后才能交给助手，输入已保留。");
      return;
    }
    if (value.length > 4000) {
      setComposerError("这段内容最多 4000 个字符。");
      return;
    }
    setComposerError(null);
    setSupplementQuestion(null);
    setClarificationContext({});
    setOperationFailure(null);
    setProcessing(true);
    if (
      !intakeKey.current ||
      intakeKey.current.text !== value ||
      intakeKey.current.continuationRunId !== activeContinuation
    ) {
      intakeKey.current = {
        text: value,
        key: newOperationKey("web-intake"),
        continuationRunId: activeContinuation,
      };
    }
    try {
      const result = await intakeMatter(
        value,
        intakeKey.current.key,
        activeContinuation,
      );
      if (result.outcome === "answer") {
        if (!result.answer) {
          setOperationFailure(
            result.message || "服务器没有返回可读的回答，输入已经保留。",
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
          },
        ].slice(-20));
        setDraft("");
        writeSession(composerStorageKey, "");
        toast("服务器已直接回答");
        intakeKey.current = null;
        setContinuationRunId(null);
        setClarificationContext({});
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
      if (result.outcome === "needs_clarification") {
        setClarificationContext(result.pending_context ?? {});
        setSupplementQuestion(
          result.clarify || result.message || "请补充缺失的信息。",
        );
        setContinuationRunId(result.local_run_id);
        setRecoveryRunId(result.local_run_id);
        setDraft("");
        writeSession(composerStorageKey, "");
        intakeKey.current = null;
        void refreshServerIntake();
        return;
      }
      if (result.outcome === "processing" || result.outcome === "unknown") {
        setOperationFailure(
          "正在核实结果。输入和恢复入口已保留，不会自动重新创建事项。",
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
        toast("已生成待确认变更，尚未写入正式安排");
        void refreshServerIntake();
        return;
      }
      if (!result.draft) {
        setOperationFailure(
          result.message || "服务器没有生成事项草稿，当前输入已保留。",
        );
        setRecoveryRunId(result.local_run_id);
        void refreshServerIntake();
        return;
      }
    } catch (caught) {
      if (
        caught instanceof ApiError &&
        (caught.status >= 500 ||
          caught.code.includes("timeout") ||
          caught.code.includes("unknown"))
      ) {
        setOperationFailure(
          "正在核实结果。输入和本次操作标识已保留，可查询原结果或用同一次操作继续。",
        );
        return;
      }
      setComposerError(caught instanceof Error ? caught.message : "暂时无法交给助手。");
      void refreshServerIntake();
    } finally {
      setProcessing(false);
    }
  };

  const recoverResult = async () => {
    setOperationFailure(null);
    try {
      const result = recoveryRunId
        ? await getIntakeResult(recoveryRunId)
        : (await listIntakeResults(20)).find(
            (item) => item.request_text === draft.trim(),
          );
      if (!result) {
        setOperationFailure(
          "服务器暂时没有找到这次结果。可以保留原输入和操作标识继续原来的处理。",
        );
        return;
      }
      setRecoveryRunId(result.local_run_id);
      if (result.outcome === "draft" && result.draft) {
        setIntakeDraft(result.draft);
        setContinuationRunId(null);
        setClarificationContext({});
        toast("已读取服务器保存的待确认结果");
      } else if (result.outcome === "needs_clarification") {
        setClarificationContext(result.pending_context ?? {});
        setSupplementQuestion(result.clarify || "请补充缺失的信息。");
        setContinuationRunId(result.local_run_id);
      } else if (result.outcome === "answer") {
        setContinuationRunId(null);
        setClarificationContext({});
        toast("服务器回答已恢复到历史");
      } else if (result.outcome === "processing" || result.outcome === "unknown") {
        setOperationFailure("服务器仍在核实这次结果，请稍后再次查询。");
      } else {
        setOperationFailure("这次操作没有形成可继续的结果，输入已保留。");
      }
      await refreshServerIntake();
    } catch (caught) {
      setOperationFailure(
        caught instanceof Error ? caught.message : "暂时无法查询原结果。",
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
      setComposerError("复制失败，可以长按选择回答文字。");
    }
  };

  const timeline = [...ask.items].reverse();
  const unifiedHistory = mergeIntakeHistory(
    serverIntakeResults.filter((item) => item.outcome !== "draft"),
    intakeAnswers,
  );

  return (
    <div className="m-screen" style={{ paddingBottom: 220 }}>
      <ScreenHeader title="问助手" subtitle="说一件事、一个问题或最新进展" />

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

      {pendingDrafts.length > 0 ? (
        <section style={{ padding: "0 var(--m-gutter)", marginBottom: 16 }}>
          <div className="card card--amber">
            <p className="card-title amber" style={{ fontSize: 15 }}>
              有 {pendingDrafts.length} 条待确认变更
            </p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              从服务器恢复，不会因为重新打开页面反复弹出。
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
          {unifiedHistory.map((item) => {
            const serverItem = "request_text" in item;
            const question = serverItem ? item.request_text : item.question;
            const answer = serverItem ? item.answer : item.answer;
            const clarify = serverItem ? item.clarify : null;
            const pendingContext = (
              serverItem ? item.pending_context : null
            ) as IntakePendingContext | null;
            const serverRunId = serverItem ? item.local_run_id : null;
            const scheduleCandidates: IntakeScheduleCandidate[] =
              pendingContext?.schedule_candidates ?? [];
            const hasContinuation = serverItem
              ? serverIntakeResults.some(
                  (result) => result.parent_run_id === item.local_run_id,
                )
              : false;
            const outcome = serverItem ? item.outcome : "answer";
            const createdAt = serverItem ? item.created_at : item.createdAt;
            const key = serverItem ? item.local_run_id : item.id;
            return (
            <div key={key}>
              <div className="bubble-user">{question}</div>
              <div className="card mt-10">
                <div className="flex items-center justify-between gap-10">
                  <span className="micro">
                    {outcome === "needs_clarification"
                      ? "还需要补充信息"
                      : outcome === "processing"
                        ? "正在核实结果"
                        : outcome === "unknown"
                          ? "结果暂不确定"
                          : outcome === "failed"
                            ? "处理失败"
                            : outcome === "cancelled"
                              ? "已取消"
                              : "服务器直接回答"}
                  </span>
                  <span className="micro">{formatDateTime(createdAt)}</span>
                </div>
                <div className="mt-8">
                  {answer ? <Markdown source={answer} /> : null}
                  {clarify ? <p className="body-text" style={{ margin: 0 }}>{clarify}</p> : null}
                  {pendingContext?.missing_information?.length ? (
                    <div className="chip-row mt-8">
                      {pendingContext.missing_information.map((item) => (
                        <span key={item} className="chip chip--sm">{item}</span>
                      ))}
                    </div>
                  ) : null}
                  {!answer && !clarify ? (
                    <p className="caption" style={{ margin: 0 }}>
                      {outcome === "processing" || outcome === "unknown"
                        ? "服务器尚未返回确定结果，可保留原输入后继续查询。"
                        : "这次操作没有形成可继续的回答。"}
                    </p>
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
                      disabled={processing || hasContinuation}
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
                            ? "已继续处理，这条候选不能再次提交。"
                            : "只用于继续生成待确认草稿，不会直接保存修改。"}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
            );
          })}
          {intakeHistoryLoading ? <LoadingCard label="正在同步统一输入历史…" /> : null}
        </section>
      ) : null}

      {timeline.length === 0 && unifiedHistory.length === 0 ? (
        <section style={{ padding: "0 var(--m-gutter)" }}>
          <div className="card">
            <p className="card-title" style={{ fontSize: 18 }}>
              直接说，交给助手理解。
            </p>
            <p className="body-text muted mt-8">
              助手会判断是回答、补充问题，还是生成一张待确认变更卡；确认前不会写入正式安排。
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
            一次发送只调用一个统一入口。服务器返回回答时直接显示；返回事项草稿时使用底部确认面板，不把同一句话再次发送给另一个入口。
          </p>
          <p className="caption" style={{ margin: 0 }}>
            日历、提醒事项和笔记目前只读取已同步到服务器的副本；浏览器不能声称获得了系统读取权限。
          </p>
          <p className="caption" style={{ margin: 0 }}>
            正式日程和提醒只在你确认变更卡后写入。待确认内容会从服务器恢复；多候选事项可在面板中直接选择，不需要重输背景。
          </p>
        </div>
      </section>

      {composerError ? (
        <p className="composer-hint" style={{ position: "fixed", bottom: "calc(132px + env(safe-area-inset-bottom))", left: "50%", transform: "translateX(-50%)", width: "100%", maxWidth: 480, zIndex: 36 }} role="alert">
          {composerError}
        </p>
      ) : null}

      {supplementQuestion ? (
        <div className="composer-operation" role="status">
          <strong>还需要补充一点</strong>
          <p>{supplementQuestion}</p>
          {clarificationContext.missing_information?.length ? (
            <div className="chip-row" style={{ marginBottom: 8 }}>
              {clarificationContext.missing_information.map((item) => (
                <span key={item} className="chip chip--sm">{item}</span>
              ))}
            </div>
          ) : null}
          {clarificationContext.schedule_candidates?.length ? (
            <div className="m-stack-8" aria-label="选择要修改的日程">
              {clarificationContext.schedule_candidates.map((candidate) => (
                <button
                  key={candidate.schedule_id}
                  type="button"
                  className="choice-card"
                  disabled={processing}
                  onClick={() => {
                    const runId = continuationRunId;
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
          <span>
            下方会接续刚才的事项和已填写内容发送，不需要重述背景。
          </span>
          <button
            type="button"
            className="link-button mt-6"
            onClick={() => {
              setContinuationRunId(null);
              setSupplementQuestion(null);
              setClarificationContext({});
            }}
          >
            改作新的输入
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
              disabled={!draft.trim() || processing}
            >
              继续原操作
            </button>
          </div>
        </div>
      ) : null}

      {processing ? (
        <p className="composer-hint" style={{ position: "fixed", bottom: "calc(132px + env(safe-area-inset-bottom))", left: "50%", transform: "translateX(-50%)", width: "100%", maxWidth: 480, zIndex: 36 }}>
          正在理解内容，服务器返回结果后再继续…
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
