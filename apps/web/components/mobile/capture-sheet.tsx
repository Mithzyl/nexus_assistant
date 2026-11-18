"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { useRouter } from "next/navigation";
import { X } from "lucide-react";

import { PrimaryButton } from "@/components/mobile/buttons";
import { useAuth, useDemo, useUI } from "@/components/mobile/providers";
import { accountKey, writeSession } from "@/lib/mobile/storage";

/** Send captured text through the existing Ask workflow. */
export function CaptureSheet({
  preset,
  onClose,
}: {
  preset: "choose" | "say";
  onClose: () => void;
}) {
  const router = useRouter();
  const { state, user } = useAuth();
  const { enabled, captureDemo } = useDemo();
  const { setCaptureSubmission, toast } = useUI();
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
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

  /** Keep the original text in the account's composer before navigating. */
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = text.trim();
    if (!value) return;
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
    writeSession(accountKey("pa.web.ask.composer", user.user_id), text);
    setCaptureSubmission({ userId: user.user_id, text });
    onClose();
    router.push("/ask");
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
              autoFocus
            />
          </label>
          {error ? <p className="caption red" role="alert">{error}</p> : null}
          <PrimaryButton type="submit" disabled={!text.trim()}>发送给助手</PrimaryButton>
        </form>
      </div>
    </div>
  );
}
