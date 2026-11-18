"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Trash2 } from "lucide-react";

import type { DataSource } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { formatDateTime } from "@/lib/mobile/format";
import { uploadFailureText, uploadStatusText } from "@/lib/mobile/source-labels";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { useAuth } from "@/components/mobile/providers";
import {
  ConfirmDialog,
  ErrorCard,
  LoadingCard,
  Notice,
  PageShell,
  Pill,
  SwitchRow,
} from "@/components/mobile/ui";

const TYPE_LABEL: Record<DataSource["source_type"], string> = {
  calendar: "日历",
  reminders: "提醒事项",
  notes: "笔记",
};

const FIELD_LABEL: Record<string, string> = {
  title: "标题",
  start: "开始时间",
  end: "结束时间",
  location: "地点",
  notes: "备注",
  status: "状态",
  due: "到期时间",
  body: "正文",
  tags: "标签",
};

const FIELDS: Record<DataSource["source_type"], string[]> = {
  calendar: ["title", "start", "end", "location", "notes", "status"],
  reminders: ["title", "due", "notes", "status"],
  notes: ["title", "body", "tags"],
};

export default function SourceDetailPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const source = useApiResource<DataSource>(
    signedIn ? `/api/pa/data-sources/${params.id}` : null,
    { enabled: signedIn },
  );
  const [saving, setSaving] = useState<string | null>(null);
  const [fieldSaving, setFieldSaving] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<"items" | "source" | null>(null);
  const [deleting, setDeleting] = useState(false);

  const update = async (body: Partial<DataSource>, key: string) => {
    setSaving(key);
    setActionError(null);
    try {
      const updated = await apiRequest<DataSource>(
        `/api/pa/data-sources/${params.id}`,
        { method: "PATCH", body },
      );
      source.setData(updated);
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "更新未能完成。");
    } finally {
      setSaving(null);
    }
  };

  const toggleField = async (field: string) => {
    if (!data || field === "title" || fieldSaving) return;
    const next = data.upload_fields.includes(field)
      ? data.upload_fields.filter((name) => name !== field)
      : [...data.upload_fields, field];
    if (!next.includes("title")) next.unshift("title");
    setFieldSaving(field);
    setActionError(null);
    try {
      const updated = await apiRequest<DataSource>(
        `/api/pa/data-sources/${params.id}`,
        { method: "PATCH", body: { upload_fields: next } },
      );
      source.setData(updated);
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "可同步的信息没能更新。");
    } finally {
      setFieldSaving(null);
    }
  };

  const remove = async () => {
    setDeleting(true);
    setActionError(null);
    try {
      if (confirm === "items") {
        const result = await apiRequest<{ deleted: number }>(
          `/api/pa/data-sources/${params.id}/items`,
          { method: "DELETE" },
        );
        setConfirm(null);
        await source.reload();
        source.setData(
          source.data ? { ...source.data, item_count: 0, last_sync_at: null } : source.data,
        );
        setActionError(`已删除 ${result.deleted} 条副本；来源和授权保留。`);
      } else if (confirm === "source") {
        await apiRequest<void>(`/api/pa/data-sources/${params.id}`, { method: "DELETE" });
        router.replace("/me/sources");
      }
    } catch (caught) {
      setActionError(caught instanceof ApiError ? caught.message : "删除未能完成。");
      setConfirm(null);
    } finally {
      setDeleting(false);
    }
  };

  const data = source.data;

  return (
    <PageShell
      title={data?.display_name || "来源详情"}
      subtitle={data ? `${TYPE_LABEL[data.source_type]} · ${data.item_count} 条副本` : undefined}
    >
      {source.loading ? <LoadingCard label="正在读取来源…" /> : null}
      {source.error ? (
        <ErrorCard message={errorText(source.error)} onRetry={source.reload} />
      ) : null}

      {data ? (
        <>
          <div className="card">
            <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
              <Pill tone={data.sync_enabled ? "fog" : "amber"}>
                {data.sync_enabled ? "继续上传" : "已停止上传"}
              </Pill>
              <Pill tone={data.assistant_enabled ? "green" : "neutral"}>
                {data.assistant_enabled ? "助手可读" : "助手不可读"}
              </Pill>
              {data.last_sync_status ? (
                <Pill tone={data.last_sync_status === "ok" ? "green" : "amber"}>
                  最近同步{uploadStatusText(data.last_sync_status)}
                </Pill>
              ) : null}
            </div>
            <p className="caption mt-10" style={{ marginBottom: 0 }}>
              {data.last_sync_at
                ? `最近同步于 ${formatDateTime(data.last_sync_at)}`
                : "还没有成功同步的记录。"}
              {data.last_error_code ? ` ${uploadFailureText(data.last_error_code)}` : ""}
            </p>
          </div>

          <div className="card mt-14">
            <SwitchRow
              title="继续上传新批次"
              detail="关闭后已有副本保留，只是不再接受新的上传；不等于删除。"
              checked={data.sync_enabled}
              busy={saving === "sync"}
              onChange={() => void update({ sync_enabled: !data.sync_enabled }, "sync")}
            />
            <hr className="divider" />
            <SwitchRow
              title="允许助手读取"
              detail="关闭后助手工具不再读取该来源；你仍可在这里查询已有副本。"
              checked={data.assistant_enabled}
              busy={saving === "assistant"}
              onChange={() =>
                void update({ assistant_enabled: !data.assistant_enabled }, "assistant")
              }
            />
          </div>

          {actionError ? (
            <p className="caption mt-10" style={{ color: actionError.includes("已删除") ? "var(--m-green)" : "var(--m-red)" }} role="status">
              {actionError}
            </p>
          ) : null}

          <div className="card mt-14">
            <p className="card-title" style={{ fontSize: 15 }}>
              允许同步的信息
            </p>
            <p className="caption" style={{ margin: "4px 0 10px" }}>
              修改只影响之后的同步；手机上原来的内容不会被改动。
            </p>
            <div style={{ display: "grid", gap: 4 }}>
              {FIELDS[data.source_type].map((field) => {
                const allowed = data.upload_fields.includes(field);
                const locked = field === "title" || Boolean(fieldSaving);
                return (
                  <label className="check" key={field} style={{ minHeight: 36, padding: "4px 0", opacity: locked && field !== "title" ? 0.65 : 1 }}>
                    <input
                      type="checkbox"
                      checked={allowed}
                      disabled={locked}
                      onChange={() => void toggleField(field)}
                    />
                    <span style={{ fontSize: 14 }}>
                      {FIELD_LABEL[field] || field}
                      {field === "title" ? "（必须）" : ""}
                      {fieldSaving === field ? " · 保存中…" : ""}
                    </span>
                  </label>
                );
              })}
            </div>
          </div>

          <div className="card mt-14">
            <p className="card-title" style={{ fontSize: 15 }}>
              可查看的范围
            </p>
            <p className="caption" style={{ margin: "4px 0 10px" }}>
              这里只显示你已经授权的范围；要调整，请在手机上操作。
            </p>
            {data.scope_keys.length === 0 ? (
              <p className="caption" style={{ margin: 0 }}>
                还没设置范围；上传后可以在这里查看。
              </p>
            ) : (
              <p className="caption" style={{ margin: 0 }}>
                当前已授权 {data.scope_keys.length} 个范围。具体范围在手机上选择，这里只显示数量。
              </p>
            )}
          </div>

          <Notice tone="amber" title="删除分两层">
            删除副本会保留来源和授权，之后仍可重新同步；删除来源会连同全部副本一起移除。两者都不会影响设备上的原始数据。
          </Notice>

          <div className="mt-14" style={{ display: "grid", gap: 10 }}>
            <Link href="/me/sources" className="btn btn--secondary">
              去已同步内容里查看
            </Link>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => setConfirm("items")}
            >
              删除副本
            </button>
            <button
              type="button"
              className="btn btn--danger"
              onClick={() => setConfirm("source")}
            >
              <Trash2 size={14} /> 删除整个来源
            </button>
          </div>
        </>
      ) : null}

      <ConfirmDialog
        open={confirm !== null}
        title={confirm === "items" ? "删除副本？" : "删除整个来源？"}
        body={
          confirm === "items"
            ? "只删除账号里的副本；授权保留，之后还能再传。手机上的内容不受影响。"
            : "删除来源与它的全部副本，之后需要重新登记来源并上传。不会影响设备上的原始数据。"
        }
        confirmLabel={confirm === "items" ? "删除副本" : "删除来源"}
        danger
        busy={deleting}
        onCancel={() => setConfirm(null)}
        onConfirm={() => void remove()}
      />
    </PageShell>
  );
}
