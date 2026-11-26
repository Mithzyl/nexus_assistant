"use client";

import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { X } from "lucide-react";
import { newOperationKey, requestTodayRefresh, submitCapture } from "@/lib/mobile/matters-api";

import { PrimaryButton } from "@/components/mobile/buttons";
import { useAuth, useDemo, useUI } from "@/components/mobile/providers";
import { ApiError } from "@/lib/mobile/api";
import { loginHref } from "@/lib/mobile/login-return.mjs";
import { accountKey, CAPTURE_LOGIN_DRAFT_KEY, readSession, writeSession } from "@/lib/mobile/storage";

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
  const [loginSaved, setLoginSaved] = useState(false);
  const [retrySend, setRetrySend] = useState(false);
  const storageKey = accountKey("pa.web.capture.pending", user?.user_id ?? null);

  useEffect(() => {
    const resume = new URLSearchParams(window.location.search).get("capture") === "resume";
    const restored = resume && state === "signedIn"
      ? readSession<{ text: string } | null>(CAPTURE_LOGIN_DRAFT_KEY, null) : null;
    setText(restored?.text ?? readSession<{ text: string } | null>(storageKey, null)?.text ?? "");
  }, [state, storageKey]);
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
      if (state === "anonymous") {
        writeSession(CAPTURE_LOGIN_DRAFT_KEY, { text, preset });
        const saved = readSession<{ text: string; preset: string } | null>(CAPTURE_LOGIN_DRAFT_KEY, null);
        const retained = saved?.text === text && saved.preset === preset;
        setLoginSaved(retained);
        setError(retained ? "登录后就能发出去，刚写的内容已经替你留好了" : "请先复制文字，再登录发送");
      } else {
        setError("还没确认登录状态，请再试一次");
      }
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
      const currentUrl = new URL(window.location.href);
      if (currentUrl.searchParams.get("capture") === "resume") {
        writeSession(CAPTURE_LOGIN_DRAFT_KEY, null);
        currentUrl.searchParams.delete("capture");
        window.history.replaceState(window.history.state, "", currentUrl.pathname + currentUrl.search + currentUrl.hash);
      }
      setText((current) => current === text ? "" : current);
      setRetrySend(false);
      toast("已记下，正在后台整理");
      requestTodayRefresh();
    } catch (caught) {
      const uncertain = !(caught instanceof ApiError) || caught.status >= 500;
      setRetrySend(uncertain);
      setError(uncertain ? "还没确认有没有发成功" : "这次没能发送，请检查后再试");
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
                setLoginSaved(false);
                setRetrySend(false);
              }}
              readOnly={accepting}
              autoFocus
            />
          </label>
          {error ? <p className="caption red" role="alert">{error}</p> : null}
          {loginSaved ? <a className="btn btn--primary" href={loginHref("/today?capture=resume")}>登录并发送</a> :
            <PrimaryButton type="submit" disabled={!text.trim() || accepting}>
              {accepting ? "正在提交…" : retrySend ? "再试一次" : "发送给助手"}
            </PrimaryButton>}
        </form>
      </div>
    </div>
  );
}
