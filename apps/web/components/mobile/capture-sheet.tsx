"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { X } from "lucide-react";
import { newOperationKey, requestTodayRefresh, submitCapture } from "@/lib/mobile/matters-api";

import { PrimaryButton } from "@/components/mobile/buttons";
import { useAuth, useDemo, useUI } from "@/components/mobile/providers";
import { accountKey, readSession, writeSession } from "@/lib/mobile/storage";

/** Accept captured text and keep the sheet available for the next entry. */
export function CaptureSheet({
  preset,
  onClose,
}: {
  preset: "choose" | "say";
  onClose: () => void;
}) {
  const { state, user } = useAuth();
  const { enabled, captureDemo } = useDemo();
  const { toast } = useUI();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [accepting, setAccepting] = useState(false);
  const storageKey = accountKey("pa.web.capture.pending", user?.user_id ?? null);

  useEffect(() => {
    setText(readSession<{ text: string } | null>(storageKey, null)?.text ?? "");
  }, [storageKey]);
  const title = preset === "say" ? "随手记一条" : "交给助手整理";

  useEffect(() => {
    document.body.classList.add("m-sheet-open");
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.body.classList.remove("m-sheet-open");
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  /** Retry the same saved text with its original key until the server acknowledges receipt. */
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = text.trim();
    if (!value || accepting) return;
    if (value.length > 4000) {
      setError("这段有点长，最多 4000 个字。");
      return;
    }
    if (enabled) {
      toast(captureDemo(null, text).title);
      onClose();
      return;
    }
    if (state !== "signedIn" || !user) {
      setError(state === "offline" || state === "loading"
        ? "暂时无法确认登录状态，原文已保留。"
        : "请先登录再发送，原文已保留。");
      return;
    }
    const saved = readSession<{ text: string; key: string } | null>(storageKey, null);
    const key = saved?.text.trim() === value ? saved.key : newOperationKey("web-capture");
    writeSession(storageKey, { text, key });
    setAccepting(true);
    setError(null);
    try {
      await submitCapture(value, key);
      if (readSession<{ key: string } | null>(storageKey, null)?.key === key) {
        writeSession(storageKey, null);
      }
      setText((current) => current === text ? "" : current);
      toast("已记下，正在后台整理");
      requestTodayRefresh();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "提交暂未确认，原文已保留。");
    } finally {
      setAccepting(false);
    }
  };

  return (
    <div
      className="sheet-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="sheet-panel" role="dialog" aria-modal="true" aria-labelledby="capture-title">
        <div className="sheet-handle" />
        <div className="flex items-center justify-between gap-10">
          <h2 id="capture-title" className="hero-line">{title}</h2>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X size={18} />
          </button>
        </div>
        <form className="mt-16" onSubmit={submit}>
          <label className="field" htmlFor="capture-text">
            <span className="field__label">输入或粘贴文字</span>
            <textarea
              id="capture-text"
              className="textarea"
              rows={4}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
                setError(null);
              }}
              readOnly={accepting}
              autoFocus
            />
          </label>
          {error ? <p className="caption red" role="alert">{error}</p> : null}
          <PrimaryButton type="submit" disabled={!text.trim() || accepting}>{accepting ? "正在提交…" : "发送给助手"}</PrimaryButton>
        </form>
      </div>
    </div>
  );
}
