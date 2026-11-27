"use client";

import { useEffect, useState } from "react";

import { CaptureSheet } from "./capture-sheet";
import { AttachmentPreviews } from "./input-attachments";
import { listMatterMarkdown, type InputAttachment } from "@/lib/mobile/input-attachments";
import { getIntakeResult } from "@/lib/mobile/matters-api";
import type { MatterIntakeResponse } from "@/lib/mobile/api";

/** Organize owned matter files without using the separate supplement or scheduling forms. */
export function MatterAttachmentIntake({ matterId, canOrganize, onFinished }: {
  matterId: string;
  canOrganize: boolean;
  onFinished: (result: { draft?: MatterIntakeResponse["draft"] }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<InputAttachment[]>([]);
  const [revision, setRevision] = useState(0);
  const [runId, setRunId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    listMatterMarkdown(matterId, controller.signal).then((items) => {
      if (!controller.signal.aborted) setFiles(items);
    }).catch(() => {
      if (!controller.signal.aborted) setFiles([]);
    });
    return () => controller.abort();
  }, [matterId, revision]);

  useEffect(() => {
    if (!runId) return;
    const controller = new AbortController();
    let timer: number | undefined;
    /** Wait for the same background task and refresh the saved matter once it finishes. */
    async function checkResult(id: string) {
      try {
        const result = await getIntakeResult(id, controller.signal);
        if (controller.signal.aborted) return;
        if (result.outcome === "processing") {
          timer = window.setTimeout(() => void checkResult(id), 1200);
          return;
        }
        setFeedback(result.clarify ?? result.answer ?? (result.outcome === "draft"
          ? "已整理好草稿，可以查看后保存。" : "这次没整理完，已保存的附件还在。"));
        onFinished(result);
      } catch {
        if (!controller.signal.aborted)
          setFeedback("暂时没查到整理结果，可以到问助手查看。");
      }
      if (!controller.signal.aborted) setRunId(null);
    }
    void checkResult(runId);
    return () => {
      controller.abort();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [onFinished, runId]);

  /** Show receipt feedback and refresh the matter's file list. */
  function accepted(result: MatterIntakeResponse) {
    setOpen(false);
    setRevision((value) => value + 1);
    if (result.outcome === "processing") {
      setRunId(result.local_run_id);
      setFeedback("已收到，正在后台整理。");
    } else {
      setFeedback(result.clarify ?? result.answer ?? "");
      onFinished(result);
    }
  }

  return <section>
    {canOrganize ? <button type="button" className="link-button" disabled={Boolean(runId)} onClick={() => setOpen(true)}>
      交给助手整理
    </button> : null}
    {feedback ? <p className="caption" role="status">{feedback}</p> : null}
    <AttachmentPreviews attachments={files} />
    {open ? <CaptureSheet preset="choose" matterId={matterId} onClose={() => setOpen(false)} onAccepted={accepted} /> : null}
  </section>;
}
