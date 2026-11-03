"use client";

import { useCallback, useEffect, useState } from "react";
import { Pause, Play } from "lucide-react";

import { ApiError } from "@/lib/mobile/api";
import type {
  FollowUpExecutionRead,
  FollowUpExecutionState,
  FollowUpRead,
  MatterIntakeResult,
} from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import {
  getFollowUp,
  getIntakeResult,
  intakeMatter,
  listFollowUpExecutions,
  listFollowUps,
  newOperationKey,
  patchFollowUp,
} from "@/lib/mobile/matters-api";
import { ApiOperationCard } from "./api-operation-card";
import { Markdown } from "./markdown";
import { useUI } from "./providers";
import { LoadingCard, Pill, Sheet } from "./ui";

const EXECUTION_LABEL: Record<FollowUpExecutionState, string> = {
  queued: "等待处理",
  running: "正在处理",
  waiting_user: "等待回复",
  succeeded: "已处理",
  failed: "没有完成",
  unknown: "待核对",
  skipped: "已跳过",
};

function stateLabel(followUp: FollowUpRead): string {
  if (followUp.state === "paused") return "已暂停";
  if (followUp.state === "closed") return "已结束";
  return "进行中";
}

function nextCheckLabel(followUp: FollowUpRead): string {
  if (followUp.state === "paused") return "暂停期间不检查";
  if (followUp.next_trigger_at) return `下次检查 ${formatDateTime(followUp.next_trigger_at)}`;
  if (followUp.trigger.kind === "record_changed") return "记录变化时检查";
  return "等待时间条件";
}

