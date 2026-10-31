"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CalendarDays,
  Clipboard,
  Database,
  Radio,
  RotateCw,
  Smartphone,
  Trash2,
} from "lucide-react";

import type {
  CalendarSubscription,
  CalendarSubscriptionStatus,
  DataSource,
  MatterSummary,
  ShortcutToken,
  ShortcutTokenCreated,
} from "@/lib/mobile/api";
import {
  createCalendarSubscription,
  getCalendarSubscription,
  revokeCalendarSubscription,
  rotateCalendarSubscription,
  updateCalendarSubscription,
} from "@/lib/mobile/calendar-api";
import { formatDateTime } from "@/lib/mobile/format";
import {
  createShortcutSource,
  createShortcutToken,
  revokeShortcutToken,
} from "@/lib/mobile/shortcuts-api";
import { uploadFailureText } from "@/lib/mobile/source-labels";
import { installationId } from "@/lib/mobile/storage";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import { ConfirmDialog, Pill } from "./ui";

function sourceRange(source: DataSource): string {
  if (source.scope_keys.length === 0) return "范围尚未列出";
  return `${source.scope_keys.length} 个已授权范围`;
}

function matterLabel(matters: MatterSummary[], ids: string[]): string {
  const titles = matters
    .filter((matter) => ids.includes(matter.matter_id))
    .map((matter) => matter.title);
  if (titles.length === 0) return `${ids.length} 个所选事项`;
  return titles.slice(0, 2).join("、") + (titles.length > 2 ? `等 ${titles.length} 项` : "");
}

