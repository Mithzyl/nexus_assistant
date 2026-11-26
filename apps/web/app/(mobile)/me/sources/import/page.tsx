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
import { LoginLink, EmptyState, PageShell } from "@/components/mobile/ui";
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
      setError("请选择纯文本笔记文件。");
      return;
    }
    try {
      const text = await file.text();
      if (text.length > MAX_BODY) {
        setError("内容太长，最多 100000 个字。");
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
          "这个来源已经停用上传了",
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
      subtitle="可以粘贴文字，也可以选取文本文件。"
    >
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录生活助手" body="跨设备保存你的笔记。">
          <LoginLink className="btn btn--primary">
            登录账号
          </LoginLink>
        </EmptyState>
      ) : null}

      {signedIn ? (
        <>
          {result ? (
            <div className="card card--green">
              <p className="card-title green" style={{ fontSize: 16 }}>
                {result.created > 0
                  ? "笔记存好了"
                  : result.updated > 0
                    ? "笔记更新好了"
                    : "以前存过这条笔记，没有重复保存"}
              </p>
              <p className="micro" style={{ margin: "6px 0 0" }}>
                同步时间 {new Date(result.last_sync_at).toLocaleString("zh-CN")}
              </p>
              <div className="mt-14" style={{ display: "grid", gap: 8 }}>
                <Link
                  href={sourceId ? `/me/sources/${sourceId}` : "/me/sources"}
                  className="btn btn--secondary"
                >
                  去看看
                </Link>
                <Link href="/me/sources" className="btn btn--secondary">
                  查看全部笔记
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
                  {fileName || "选择文本文件，或在下方粘贴内容。"}
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
                  {sourceId && error === "这个来源已经停用上传了" ? <Link href={`/me/sources/${sourceId}`} className="link-button">去打开</Link> : null}
                </p>
              ) : null}

              <div className="mt-14">
                <PrimaryButton onClick={() => void submit()} busy={busy} disabled={busy}>
                  {busy ? "正在保存…" : "确认导入"}
                </PrimaryButton>
              </div>
              <div className="mt-10">
                <SecondaryButton onClick={() => router.push("/me/sources")}>
                  返回内容来源
                </SecondaryButton>
              </div>
            </>
          )}

        </>
      ) : null}
    </PageShell>
  );
}
