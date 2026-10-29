"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { Brain, Pencil, Trash2 } from "lucide-react";

import { MemoryPicker, MemoryCandidates } from "@/components/mobile/account-memory";
import { useDemo } from "@/components/mobile/providers";
import { ConfirmDialog, PageShell, SwitchRow } from "@/components/mobile/ui";

export default function MemoriesPage() {
  const demo = useDemo();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [deleting, setDeleting] = useState<string | null>(null);
  const touchX = useRef<number | null>(null);
  const enabledCount = demo.memories.filter((memory) => memory.enabled).length;

  return (
    <PageShell
      title="个人记忆"
      subtitle="查看 Nexus 账号记忆；提取的候选内容需确认后保存。"
      action={
        <Link href="/me/lesson" className="page-bar__action">
          记一条
        </Link>
      }
    >
      {!demo.enabled ? (
        <>
          <MemoryCandidates />
          <MemoryPicker selected={[]} onChange={() => {}} viewOnly />
          <Link href="/ask">前往问助手，为本次对话选择授权记忆</Link>
        </>
      ) : (
        <>
          <div className="card card--mist">
            <div className="flex items-center gap-10">
              <Brain size={18} className="fog" />
              <p className="card-title grow" style={{ fontSize: 15 }}>
                {enabledCount} 条正在使用
              </p>
            </div>
            <p className="caption" style={{ margin: "6px 0 0" }}>
              停用后仍保留原记录，但不再用于出门清单等本地建议。修改只影响这次演示会话。
            </p>
          </div>

          <div className="m-stack-10 mt-14">
            {demo.memories.map((memory) => (
              <div
                className="card"
                key={memory.id}
                onTouchStart={(event) => { touchX.current = event.touches[0]?.clientX ?? null; }}
                onTouchEnd={(event) => {
                  const start = touchX.current;
                  const end = event.changedTouches[0]?.clientX ?? start;
                  touchX.current = null;
                  if (start !== null && end !== null && start - end > 60 && editing !== memory.id) {
                    setDeleting(memory.id);
                  }
                }}
              >
                <div className="flex items-start justify-between gap-10">
                  <div className="grow">
                    <span className="micro">
                      {memory.place} · {memory.dateText}
                    </span>
                    {editing === memory.id ? (
                      <>
                        <textarea
                          className="textarea mt-8"
                          rows={3}
                          value={draft}
                          onChange={(event) => setDraft(event.target.value)}
                          aria-label="编辑个人经验"
                        />
                        <div className="flex gap-10 mt-8">
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => {
                              if (draft.trim()) demo.updateMemory(memory.id, draft.trim());
                              setEditing(null);
                            }}
                          >
                            保存修改
                          </button>
                          <button
                            type="button"
                            className="link-button"
                            onClick={() => setEditing(null)}
                          >
                            放弃
                          </button>
                        </div>
                      </>
                    ) : (
                      <p className="body-text mt-6" style={{ marginBottom: 0 }}>
                        {memory.text}
                      </p>
                    )}
                  </div>
                </div>

                {editing !== memory.id ? (
                  <button
                    type="button"
                    className="link-button link-button--inline mt-8"
                    onClick={() => {
                      setEditing(memory.id);
                      setDraft(memory.text);
                    }}
                  >
                    <Pencil size={12} /> 编辑这条经验
                  </button>
                ) : null}

                <hr className="divider mt-10" />
                <SwitchRow
                  title={memory.enabled ? "正在使用" : "已停用"}
                  detail={
                    memory.enabled
                      ? "会参与本地演示中的准备清单"
                      : "原记录保留，不再参与建议"
                  }
                  checked={memory.enabled}
                  onChange={() => demo.toggleMemory(memory.id)}
                />
                <p className="micro" style={{ margin: "4px 0 0", lineHeight: 1.6 }}>
                  依据：{memory.source}
                </p>
                <button
                  type="button"
                  className="link-button danger-text mt-8"
                  onClick={() => setDeleting(memory.id)}
                >
                  <Trash2 size={12} /> 删除这条经验
                </button>
              </div>
            ))}
          </div>

          <p className="micro mt-14" style={{ lineHeight: 1.7 }}>
            演示模式 · 这些经验和修改都只保存在当前页面内，刷新后恢复；没有写入 AI 平台，
            也没有自动学习。
          </p>
        </>
      )}
      <ConfirmDialog
        open={Boolean(deleting)}
        title="删除这条经验？"
        body="只删除当前演示会话中的这条本机经验，不会影响服务器或 AI 平台；刷新后可恢复示例。"
        confirmLabel="删除"
        danger
        onCancel={() => setDeleting(null)}
        onConfirm={() => {
          if (deleting) demo.deleteMemory(deleting);
          setDeleting(null);
        }}
      />
    </PageShell>
  );
}