/** Three independent data connections and their real management operations. */
export function DataConnections({
  signedIn,
  mattersLoading,
  mattersError,
  matters,
  sourcesLoading,
  sourcesError,
  sources,
  onRetry,
  onSourcesChanged,
}: {
  signedIn: boolean;
  mattersLoading: boolean;
  mattersError: boolean;
  matters: MatterSummary[];
  sourcesLoading: boolean;
  sourcesError: boolean;
  sources: DataSource[];
  onRetry: () => void;
  onSourcesChanged: () => void;
}) {
  const subscription = useApiResource<CalendarSubscriptionStatus>(
    signedIn ? "/api/pa/calendar-subscription" : null,
    { enabled: signedIn },
  );
  const shortcutTokens = useApiResource<ShortcutToken[]>(
    signedIn ? "/api/pa/sync/ios/shortcut-tokens" : null,
    { enabled: signedIn },
  );
  const [selectedMatterIds, setSelectedMatterIds] = useState<string[]>([]);
  const [privateSubscription, setPrivateSubscription] =
    useState<CalendarSubscription | null>(null);
  const [calendarBusy, setCalendarBusy] = useState<string | null>(null);
  const [calendarError, setCalendarError] = useState<string | null>(null);
  const [confirmCalendarAction, setConfirmCalendarAction] = useState<
    "close" | "rotate" | null
  >(null);
  const [openedSubscription, setOpenedSubscription] = useState(false);

  const [shortcutSourceId, setShortcutSourceId] = useState("");
  const [newSourceType, setNewSourceType] = useState<"calendar" | "reminders">("calendar");
  const [newSourceName, setNewSourceName] = useState("快捷指令上传");
  const [shortcutCredential, setShortcutCredential] =
    useState<ShortcutTokenCreated | null>(null);
  const [shortcutBusy, setShortcutBusy] = useState<string | null>(null);
  const [shortcutError, setShortcutError] = useState<string | null>(null);
  const [copiedSecret, setCopiedSecret] = useState<"feed" | "credential" | null>(null);

  const currentSubscription = subscription.data?.subscription ?? null;
  const shortcutSources = sources.filter(
    (source) => source.source_type === "calendar" || source.source_type === "reminders",
  );
  const recentSync = shortcutSources
    .map((source) => source.last_sync_at)
    .filter((value): value is string => Boolean(value))
    .sort()
    .at(-1) ?? null;
  const uploadState = !signedIn
    ? "需登录"
    : sourcesError
      ? "读取失败"
      : sourcesLoading
        ? "读取中"
        : shortcutSources.length > 0
          ? "已建立来源"
          : "尚未连接";
  const appState = !signedIn
    ? "需登录"
    : mattersError
      ? "读取失败"
      : mattersLoading
        ? "读取中"
        : "可用";

  useEffect(() => {
    const ids = currentSubscription?.matter_ids ?? [];
    setSelectedMatterIds((current) =>
      current.length === 0 || !currentSubscription
        ? ids
        : current.filter((id) => matters.some((matter) => matter.matter_id === id)),
    );
  }, [currentSubscription?.subscription_id, currentSubscription?.matter_ids, matters]);

  useEffect(() => {
    if (shortcutSourceId || shortcutSources.length === 0) return;
    setShortcutSourceId(shortcutSources[0].source_id);
  }, [shortcutSourceId, shortcutSources]);

  const copySecret = async (
    kind: "feed" | "credential",
    value: string | null | undefined,
  ) => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopiedSecret(kind);
      window.setTimeout(() => setCopiedSecret(null), 1600);
    } catch {
      setCalendarError("复制失败，请长按选择链接。");
    }
  };

  const refreshSubscription = async () => {
    const next = await getCalendarSubscription();
    subscription.setData(next);
    return next;
  };

  const createOrRotate = async (rotate: boolean) => {
    if (selectedMatterIds.length === 0) {
      setCalendarError("请至少选择一个要共享的事项。");
      return;
    }
    setCalendarBusy(rotate ? "rotate" : "create");
    setCalendarError(null);
    setOpenedSubscription(false);
    try {
      const created = rotate
        ? await rotateCalendarSubscription(selectedMatterIds)
        : await createCalendarSubscription(selectedMatterIds);
      setPrivateSubscription(created);
      await refreshSubscription();
    } catch (caught) {
      setCalendarError(caught instanceof Error ? caught.message : "订阅链接未能生成。");
    } finally {
      setCalendarBusy(null);
      setConfirmCalendarAction(null);
    }
  };

  const updateSubscriptionRange = async () => {
    if (selectedMatterIds.length === 0) {
      setCalendarError("请至少保留一个共享事项。");
      return;
    }
    setCalendarBusy("range");
    setCalendarError(null);
    try {
      const updated = await updateCalendarSubscription({ matter_ids: selectedMatterIds });
      subscription.setData({ exists: true, subscription: updated });
    } catch (caught) {
      setCalendarError(caught instanceof Error ? caught.message : "订阅范围未能更新。");
    } finally {
      setCalendarBusy(null);
    }
  };

  const closeSubscription = async () => {
    setCalendarBusy("close");
    setCalendarError(null);
    try {
      const closed = await revokeCalendarSubscription();
      subscription.setData({ exists: true, subscription: closed });
      setPrivateSubscription(null);
      setConfirmCalendarAction(null);
    } catch (caught) {
      setCalendarError(caught instanceof Error ? caught.message : "订阅未能关闭。");
    } finally {
      setCalendarBusy(null);
    }
  };

  const createShortcut = async () => {
    setShortcutBusy("create");
    setShortcutError(null);
    setShortcutCredential(null);
    try {
      let sourceId = shortcutSourceId;
      if (!sourceId) {
        const source = await createShortcutSource({
          source_type: newSourceType,
          device_id: `shortcut-${newSourceType}-${installationId()}`,
          display_name: newSourceName.trim() || "快捷指令上传",
        });
        sourceId = source.source_id;
        setShortcutSourceId(sourceId);
        onSourcesChanged();
      }
      const created = await createShortcutToken(
        sourceId,
        "手动上传快捷指令",
      );
      setShortcutCredential(created);
      shortcutTokens.reload();
    } catch (caught) {
      setShortcutError(caught instanceof Error ? caught.message : "上传连接未能生成。");
    } finally {
      setShortcutBusy(null);
    }
  };

  const revokeShortcut = async (tokenId: string) => {
    setShortcutBusy(tokenId);
    setShortcutError(null);
    try {
      const revoked = await revokeShortcutToken(tokenId);
      shortcutTokens.setData(
        (shortcutTokens.data ?? []).map((token) =>
          token.token_id === revoked.token_id ? revoked : token,
        ),
      );
    } catch (caught) {
      setShortcutError(caught instanceof Error ? caught.message : "上传连接未能撤销。");
    } finally {
      setShortcutBusy(null);
    }
  };

  return (
    <section style={{ padding: "0 var(--m-gutter)" }}>
      <h2 className="section-title" style={{ fontSize: 17, marginBottom: 8 }}>
        同步方式
      </h2>
      <div className="m-stack-10">
        <details className="connection-card">
          <summary>
            <span className="tile tile--sm"><CalendarDays size={15} /></span>
            <span className="grow">
              <span className="connection-card__title">日程与提醒</span>
              <span className="connection-card__detail">
                {mattersError
                  ? "这次读取失败，不代表记录不存在。"
                  : `已同步的记录${matters.length > 0 ? `，当前读取 ${matters.length} 个事项` : ""}。`}
              </span>
            </span>
            <Pill tone={appState === "可用" ? "green" : "neutral"}>{appState}</Pill>
          </summary>
          <div className="connection-card__body">
            <p>日程与提醒会同步到你的账号，换设备登录也能查看。</p>
            <p>独立待办会作为应用内提醒显示。</p>
            <Link href="/matters" className="link-button">查看正式事项与安排</Link>
          </div>
        </details>

        <details className="connection-card">
          <summary>
            <span className="tile tile--sm"><Radio size={15} /></span>
            <span className="grow">
              <span className="connection-card__title">日历订阅（.ics）</span>
              <span className="connection-card__detail">
                {currentSubscription
                  ? `${currentSubscription.enabled ? "已开启" : "已关闭"} · ${matterLabel(matters, currentSubscription.matter_ids)}`
                  : "把选定事项的日程放进你的日历，只读。"}
              </span>
            </span>
            <Pill tone={currentSubscription?.enabled ? "green" : "neutral"}>
              {currentSubscription?.enabled ? "已开启" : currentSubscription ? "已关闭" : "未创建"}
            </Pill>
          </summary>
          <div className="connection-card__body">
            <p>订阅只包含选定事项中的确认日程，手机端只读。更新可能延迟，私人订阅地址需要保密。</p>
            <p>私人链接只在创建或重新生成时返回一次，不写入浏览器普通缓存；状态查询无法找回旧链接。</p>
            {currentSubscription ? (
              <p>
                {currentSubscription.last_read_at
                  ? `最近一次读取：${formatDateTime(currentSubscription.last_read_at)}。手机端同步可能需要一点时间。`
                  : "还没有读取记录。"}
              </p>
            ) : null}
            <div className="connection-matter-list">
              {matters.length === 0 ? (
                <p>先创建正式事项后才能选择订阅范围。</p>
              ) : (
                matters.map((matter) => {
                  const selected = selectedMatterIds.includes(matter.matter_id);
                  return (
                    <label className="check" key={matter.matter_id}>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() =>
                          setSelectedMatterIds((current) =>
                            selected
                              ? current.filter((id) => id !== matter.matter_id)
                              : [...current, matter.matter_id],
                          )
                        }
                      />
                      <span>{matter.title}</span>
                    </label>
                  );
                })
              )}
            </div>
            {calendarError ? <p className="danger-text">{calendarError}</p> : null}
            {privateSubscription?.feed_url ? (
              <div className="private-link-box">
                <strong>私人订阅链接，仅本次显示</strong>
                <p className="micro">{privateSubscription.feed_url}</p>
                {/localhost|127\.0\.0\.1/i.test(privateSubscription.feed_url) ? (
                  <p className="micro danger-text">
                    当前是本地调试地址，手机暂时无法访问。
                  </p>
                ) : null}
                <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
                  <a
                    className="btn btn--secondary btn--small"
                    href={privateSubscription.feed_url}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => setOpenedSubscription(true)}
                  >
                    打开订阅添加步骤
                  </a>
                  <button
                    type="button"
                    className="btn btn--quiet btn--small"
                    onClick={() => void copySecret("feed", privateSubscription.feed_url)}
                  >
                    <Clipboard size={13} /> {copiedSecret === "feed" ? "已复制" : "复制地址"}
                  </button>
                  <button
                    type="button"
                    className="btn btn--quiet btn--small"
                    onClick={() => {
                      setPrivateSubscription(null);
                      setOpenedSubscription(false);
                    }}
                  >
                    关闭并清除链接
                  </button>
                </div>
                {openedSubscription ? (
                  <p className="micro">添加后，手机日历会自动读取。</p>
                ) : null}
              </div>
            ) : currentSubscription ? (
              <p className="micro">旧链接不会再次显示；如未保存，请重新生成。</p>
            ) : null}
            <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
              {!currentSubscription ? (
                <button
                  type="button"
                  className="btn btn--primary btn--small"
                  onClick={() => void createOrRotate(false)}
                  disabled={calendarBusy !== null || selectedMatterIds.length === 0}
                >
                  {calendarBusy === "create" ? "正在生成…" : "生成私人订阅"}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn btn--secondary btn--small"
                    onClick={() => void updateSubscriptionRange()}
                    disabled={calendarBusy !== null || selectedMatterIds.length === 0}
                  >
                    {calendarBusy === "range" ? "正在更新…" : "更新订阅范围"}
                  </button>
                  <button
                    type="button"
                    className="btn btn--secondary btn--small"
                    onClick={() => setConfirmCalendarAction("rotate")}
                    disabled={calendarBusy !== null}
                  >
                    <RotateCw size={13} /> 重新生成
                  </button>
                  {currentSubscription.enabled ? (
                    <button
                      type="button"
                      className="btn btn--danger btn--small"
                      onClick={() => setConfirmCalendarAction("close")}
                      disabled={calendarBusy !== null}
                    >
                      关闭订阅
                    </button>
                  ) : null}
                </>
              )}
            </div>
            <p className="micro">订阅中的日程可以只读查看；提醒由日历应用负责。</p>
          </div>
        </details>

        <details className="connection-card">
          <summary>
            <span className="tile tile--sm"><Smartphone size={15} /></span>
            <span className="grow">
              <span className="connection-card__title">快捷指令连接</span>
              <span className="connection-card__detail">
                {recentSync
                  ? `最近成功上传 ${formatDateTime(recentSync)}`
                  : "用快捷指令手动上传日历与提醒清单。"}
              </span>
            </span>
            <Pill tone={uploadState === "已建立来源" ? "green" : uploadState === "读取失败" ? "red" : "neutral"}>
              {uploadState}
            </Pill>
          </summary>
          <div className="connection-card__body">
            <ol className="connection-steps">
              <li>选择允许读取的日历与提醒清单。</li>
              <li>生成一次性设备连接码，按提示填入快捷指令。</li>
              <li>手动上传一次，看到“最近成功上传”即完成。</li>
              <li>确认结果后，可按需设置定时运行。</li>
            </ol>
            {shortcutSources.length > 0 ? (
              <label className="field">
                <span className="field__label">使用已有上传来源</span>
                <select
                  className="input"
                  value={shortcutSourceId}
                  onChange={(event) => setShortcutSourceId(event.target.value)}
                >
                  {shortcutSources.map((source) => (
                    <option key={source.source_id} value={source.source_id}>
                      {source.display_name} · {source.source_type === "calendar" ? "日历" : "提醒事项"}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <div className="m-stack-8">
                <label className="field">
                  <span className="field__label">新连接类型</span>
                  <select
                    className="input"
                    value={newSourceType}
                    onChange={(event) =>
                      setNewSourceType(event.target.value as "calendar" | "reminders")
                    }
                  >
                    <option value="calendar">日历</option>
                    <option value="reminders">提醒事项</option>
                  </select>
                </label>
                <label className="field">
                  <span className="field__label">连接名称</span>
                  <input
                    className="input"
                    value={newSourceName}
                    onChange={(event) => setNewSourceName(event.target.value)}
                  />
                </label>
              </div>
            )}
            <button
              type="button"
              className="btn btn--primary btn--small"
              onClick={() => void createShortcut()}
              disabled={shortcutBusy !== null}
            >
              {shortcutBusy === "create"
                ? "正在生成连接码…"
                : shortcutSources.length > 0
                  ? "生成一次性连接码"
                  : "建立连接并生成连接码"}
            </button>
            {shortcutError ? <p className="danger-text">{shortcutError}</p> : null}
            {shortcutCredential ? (
              <div className="private-link-box">
                <strong>设备连接码，仅本次显示</strong>
                <p className="micro">{shortcutCredential.credential}</p>
                <button
                  type="button"
                  className="btn btn--quiet btn--small"
                  onClick={() => void copySecret("credential", shortcutCredential.credential)}
                >
                  <Clipboard size={13} /> {copiedSecret === "credential" ? "已复制" : "复制连接码"}
                </button>
                <button
                  type="button"
                  className="btn btn--quiet btn--small"
                  onClick={() => setShortcutCredential(null)}
                >
                  关闭并清除连接码
                </button>
                <p className="micro">连接码只显示一次，关闭后无法再次查看。</p>
              </div>
            ) : null}
            {(shortcutTokens.data ?? []).length > 0 ? (
              <div className="connection-source-list">
                {(shortcutTokens.data ?? []).map((token) => (
                  <p key={token.token_id}>
                    <strong>{token.display_name}</strong>
                    {" · "}
                    {token.enabled ? "有效" : "已撤销"}
                    {token.last_used_at ? ` · 最近使用 ${formatDateTime(token.last_used_at)}` : " · 尚未使用"}
                    {token.enabled ? (
                      <button
                        type="button"
                        className="link-button danger-text"
                        style={{ marginLeft: 6 }}
                        onClick={() => void revokeShortcut(token.token_id)}
                        disabled={shortcutBusy !== null}
                      >
                        <Trash2 size={12} /> 撤销
                      </button>
                    ) : null}
                  </p>
                ))}
              </div>
            ) : null}
            {shortcutSources.map((source) => (
              <p key={source.source_id} className="micro">
                {source.display_name} · {sourceRange(source)}
                {source.last_sync_at
                  ? ` · 最近成功上传 ${formatDateTime(source.last_sync_at)}`
                  : " · 还没有成功上传"}
                {source.last_error_code ? ` · ${uploadFailureText(source.last_error_code)}` : ""}
              </p>
            ))}
            <div className="flex gap-8" style={{ flexWrap: "wrap" }}>
              <Link href="/me/sources" className="btn btn--secondary btn--small">
                管理来源与上传记录
              </Link>
              <button type="button" className="btn btn--quiet btn--small" onClick={onRetry}>
                重新读取连接状态
              </button>
            </div>
            <p className="micro">按上面的步骤配置快捷指令即可。</p>
            <p className="micro">我们不会要求 Apple 账号密码；请勿截图或分享设备连接码。</p>
          </div>
        </details>
      </div>
      <p className="micro" style={{ margin: "8px 0 0", lineHeight: 1.6 }}>
        <Database size={12} style={{ display: "inline", verticalAlign: "-2px" }} />{" "}
        日历订阅和资料上传可以分别开启，互不影响。
      </p>

      <ConfirmDialog
        open={confirmCalendarAction !== null}
        title={confirmCalendarAction === "rotate" ? "重新生成订阅链接？" : "关闭日历订阅？"}
        body={
          confirmCalendarAction === "rotate"
            ? "旧订阅链接会立即失效，手机可能需要重新订阅。"
            : "关闭后手机不再读取订阅；已下载到手机的内容可能需要手动移除。"
        }
        confirmLabel={confirmCalendarAction === "rotate" ? "重新生成" : "关闭订阅"}
        danger={confirmCalendarAction === "close"}
        busy={calendarBusy !== null}
        onCancel={() => setConfirmCalendarAction(null)}
        onConfirm={() => {
          if (confirmCalendarAction === "rotate") void createOrRotate(true);
          else void closeSubscription();
        }}
      />
    </section>
  );
}
