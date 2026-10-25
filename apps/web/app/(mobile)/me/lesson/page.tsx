"use client";

import { useState } from "react";
import Link from "next/link";

import { useDemo, useLocalRecords, useUI } from "@/components/mobile/providers";
import { Notice, PageShell } from "@/components/mobile/ui";
import { PrimaryButton } from "@/components/mobile/buttons";

export default function LessonPage() {
  const demo = useDemo();
  const { addRecord } = useLocalRecords();
  const { toast } = useUI();
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<null | { demo: boolean }>(null);

  const save = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    if (demo.enabled) {
      demo.addMemory(trimmed);
      setSaved({ demo: true });
      toast("已加入演示记忆");
    } else {
      addRecord({
        kind: "note",
        title: trimmed.slice(0, 28),
        text: `个人经验：${trimmed}`,
      });
      setSaved({ demo: false });
      toast("已保存在本机记录");
    }
  };

  return (
    <PageShell
      title="记住一点经验"
      subtitle="你主动写下的内容；不是模型自动总结，也不会凭空生成。"
    >
      {saved ? (
        <>
          <div className={`card ${saved.demo ? "card--green" : "card--mist"}`}>
            <p className="card-title" style={{ fontSize: 16 }}>
              {saved.demo ? "这句经验，已放进演示记忆" : "已保存在这台浏览器"}
            </p>
            <p className="caption" style={{ margin: "6px 0 0" }}>
              {saved.demo
                ? "可以在“个人记忆”里编辑或停用；所有改动只存在于当前演示会话。"
                : "服务器目前没有记忆写入接口，所以这句话不会同步到 AI 平台，也不会进入助手记忆。"}
            </p>
          </div>
          <div className="mt-14" style={{ display: "grid", gap: 8 }}>
            <Link href={saved.demo ? "/me/memories" : "/today"} className="btn btn--primary">
              {saved.demo ? "去个人记忆看看" : "回到今天"}
            </Link>
            <button
              type="button"
              className="btn btn--quiet"
              onClick={() => {
                setSaved(null);
                setText("");
              }}
            >
              再记一条经验
            </button>
          </div>
        </>
      ) : (
        <>
          <label className="field" htmlFor="lesson-text">
            <span className="field__label">这次有什么想记住？</span>
            <textarea
              id="lesson-text"
              className="textarea"
              rows={4}
              value={text}
              maxLength={2000}
              onChange={(event) => setText(event.target.value)}
              placeholder="例如：7排视野很好，下次还可以选这里。"
              autoFocus
            />
            <span className="field__hint">
              {text.length} / 2000 字符
            </span>
          </label>
          <PrimaryButton onClick={save} disabled={!text.trim()}>
            {demo.enabled ? "保存到演示记忆" : "保存为本机记录"}
          </PrimaryButton>
          <Notice tone="amber" title={demo.enabled ? "演示模式" : "能力边界"}>
            {demo.enabled
              ? "这条经验只会影响当前页面的本地示例，刷新后恢复；不会写入服务器或 AI 平台。"
              : "当前后端没有记忆新增接口。正常模式只能把这句话保存在本机记录，不能声称已经同步到助手记忆。"}
          </Notice>
          <Link href="/me/memories" className="link-button mt-6">
            查看个人记忆说明
          </Link>
        </>
      )}
    </PageShell>
  );
}
