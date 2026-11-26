"use client";

/**
 * Show saved supplements with their separately stored AI texts.
 *
 * User text and assistant text keep their own labels even after acceptance.
 * Corrections and answers are recorded with an explicit relation type; their
 * previews and totals come from the server and are never inferred locally.
 * Only queued/running items are polled, for at most three minutes, and the
 * poll only reads state: it never saves text, starts analysis or retries.
 */

import { useEffect, useRef, useState } from "react";
import { Check, RotateCcw } from "lucide-react";

import type {
  MatterSupplement,
  MatterSupplementText,
  SupplementRelatedText,
} from "@/lib/mobile/api";
import { ApiError } from "@/lib/mobile/api";
import {
  acceptSupplementText,
  getMatterSupplement,
  listSupplementTextRelations,
  retrySupplementAnalysis,
} from "@/lib/mobile/matters-api";
import { formatDateTime } from "@/lib/mobile/format";

import type { SupplementReplyTarget } from "./matter-supplement-reply-sheet";

const POLL_INTERVAL_MS = 3000;
const POLL_LIMIT_MS = 180_000;
const MAX_ANALYSIS_ATTEMPTS = 3;
const RELATION_PAGE_SIZE = 20;

const PURPOSE_LABEL: Record<MatterSupplementText["purpose"], string> = {
  original: "你",
  user_revision: "你的补充",
  summary: "助手摘要",
  suggestion: "助手建议",
  question: "助手追问",
};

const RELATION_LABEL: Record<string, string> = {
  correction: "纠正",
  answer: "回复",
  related: "早前的补充",
};

const STATUS: Record<MatterSupplement["ai_status"], { label: string; tone: string }> = {
  queued: { label: "正在整理你补充的内容…", tone: "pill" },
  running: { label: "正在整理你补充的内容…", tone: "pill pill--fog" },
  succeeded: { label: "已整理", tone: "pill pill--green" },
  no_supplement: { label: "已经记下来了", tone: "pill" },
  failed: { label: "内容已经记下来了，但整理没完成", tone: "pill pill--red" },
  unknown: { label: "结果待核对", tone: "pill pill--amber" },
};

/** Prefer the recorded action over the compatibility `user_revision` purpose. */
function userTextLabel(text: MatterSupplementText): string {
  if (text.relation_type === "correction") return "你的纠正";
  if (text.relation_type === "answer") return "你的回复";
  return PURPOSE_LABEL[text.purpose];
}

function actionErrorText(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "not_assistant_text") return "这段不是助手写的，不能采纳。";
    if (error.code === "analysis_retry_unavailable") return "这次不能重试。";
    if (error.status === 404) return "这条补充已经不在了。";
    return error.message || "这次没成功，再试一次。";
  }
  return "这次没成功，再试一次。";
}

/** One-line count of direct corrections, answers and legacy links. */
function relationSummary(text: MatterSupplementText): string {
  const parts: string[] = [];
  if (text.correction_count > 0) parts.push(`${text.correction_count} 条纠正`);
  if (text.answer_count > 0) parts.push(`${text.answer_count} 条回复`);
  if (text.untyped_relation_count > 0) parts.push(`${text.untyped_relation_count} 条早前的补充`);
  return parts.join(" · ");
}

/**
 * Load the full list of direct relations for one text.
 *
 * The server keeps at most five previews on each text; this panel pages the
 * rest with the relation text id and never triggers AI work.
 */
function RelationsPanel({
  matterId,
  supplementId,
  textId,
  open,
  total,
}: {
  matterId: string;
  supplementId: string;
  textId: string;
  open: boolean;
  total: number;
}) {
  const [records, setRecords] = useState<SupplementRelatedText[]>([]);
  const [nextBefore, setNextBefore] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    setError(null);
    void listSupplementTextRelations(matterId, supplementId, textId, {
      limit: RELATION_PAGE_SIZE,
    })
      .then((page) => {
        if (!alive) return;
        setRecords(page.records);
        setNextBefore(page.has_more ? page.next_before_id : null);
      })
      .catch((caught) => {
        if (alive) {
          setError(
            caught instanceof ApiError && caught.status === 404
              ? "关联记录暂时没读到。"
              : actionErrorText(caught),
          );
        }
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [open, matterId, supplementId, textId, total]);

  const loadMore = async () => {
    if (!nextBefore || loadingMore) return;
    setLoadingMore(true);
    try {
      const page = await listSupplementTextRelations(matterId, supplementId, textId, {
        limit: RELATION_PAGE_SIZE,
        beforeId: nextBefore,
      });
      setRecords((current) => [...current, ...page.records]);
      setNextBefore(page.has_more ? page.next_before_id : null);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 404
          ? "关联记录暂时没读到。"
          : actionErrorText(caught),
      );
    } finally {
      setLoadingMore(false);
    }
  };

  if (!open) return null;
  return (
    <div className="supplement-relation-panel">
      {loading ? <p className="micro">正在读取关联记录…</p> : null}
      {error ? <p className="micro danger-text">{error}</p> : null}
      {!loading && !error && records.length === 0 ? (
        <p className="micro">还没有关联记录。</p>
      ) : null}
      {records.map((record) => (
        <div className="supplement-relation" key={record.text_id}>
          <span className="micro">
            {formatDateTime(record.created_at)} · {RELATION_LABEL[record.relation_type ?? "related"]}
          </span>
          <p className="caption" style={{ color: "var(--m-ink)", margin: "2px 0 0" }}>
            {record.text}
          </p>
        </div>
      ))}
      {nextBefore ? (
        <button
          type="button"
          className="link-button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
        >
          {loadingMore ? "正在读取…" : "加载更多"}
        </button>
      ) : null}
    </div>
  );
}

