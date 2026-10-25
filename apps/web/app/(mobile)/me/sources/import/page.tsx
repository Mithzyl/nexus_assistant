"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { FileText } from "lucide-react";

import type { DataSource, SyncResult } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { sha256Hex, uuid } from "@/lib/mobile/format";
import { installationId, readSession, writeSession } from "@/lib/mobile/storage";
import { useAuth } from "@/components/mobile/providers";
import { EmptyState, Notice, PageShell } from "@/components/mobile/ui";
import { PrimaryButton, SecondaryButton } from "@/components/mobile/buttons";

interface Draft {
  title?: string;
  body?: string;
}

const MAX_BODY = 100_000;
const MAX_TITLE = 255;

export default function ImportNotePage() {
  const router = useRouter();
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SyncResult | null>(null);
  const [sourceId, setSourceId] = useState<string | null>(null);

  useEffect(() => {
    const draft = readSession<Draft | null>("pa.web.note.draft", null);
    if (draft?.body) {
      setTitle(draft.title || "");
      setBody(draft.body);
      writeSession("pa.web.note.draft", null);
    }
  }, []);

  const chooseFile = async (file: File | null) => {
    setError(null);
    setResult(null);
    if (!file) return;
    if (file.size > 200 * 1024) {
      setError("文件超过 200 KB。这里只支持较小的纯文本或 Markdown。");
      return;
    }
    if (!/\.(txt|md|markdown)$/i.test(file.name)) {
      setError("只支持 .txt / .md 纯文本。不支持 Office 文件、PDF、图片或 OCR。");
      return;
    }
    try {
      const text = await file.text();
      if (text.length > MAX_BODY) {
        setError("文件内容超过 100000 个字符，服务器不接受更大的正文。");
        return;
      }
      const heading = /^#\s+(.+)$/m.exec(text);
      const derivedTitle =
        heading?.[1]?.trim() ||
        file.name.replace(/\.(txt|md|markdown)$/i, "").slice(0, MAX_TITLE) ||
        "未命名笔记";
      setTitle(derivedTitle);
      setBody(heading ? text.replace(/^#\s+.+\n?/, "").trim() : text);
      setFileName(file.name);
    } catch {
      setError("无法读取这个文件，请换成纯文本。");
    }
  };

  const submit = async () => {
    const trimmedTitle = title.trim();
    const trimmedBody = body.trim();
    if (!trimmedTitle) {
      setError("给这条笔记写一个标题。");
      return;
    }
    if (!trimmedBody) {
      setError("笔记正文为空。");
      return;
    }
    if (trimmedTitle.length > MAX_TITLE) {
      setError("标题最多 255 个字符。");
      return;
    }
    if (trimmedBody.length > MAX_BODY) {
      setError("正文最多 100000 个字符。");
      return;
    }

    setBusy(true);
    setError(null);
    setResult(null);

    try {
      const deviceId = installationId();
      const scopeKey = `browser:${deviceId}`;
      const sources = await apiRequest<DataSource[]>("/api/pa/data-sources");
      let source = sources.find(
        (item) => item.source_type === "notes" && item.scope_keys.includes(scopeKey),
      );
      if (!source) {
        source = await apiRequest<DataSource>("/api/pa/data-sources", {
          method: "POST",
          body: {
            source_type: "notes",
            device_id: deviceId,
            display_name: "浏览器导入笔记",
            scope_keys: [scopeKey],
            upload_fields: ["title", "body"],
            sync_enabled: true,
            assistant_enabled: false,
          },
        });
      }
      setSourceId(source.source_id);

      if (!source.sync_enabled) {
        throw new ApiError(
          409,
          "sync_disabled",
          "这个来源已停止上传。请先在来源详情里重新开启“继续上传”。",
          true,
        );
      }

      const digest = await sha256Hex(`${trimmedTitle}\u001f${trimmedBody}`);
      const sync = await apiRequest<SyncResult>(
        `/api/pa/data-sources/${source.source_id}/sync`,
        {
          method: "POST",
          body: {
            batch_id: uuid(),
            items: [
              {
                external_id: `browser-note:${digest.slice(0, 48)}`,
                scope_key: scopeKey,
                version: 1,
                updated_at: new Date().toISOString(),
                title: trimmedTitle,
                body: trimmedBody,
              },
            ],
            removed_external_ids: [],
          },
        },
      );
      setResult(sync);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.status === 401
            ? "登录状态已失效，请重新登录。"
            : caught.message
          : "导入未能完成，内容仍保留在表单里。",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageShell
      title="导入笔记"
      subtitle="只支持纯文本与 Markdown；确认后才会写入服务器。"
    >
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后才能导入" body="上传会写入服务器上的笔记来源。">
          <Link href="/login" className="btn btn--primary">
            登录 PA 账号
          </Link>
        </EmptyState>
      ) : null}

      {signedIn ? (
        <>
          {result ? (
            <div className="card card--green">
              <p className="card-title green" style={{ fontSize: 16 }}>
                {result.created > 0
                  ? "已保存到服务器"
                  : result.updated > 0
                    ? "已更新已有副本"
                    : "内容已存在，没有新增副本"}
              </p>
              <p className="caption" style={{ margin: "6px 0 0" }}>
                新建 {result.created} · 更新 {result.updated} · 未改动 {result.unchanged}
              </p>
              <p className="micro" style={{ margin: "6px 0 0" }}>
                服务器时间 {new Date(result.last_sync_at).toLocaleString("zh-CN")} · 这条笔记来自浏览器导入
              </p>
              <div className="mt-14" style={{ display: "grid", gap: 8 }}>
                <Link
                  href={sourceId ? `/me/sources/${sourceId}` : "/me/sources"}
                  className="btn btn--secondary"
                >
                  查看来源与字段授权
                </Link>
                <Link href="/me/sources" className="btn btn--secondary">
                  去云端内容搜索
                </Link>
                <button
                  type="button"
                  className="btn btn--quiet"
                  onClick={() => {
                    setResult(null);
                    setTitle("");
                    setBody("");
                    setFileName(null);
                  }}
                >
                  再导入一条
                </button>
              </div>
            </div>
          ) : (
            <>
              <label className="field" htmlFor="note-file">
                <span className="field__label">选择文本文件（可选）</span>
                <input
                  id="note-file"
                  className="input"
                  type="file"
                  accept=".txt,.md,.markdown,text/plain,text/markdown"
                  onChange={(event) => void chooseFile(event.target.files?.[0] ?? null)}
                  style={{ paddingTop: 10 }}
                />
                <span className="field__hint">
                  {fileName ? `已读取 ${fileName}` : "只读取本地文本，不会上传未经确认的文件。"}
                </span>
              </label>

              <label className="field" htmlFor="note-title">
                <span className="field__label">标题</span>
                <input
                  id="note-title"
                  className="input"
                  value={title}
                  maxLength={MAX_TITLE}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="例如：牙医复诊记录"
                />
              </label>

              <label className="field" htmlFor="note-body">
                <span className="field__label">正文</span>
                <textarea
                  id="note-body"
                  className="textarea"
                  rows={8}
                  value={body}
                  onChange={(event) => setBody(event.target.value)}
                  placeholder="粘贴或输入笔记正文……"
                />
                <span className="field__hint">
                  {body.length} / {MAX_BODY} 字符
                </span>
              </label>

              {title || body ? (
                <div className="card card--tight mb-0">
                  <p className="micro">
                    <FileText size={12} style={{ display: "inline", verticalAlign: "-1px" }} /> 预览
                  </p>
                  <p className="card-title mt-4" style={{ fontSize: 15 }}>
                    {title || "（未填写标题）"}
                  </p>
                  <p
                    className="caption mt-4"
                    style={{ marginBottom: 0, whiteSpace: "pre-wrap", maxHeight: 160, overflow: "hidden" }}
                  >
                    {body.slice(0, 400) || "（正文为空）"}
                    {body.length > 400 ? "…" : ""}
                  </p>
                </div>
              ) : null}

              {error ? (
                <p className="caption red" role="alert" style={{ margin: "12px 0 0" }}>
                  {error}
                </p>
              ) : null}

              <div className="mt-14">
                <PrimaryButton onClick={() => void submit()} busy={busy} disabled={busy}>
                  {busy ? "正在写入服务器…" : "确认导入"}
                </PrimaryButton>
              </div>
              <div className="mt-10">
                <SecondaryButton onClick={() => router.push("/me/sources")}>
                  返回内容来源
                </SecondaryButton>
              </div>
            </>
          )}

          <Notice tone="amber" title="能力边界">
            浏览器只能读取你选择的文本文件；不读取 Apple 备忘录，不做 OCR，不支持 Word/PDF/表格。
            选择文件不等于同步成功，只有上面的保存结果才算写入。
          </Notice>
          <p className="micro mt-12" style={{ lineHeight: 1.7 }}>
            导入后会创建一个独立的“浏览器导入笔记”来源（范围键以本机安装 ID 区分），
            不会与 iPhone 上传的日历、提醒或笔记混在一起。默认助手不可读，可在来源详情里单独授权。
          </p>
        </>
      ) : null}
    </PageShell>
  );
}