/** Follow-up questions and controls inside a matter, without exposing backend stages. */
export function MatterFollowUps({
  matterId,
  onChanged,
}: {
  matterId: string;
  onChanged?: () => void;
}) {
  const { toast } = useUI();
  const [followUps, setFollowUps] = useState<FollowUpRead[]>([]);
  const [executions, setExecutions] = useState<Record<string, FollowUpExecutionRead[]>>({});
  const [questions, setQuestions] = useState<Record<string, MatterIntakeResult>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [replyText, setReplyText] = useState<Record<string, string>>({});
  const [resultOpen, setResultOpen] = useState(false);
  const [resultLoading, setResultLoading] = useState(false);
  const [result, setResult] = useState<MatterIntakeResult | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const rows = await listFollowUps(matterId, null, 50, signal);
      const visible = rows.filter((row) => row.state !== "closed");
      if (signal?.aborted) return;
      setFollowUps(visible);

      const executionPairs = await Promise.all(
        visible.map(async (row) => {
          try {
            const items = await listFollowUpExecutions(row.follow_up_id, 5, signal);
            return [row.follow_up_id, items] as const;
          } catch {
            return [row.follow_up_id, [] as FollowUpExecutionRead[]] as const;
          }
        }),
      );
      if (signal?.aborted) return;
      const executionMap = Object.fromEntries(executionPairs);
      setExecutions(executionMap);

      const waiting = visible
        .map((row) => executionMap[row.follow_up_id]?.[0])
        .filter(
          (item): item is FollowUpExecutionRead =>
            Boolean(item?.local_run_id) && item.state === "waiting_user",
        );
      const questionPairs = await Promise.all(
        waiting.map(async (item) => {
          try {
            return [item.local_run_id as string, await getIntakeResult(item.local_run_id as string, signal)] as const;
          } catch {
            return null;
          }
        }),
      );
      if (signal?.aborted) return;
      setQuestions(
        Object.fromEntries(
          questionPairs.filter(
            (item): item is readonly [string, MatterIntakeResult] => item !== null,
          ),
        ),
      );
    } catch (caught) {
      if (signal?.aborted) return;
      // Older PA builds do not have follow-ups yet; keep the matter usable.
      if (caught instanceof ApiError && caught.status === 404) {
        setFollowUps([]);
        setError(null);
        return;
      }
      setError(caught instanceof Error ? caught.message : "暂时无法读取跟进。");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, [matterId]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === "visible") void load();
    };
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("focus", refresh);
    return () => {
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [load]);

  const changeState = async (followUp: FollowUpRead) => {
    const nextState = followUp.state === "enabled" ? "paused" : "enabled";
    setBusy(`state-${followUp.follow_up_id}`);
    try {
      const latest = await getFollowUp(followUp.follow_up_id);
      const updated = await patchFollowUp(followUp.follow_up_id, {
        expected_version: latest.version,
        state: nextState,
      });
      setFollowUps((current) =>
        current.map((item) =>
          item.follow_up_id === updated.follow_up_id ? updated : item,
        ),
      );
      toast(nextState === "paused" ? "已暂停这条跟进" : "已恢复这条跟进");
      onChanged?.();
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "跟进状态没有更新");
      await load();
    } finally {
      setBusy(null);
    }
  };

  const reply = async (followUp: FollowUpRead, runId: string, text: string) => {
    const value = text.trim();
    if (!value) return;
    setBusy(`reply-${followUp.follow_up_id}`);
    try {
      const response = await intakeMatter(
        value,
        newOperationKey("web-follow-up"),
        runId,
      );
      if (response.outcome === "needs_clarification") {
        toast("还需要你补充一点信息");
      } else if (response.outcome === "failed" || response.outcome === "unknown") {
        toast(response.message || "这次回复还没处理完");
      } else {
        toast("已回复这条跟进");
      }
      setReplyText((current) => ({ ...current, [followUp.follow_up_id]: "" }));
      await load();
      onChanged?.();
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "回复没有发出去");
    } finally {
      setBusy(null);
    }
  };

  const openResult = async (runId: string) => {
    setResultOpen(true);
    setResult(null);
    setResultLoading(true);
    try {
      setResult(await getIntakeResult(runId));
    } catch (caught) {
      toast(caught instanceof Error ? caught.message : "暂时无法读取处理记录");
      setResultOpen(false);
    } finally {
      setResultLoading(false);
    }
  };

  if (!loading && followUps.length === 0 && !error) return null;

  return (
    <section>
      <div className="matter-section-head">
        <h2 className="section-title">跟进</h2>
        {followUps.length > 0 ? <span className="micro">{followUps.length} 项</span> : null}
      </div>

      {loading && followUps.length === 0 ? <LoadingCard label="正在读取跟进…" /> : null}
      {error ? (
        <div className="follow-up-error">
          <p className="caption red">{error}</p>
          <button type="button" className="link-button" onClick={() => void load()}>
            重试
          </button>
        </div>
      ) : null}

      <div className="m-stack-10">
        {followUps.map((followUp) => {
          const latest = executions[followUp.follow_up_id]?.[0] ?? null;
          const question = latest?.local_run_id ? questions[latest.local_run_id] : null;
          const isPaused = followUp.state === "paused";
          const tone = isPaused ? "amber" : "fog";
          const replyValue = replyText[followUp.follow_up_id] ?? "";
          return (
            <article key={followUp.follow_up_id} className="card follow-up-card">
              <div className="follow-up-card__head">
                <div className="grow">
                  <p className="follow-up-card__title">{followUp.title}</p>
                  <p className="micro" style={{ margin: "3px 0 0" }}>
                    {nextCheckLabel(followUp)}
                  </p>
                </div>
                <Pill tone={tone}>{stateLabel(followUp)}</Pill>
              </div>

              {question?.clarify && !isPaused ? (
                <div className="follow-up-question">
                  <p className="follow-up-question__text">{question.clarify}</p>
                  {question.pending_context.choices?.length ? (
                    <div className="chip-row">
                      {question.pending_context.choices.map((choice) => (
                        <button
                          key={choice}
                          type="button"
                          className="chip"
                          disabled={Boolean(busy)}
                          onClick={() => void reply(followUp, latest?.local_run_id ?? "", choice)}
                        >
                          {choice}
                        </button>
                      ))}
                    </div>
                  ) : null}
                  {latest?.local_run_id ? (
                    <form
                      className="follow-up-reply"
                      onSubmit={(event) => {
                        event.preventDefault();
                        void reply(followUp, latest.local_run_id as string, replyValue);
                      }}
                    >
                      <input
                        className="input follow-up-reply__input"
                        value={replyValue}
                        onChange={(event) =>
                          setReplyText((current) => ({
                            ...current,
                            [followUp.follow_up_id]: event.target.value,
                          }))
                        }
                        placeholder="也可以直接回复"
                        aria-label={`回复跟进：${followUp.title}`}
                      />
                      <button
                        type="submit"
                        className="btn btn--primary btn--small"
                        disabled={Boolean(busy) || !replyValue.trim()}
                      >
                        发送
                      </button>
                    </form>
                  ) : null}
                </div>
              ) : latest ? (
                <p className="follow-up-card__latest">
                  {EXECUTION_LABEL[latest.state]}
                  {latest.created_at ? ` · ${formatDateTime(latest.created_at)}` : ""}
                </p>
              ) : null}

              {(executions[followUp.follow_up_id]?.length ?? 0) > 0 ? (
                <details className="follow-up-history">
                  <summary>处理记录 {executions[followUp.follow_up_id].length} 条</summary>
                  <div className="follow-up-history__list">
                    {executions[followUp.follow_up_id].map((execution) => (
                      <div className="follow-up-history__row" key={execution.execution_id}>
                        <span>
                          {EXECUTION_LABEL[execution.state]} · {formatDateTime(execution.created_at)}
                        </span>
                        {execution.local_run_id ? (
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => void openResult(execution.local_run_id as string)}
                          >
                            查看
                          </button>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </details>
              ) : null}

              <div className="follow-up-card__actions">
                <button
                  type="button"
                  className="link-button"
                  disabled={Boolean(busy)}
                  onClick={() => void changeState(followUp)}
                >
                  {isPaused ? <Play size={12} /> : <Pause size={12} />}
                  {isPaused ? "恢复" : "暂停"}
                </button>
                {latest?.local_run_id ? (
                  <button
                    type="button"
                    className="link-button"
                    disabled={Boolean(busy)}
                    onClick={() => void openResult(latest.local_run_id as string)}
                  >
                    查看结果
                  </button>
                ) : null}
              </div>
            </article>
          );
        })}
      </div>

      <Sheet
        title="跟进结果"
        open={resultOpen}
        onClose={() => setResultOpen(false)}
      >
        {resultLoading ? <LoadingCard label="正在读取处理记录…" /> : null}
        {!resultLoading && result ? (
          <div className="m-stack-12">
            <p className="micro" style={{ margin: 0 }}>{formatDateTime(result.created_at)}</p>
            {result.answer ? <Markdown source={result.answer} /> : null}
            {result.clarify ? <p className="body-text">{result.clarify}</p> : null}
            <ApiOperationCard context={result.pending_context} />
          </div>
        ) : null}
        {!resultLoading && !result ? <p className="caption">还没有读取到这条记录。</p> : null}
      </Sheet>
    </section>
  );
}
