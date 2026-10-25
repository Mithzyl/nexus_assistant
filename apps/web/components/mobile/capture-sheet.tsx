"use client";

/**
 * Global capture sheet.
 *
 * Demo mode replays the designed sample outcomes and mutates in-memory demo
 * state. Real mode only saves a browser-local record and says so; it never
 * pretends a business matter was created on the server.
 */

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  CheckCircle2,
  ChevronDown,
  FileText,
  Mic,
  RotateCcw,
  SquareArrowDown,
  X,
} from "lucide-react";

import { DEMO_SOURCES, type CaptureOutcome } from "@/lib/mobile/demo";
import { writeSession } from "@/lib/mobile/storage";
import { PrimaryButton, SecondaryButton } from "@/components/mobile/buttons";
import { useDemo, useLocalRecords } from "@/components/mobile/providers";

export function CaptureSheet({
  preset,
  onClose,
}: {
  preset: "choose" | "say";
  onClose: () => void;
}) {
  const router = useRouter();
  const { enabled, captureDemo } = useDemo();
  const { addRecord } = useLocalRecords();

  const [mode, setMode] = useState<"choose" | "say">(preset);
  const [text, setText] = useState("");
  const [sourceId, setSourceId] = useState(DEMO_SOURCES[0].id);
  const [expanded, setExpanded] = useState(false);
  const [stage, setStage] = useState<"compose" | "processing" | "result">("compose");
  const [outcome, setOutcome] = useState<CaptureOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  useEffect(() => {
    if (stage !== "processing") return;
    const timer = window.setTimeout(() => {
      setStage("result");
    }, 700);
    return () => window.clearTimeout(timer);
  }, [stage]);

  const selected = useMemo(
    () => DEMO_SOURCES.find((source) => source.id === sourceId) ?? DEMO_SOURCES[0],
    [sourceId],
  );

  const submit = () => {
    setError(null);
    if (mode === "say" && !text.trim()) {
      setError("先记一句，或试试下面的示例。");
      return;
    }
    if (!enabled) {
      if (!text.trim()) {
        setError("先把要收下的内容粘贴进来。");
        return;
      }
      const title = text.trim().split("\n")[0].slice(0, 28) || "本机记录";
      addRecord({ kind: mode === "say" ? "note" : "capture", title, text: text.trim() });
      setOutcome({
        tone: "done",
        title: "已保存在这台浏览器",
        body:
          "这段内容保存在本机记录里，没有上传到 PA 服务器，也没有生成事务。你可以在“今天”的最近整理里看到它。",
        route: null,
      });
      setStage("result");
      return;
    }
    setStage("processing");
    const result = captureDemo(mode === "choose" ? sourceId : null, text);
    setOutcome(result);
  };

  const reset = () => {
    setStage("compose");
    setOutcome(null);
    setText("");
    setError(null);
  };

  const goToRoute = () => {
    if (!outcome?.route) {
      onClose();
      return;
    }
    const target = outcome.route;
    onClose();
    router.push(target);
  };

  const askDraft = () => {
    const content =
      mode === "choose"
        ? enabled
          ? selected.text
          : text
        : text;
    if (!content.trim()) {
      setError("先写一点内容。");
      return;
    }
    writeSession("pa.web.ask.draft", content);
    onClose();
    router.push("/ask");
  };

  const importNote = () => {
    const content = mode === "choose" ? text : text;
    if (content.trim()) {
      writeSession("pa.web.note.draft", { title: content.split("\n")[0].slice(0, 60), body: content });
    }
    onClose();
    router.push("/me/sources/import");
  };

  return (
    <div
      className="sheet-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="sheet-panel" role="dialog" aria-modal="true" aria-label="收下内容">
        <div className="sheet-handle" />
        <div className="flex items-center gap-10">
          <span className="micro grow">
            {enabled ? "演示模式 · 使用内置示例" : "保存到本机 · 不上传服务器"}
          </span>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭" style={{ width: 40, height: 40 }}>
            <X size={18} />
          </button>
        </div>

        {stage === "processing" ? (
          <div style={{ padding: "64px 0 72px", textAlign: "center" }}>
            <span className="spinner" style={{ margin: "0 auto 18px", width: 24, height: 24 }} />
            <p className="serif" style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>
              收到了，正在理一理。
            </p>
            <p className="caption mt-8" style={{ marginBottom: 0 }}>
              先核对内容，再决定归到哪件事；不猜测缺少的信息。
            </p>
          </div>
        ) : null}

        {stage === "result" && outcome ? (
          <div className="mt-16">
            <div className="flex items-center gap-8">
              {outcome.tone === "done" ? (
                <CheckCircle2 size={16} className="green" />
              ) : outcome.tone === "unclear" ? (
                <FileText size={16} className="amber" />
              ) : (
                <RotateCcw size={16} className="amber" />
              )}
              <span
                className={`micro ${outcome.tone === "done" ? "green" : "amber"}`}
                style={{ fontSize: 12 }}
              >
                {outcome.tone === "done"
                  ? "理好了"
                  : outcome.tone === "unclear"
                    ? "留待确认"
                    : "没有重复处理"}
              </span>
            </div>
            <h2 className="serif mt-10" style={{ fontSize: 24, fontWeight: 600, margin: "10px 0 0" }}>
              {outcome.title}
            </h2>
            <p className="body-text muted mt-10" style={{ marginBottom: 20 }}>
              {outcome.body}
            </p>
            {outcome.route ? (
              <PrimaryButton onClick={goToRoute}>去看看</PrimaryButton>
            ) : (
              <PrimaryButton onClick={onClose}>收起</PrimaryButton>
            )}
            <div className="mt-10">
              <SecondaryButton onClick={reset}>再交一份</SecondaryButton>
            </div>
          </div>
        ) : null}

        {stage === "compose" ? (
          <>
            <div className="mode-switch mt-8" role="tablist" aria-label="收下方式">
              <button
                type="button"
                role="tab"
                aria-selected={mode === "choose"}
                onClick={() => setMode("choose")}
              >
                <SquareArrowDown size={16} /> 收下这个
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === "say"}
                onClick={() => setMode("say")}
              >
                <Mic size={16} /> 记一句
              </button>
            </div>

            <h2 className="hero-line mt-16">
              {mode === "choose" ? "把这份内容，交给助手。" : "想到什么，顺手留一句。"}
            </h2>
            <p className="body-text muted mt-6" style={{ fontSize: 15 }}>
              {enabled
                ? mode === "choose"
                  ? "不用选择记账还是待办。先收下，再关联到对应的事。"
                  : "这里用输入文字体验口述，不会调用麦克风。"
                : mode === "choose"
                  ? "浏览器没有系统分享与读取其他 App 的权限；粘贴或输入的内容只会保存在本机。"
                  : "这句话会保存在这台浏览器，不会写入 PA 服务器。"}
            </p>

            {enabled && mode === "choose" ? (
              <div className="mt-16">
                <div className="flex items-center justify-between gap-10">
                  <span className="micro">本次交来的内容 · 示例</span>
                  <button
                    type="button"
                    className="link-button link-button--inline"
                    onClick={() => setExpanded((value) => !value)}
                    aria-expanded={expanded}
                  >
                    {expanded ? "收起示例" : "换一个示例"}
                    <ChevronDown
                      size={12}
                      style={{ transform: expanded ? "rotate(180deg)" : "none" }}
                    />
                  </button>
                </div>

                {expanded ? (
                  <div className="mt-8" style={{ display: "grid", gap: 6 }}>
                    {DEMO_SOURCES.map((source) => (
                      <button
                        key={source.id}
                        type="button"
                        onClick={() => {
                          setSourceId(source.id);
                          setExpanded(false);
                        }}
                        style={{
                          display: "flex",
                          gap: 10,
                          alignItems: "flex-start",
                          padding: 10,
                          textAlign: "left",
                          border: "0.5px solid var(--m-line)",
                          borderRadius: 12,
                          background:
                            source.id === sourceId ? "var(--m-mist)" : "var(--m-card)",
                        }}
                      >
                        <span
                          aria-hidden="true"
                          style={{
                            width: 16,
                            height: 16,
                            marginTop: 2,
                            borderRadius: "50%",
                            border: `1.5px solid ${source.id === sourceId ? "var(--m-fog)" : "var(--m-faint)"}`,
                            background: source.id === sourceId ? "var(--m-fog)" : "transparent",
                            flex: "0 0 auto",
                          }}
                        />
                        <span className="grow">
                          <span className="card-title" style={{ display: "block", fontSize: 14, fontWeight: 500 }}>
                            {source.title}
                          </span>
                          <span className="caption" style={{ display: "block", fontSize: 12 }}>
                            {source.hint}
                          </span>
                        </span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="card card--tight mt-8">
                    <p className="micro" style={{ margin: 0 }}>
                      <FileText size={12} style={{ display: "inline", verticalAlign: "-1px" }} />{" "}
                      {selected.channel}
                    </p>
                    <p className="card-title mt-6" style={{ fontSize: 15 }}>
                      {selected.title}
                    </p>
                    <p className="caption mt-4" style={{ marginBottom: 0 }}>
                      {selected.text}
                    </p>
                    <hr className="divider mt-10" />
                    <button
                      type="button"
                      className="link-button link-button--inline mt-4"
                      onClick={() => {
                        onClose();
                        router.push(`/matters/evidence/${selected.id}`);
                      }}
                    >
                      先看这次交出的内容 <ArrowRight size={12} />
                    </button>
                  </div>
                )}
              </div>
            ) : null}

            {(!enabled || mode === "say") && (
              <div className="mt-16">
                <label className="field" htmlFor="capture-text">
                  <span className="field__label">
                    {mode === "say" ? "写下这句话" : "粘贴或输入内容"}
                  </span>
                  <textarea
                    id="capture-text"
                    className="textarea"
                    rows={4}
                    value={text}
                    onChange={(event) => setText(event.target.value)}
                    placeholder={
                      mode === "say"
                        ? "例如：下次去江边，记得带件外套。"
                        : "例如：订单号 DEMO-20261015-129，退款 ¥129.00……"
                    }
                    autoFocus={preset === "say"}
                  />
                </label>
                {mode === "say" ? (
                  <div className="chip-row" style={{ marginTop: -4, marginBottom: 12 }}>
                    <button
                      type="button"
                      className="chip chip--sm"
                      onClick={() => setText("下次去江边，记得带件外套。")}
                    >
                      记住一点经验
                    </button>
                    <button
                      type="button"
                      className="chip chip--sm"
                      onClick={() => setText("想去柳江边散散步，留60分钟。")}
                    >
                      留住一个念头
                    </button>
                  </div>
                ) : null}
              </div>
            )}

            {error ? (
              <p className="caption red" role="alert" style={{ marginBottom: 10 }}>
                {error}
              </p>
            ) : null}

            <PrimaryButton onClick={submit}>
              {mode === "choose" ? "收下这个" : "把这句话交给助手"}
            </PrimaryButton>

            {!enabled ? (
              <div className="mt-10" style={{ display: "grid", gap: 8 }}>
                <SecondaryButton onClick={askDraft}>带着这段内容去提问</SecondaryButton>
                <SecondaryButton onClick={importNote}>导入为云端笔记</SecondaryButton>
              </div>
            ) : null}

            <p className="micro text-center mt-14" style={{ lineHeight: 1.6 }}>
              {enabled
                ? "仅整理你在这里选中的示例。不会读取真实屏幕、通知、相册或其他应用。"
                : "只保存在本机浏览器存储中；不会读取真实屏幕、通知或相册。"}
            </p>
          </>
        ) : null}

      </div>
    </div>
  );
}