export function MatterSupplementList({
  matterId,
  items,
  onUpdated,
  onReply,
  hasMore,
  loadingMore,
  onLoadMore,
}: {
  matterId: string;
  items: MatterSupplement[];
  onUpdated: (supplement: MatterSupplement) => void;
  /** Attach the next composer save to an existing text with an explicit action. */
  onReply: (parent: SupplementReplyTarget) => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
}) {
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openRelations, setOpenRelations] = useState<string | null>(null);
  const deadlines = useRef(new Map<string, number>());
  const updatedRef = useRef(onUpdated);
  updatedRef.current = onUpdated;

  const activeKey = items
    .filter((item) => item.ai_status === "queued" || item.ai_status === "running")
    .map((item) => `${item.supplement_id}:${item.ai_status}`)
    .join(",");

  useEffect(() => {
    if (!activeKey) return;
    const ids = activeKey.split(",").map((entry) => entry.split(":")[0]);
    const now = Date.now();
    for (const id of ids) {
      if (!deadlines.current.has(id)) deadlines.current.set(id, now);
    }
    const tick = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      for (const id of ids) {
        const started = deadlines.current.get(id);
        if (started === undefined) continue;
        if (Date.now() - started > POLL_LIMIT_MS) {
          deadlines.current.delete(id);
          continue;
        }
        try {
          updatedRef.current(await getMatterSupplement(matterId, id));
        } catch {
          // Keep the saved text and try again on the next tick.
        }
      }
    };
    const timer = window.setInterval(() => void tick(), POLL_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [activeKey, matterId]);

  const accept = async (supplement: MatterSupplement, text: MatterSupplementText) => {
    setBusyId(text.text_id);
    setError(null);
    try {
      onUpdated(await acceptSupplementText(matterId, supplement.supplement_id, text.text_id));
    } catch (caught) {
      setError(actionErrorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  /** Manual status check after automatic polling stops or the page was hidden. */
  const refresh = async (supplement: MatterSupplement) => {
    setBusyId(supplement.supplement_id);
    setError(null);
    try {
      onUpdated(await getMatterSupplement(matterId, supplement.supplement_id));
    } catch (caught) {
      setError(actionErrorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  const retry = async (supplement: MatterSupplement) => {
    setBusyId(supplement.supplement_id);
    setError(null);
    try {
      onUpdated(await retrySupplementAnalysis(matterId, supplement.supplement_id));
    } catch (caught) {
      setError(actionErrorText(caught));
    } finally {
      setBusyId(null);
    }
  };

  if (items.length === 0) {
    return <p className="caption">还没有补充内容。</p>;
  }

  return (
    <div>
      {items.map((supplement) => {
        const status = STATUS[supplement.ai_status];
        const userTexts = supplement.texts.filter((text) => text.origin === "user");
        const assistantTexts = supplement.texts.filter((text) => text.origin === "assistant");
        const canRetry =
          supplement.ai_status === "failed" && supplement.analysis_attempt < MAX_ANALYSIS_ATTEMPTS;
        return (
          <article className="supplement-item" key={supplement.supplement_id}>
            <div className="supplement-meta">
              <span className="micro">{formatDateTime(supplement.created_at)}</span>
              <span className={status.tone}>{status.label}</span>
              {supplement.ai_status === "succeeded" && supplement.classification !== "unknown" ? (
                <span className="pill">
                  {supplement.classification === "explanation"
                    ? "说明"
                    : supplement.classification === "progress"
                      ? "进展"
                      : "说明与进展"}
                </span>
              ) : null}
            </div>

            {userTexts.map((text) => {
              const total =
                text.correction_count + text.answer_count + text.untyped_relation_count;
              const key = `${supplement.supplement_id}:${text.text_id}`;
              return (
                <div key={text.text_id}>
                  <div className="supplement-quote">
                    <span className="micro" style={{ display: "block", marginBottom: 3 }}>
                      {userTextLabel(text)}
                    </span>
                    {text.text}
                  </div>
                  <div className="supplement-actions">
                    <button
                      type="button"
                      className="link-button"
                      onClick={() =>
                        onReply({
                          textId: text.text_id,
                          supplementId: supplement.supplement_id,
                          preview: text.text,
                          sourceLabel: text.origin === "assistant" ? PURPOSE_LABEL[text.purpose] : userTextLabel(text),
                          relationType: "correction",
                        })
                      }
                    >
                      纠正
                    </button>
                    {total > 0 ? (
                      <>
                        <span className="micro">{relationSummary(text)}</span>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => setOpenRelations(openRelations === key ? null : key)}
                        >
                          {openRelations === key ? "收起" : "查看"}
                        </button>
                      </>
                    ) : null}
                  </div>
                  <RelationsPanel
                    matterId={matterId}
                    supplementId={supplement.supplement_id}
                    textId={text.text_id}
                    open={openRelations === key}
                    total={total}
                  />
                </div>
              );
            })}

            {assistantTexts.map((text) => {
              const total =
                text.correction_count + text.answer_count + text.untyped_relation_count;
              const key = `${supplement.supplement_id}:${text.text_id}`;
              return (
                <div className="supplement-block" key={text.text_id}>
                  <span className="supplement-origin">{PURPOSE_LABEL[text.purpose]}</span>
                  <p className="body-text" style={{ margin: "3px 0 0" }}>
                    {text.text}
                  </p>
                  <div className="supplement-actions">
                    {text.purpose !== "question" ? (
                      text.accepted_at ? (
                        <span className="micro">已采纳</span>
                      ) : (
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => void accept(supplement, text)}
                          disabled={busyId === text.text_id}
                        >
                          <Check size={13} /> {busyId === text.text_id ? "正在采纳…" : "采纳"}
                        </button>
                      )
                    ) : null}
                    {text.purpose === "question" ? (
                      <button
                        type="button"
                        className="link-button"
                        onClick={() =>
                          onReply({
                            textId: text.text_id,
                            supplementId: supplement.supplement_id,
                            preview: text.text,
                            sourceLabel: text.origin === "assistant" ? PURPOSE_LABEL[text.purpose] : userTextLabel(text),
                            relationType: "answer",
                          })
                        }
                      >
                        回复
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="link-button"
                        onClick={() =>
                          onReply({
                            textId: text.text_id,
                            supplementId: supplement.supplement_id,
                            preview: text.text,
                            sourceLabel: text.origin === "assistant" ? PURPOSE_LABEL[text.purpose] : userTextLabel(text),
                            relationType: "correction",
                          })
                        }
                      >
                        纠正
                      </button>
                    )}
                    {total > 0 ? (
                      <>
                        <span className="micro">{relationSummary(text)}</span>
                        <button
                          type="button"
                          className="link-button"
                          onClick={() => setOpenRelations(openRelations === key ? null : key)}
                        >
                          {openRelations === key ? "收起" : "查看"}
                        </button>
                      </>
                    ) : null}
                  </div>
                  <RelationsPanel
                    matterId={matterId}
                    supplementId={supplement.supplement_id}
                    textId={text.text_id}
                    open={openRelations === key}
                    total={total}
                  />
                </div>
              );
            })}

            {supplement.ai_status === "queued" || supplement.ai_status === "running" ? (
              <button
                type="button"
                className="link-button"
                onClick={() => void refresh(supplement)}
                disabled={busyId === supplement.supplement_id}
              >
                {busyId === supplement.supplement_id ? "正在刷新…" : "刷新状态"}
              </button>
            ) : null}
            {supplement.ai_status === "no_supplement" ? (
              <p className="micro">这次没有需要补充的内容。</p>
            ) : null}
            {canRetry ? (
              <button
                type="button"
                className="link-button"
                onClick={() => void retry(supplement)}
                disabled={busyId === supplement.supplement_id}
              >
                <RotateCcw size={13} />
                {busyId === supplement.supplement_id ? "正在整理…" : "重新整理"}
              </button>
            ) : null}
            {supplement.ai_status === "failed" && !canRetry ? (
              <p className="micro">已经重试过三次，内容还在。</p>
            ) : null}
          </article>
        );
      })}

      {error ? (
        <p className="danger-text" role="alert" style={{ margin: "10px 0 0" }}>
          {error}
        </p>
      ) : null}

      {hasMore ? (
        <button
          type="button"
          className="link-button"
          onClick={onLoadMore}
          disabled={loadingMore}
          style={{ marginTop: 12 }}
        >
          {loadingMore ? "正在读取…" : "更早的补充"}
        </button>
      ) : null}
    </div>
  );
}
