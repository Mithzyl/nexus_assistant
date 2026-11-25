"use client";
import { MemoryHistory } from "./memory-history";
import { MemoryAttribution, type MemorySource } from "./memory-attribution";
import { useEffect, useRef, useState } from "react";
import { apiRequest, pa, ApiError } from "@/lib/mobile/api";
type Typed = Record<string, string | number | number[]> & {
    memory_type: string;
};
interface Entry {
    entry_id: string;
    version: number;
    status: string;
    text: string;
    sensitive: boolean;
    typed_data?: Typed;
    app_data?: unknown;
    source?: MemorySource;
    updated_at?: string;
}
interface Page {
    items: Entry[];
    next_offset: number | null;
}
interface Settings {
    enabled: boolean;
    version: number;
}
interface Result {
    entry_id?: string;
    version: number;
    applied_version?: number;
    status?: string;
}
const labels: Record<string, string> = { label: "名称", address: "地址", coordinate_system: "坐标系", latitude: "纬度（可选）", longitude: "经度（可选）", origin: "出发地", destination: "目的地", travel_mode: "出行方式", weekdays: "星期（1 为周一，用逗号分隔）", timezone: "时区", preferred_departure: "出发时间（可选）", activity: "活动", preferred_time: "偏好时间", occurred_on: "发生日期", description: "经历", lesson: "经验（可选）" };
const fields: Record<string, string[]> = { place: ["label", "address", "coordinate_system", "latitude", "longitude"], commute_preference: ["origin", "destination", "travel_mode", "weekdays", "timezone", "preferred_departure"], time_preference: ["activity", "preferred_time", "timezone"], experience: ["occurred_on", "description", "lesson"] };
const options: Record<string, string[]> = { coordinate_system: ["WGS84", "GCJ02", "BD09"], travel_mode: ["walk", "bicycle", "transit", "drive"] };
const optionLabels: Record<string, string> = { walk: "步行", bicycle: "骑行", transit: "公共交通", drive: "驾车" };
/** Manage Assistant-owned memories, preserving original authorship and exact-version undo. */
export function MemoryManagement() {
    const [historyId, setHistoryId] = useState<string | null>(null);
    const [page, setPage] = useState<Page | null>(null), [settings, setSettings] = useState<Settings | null>(null), [offset, setOffset] = useState(0);
    const [error, setError] = useState(""), [notice, setNotice] = useState(""), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
    const [editing, setEditing] = useState<Entry | null>(null), [text, setText] = useState(""), [kind, setKind] = useState("");
    const [values, setValues] = useState<Record<string, string>>({}), [sensitive, setSensitive] = useState(false), [confirmedType, setConfirmedType] = useState(false);
    const [undo, setUndo] = useState<Result | null>(null);
    const operation = useRef<{
        signature: string;
        key: string;
    } | null>(null), locked = useRef(false), generation = useRef(0);
    /** Fetch a fresh page and settings together; retain an explicit error on failure. */
    async function load(at: number) {
        const current = ++generation.current;
        setLoading(true);
        try {
            const [p, s] = await Promise.all([apiRequest<Page>(pa(`/memory?offset=${at}&limit=20`)), apiRequest<Settings>(pa('/memory/settings'))]);
            if (current === generation.current) {
                setPage(p);
                setSettings(s);
                setOffset(at);
            }
        }
        finally {
            if (current === generation.current)
                setLoading(false);
        }
    }
    useEffect(() => { void load(0).catch(e => setError(e.message)); return () => { generation.current++; }; }, []);
    /** Reset confirmation whenever the edited content or type changes. */
    function changeForm() { operation.current = null; setConfirmedType(false); setSensitive(false); }
    /** Preserve typed and legacy data when correcting an existing entry. */
    function edit(entry: Entry) { changeForm(); setEditing(entry); setText(entry.text); setKind(entry.typed_data?.memory_type ?? ''); setValues(Object.fromEntries(Object.entries(entry.typed_data ?? {}).filter(([k]) => k !== 'memory_type').map(([k, v]) => [k, String(v)]))); }
    /** Retry identical commands with the same key; conflicts require fresh user review. */
    async function mutate(body: Record<string, unknown>) {
        if (locked.current)
            return;
        locked.current = true;
        setBusy(true);
        setError('');
        setNotice('');
        const signature = JSON.stringify(body);
        if (operation.current?.signature !== signature)
            operation.current = { signature, key: crypto.randomUUID() };
        try {
            const result = await apiRequest<Result>(pa('/memory/commands'), { method: 'POST', body: { ...body, key: operation.current.key } });
            operation.current = null;
            setUndo(result.entry_id && result.applied_version === result.version && body.action !== 'undo' ? result : null);
            setNotice(result.status === 'candidate' ? '已提交待确认' : body.action === 'forget' ? '已删除这条记忆' : body.action === 'undo' ? '已撤销' : body.action === 'settings' ? '记忆设置已更新。' : '已保存。');
            if (body.action === 'save' || body.action === 'correct') {
                setText('');
                setEditing(null);
                setKind('');
                setValues({});
                setSensitive(false);
                setConfirmedType(false);
            }
            await load(0);
        }
        catch (e) {
            setError(e instanceof Error ? e.message : '操作失败，保存结果未知。');
            if (e instanceof ApiError && e.status === 409 && ['memory_version_conflict', 'memory_request_conflict', 'nexus_conflict'].includes(e.code)) {
                operation.current = null;
                setUndo(null);
                setEditing(null);
                setText('');
                setKind('');
                setValues({});
                    await load(0).catch(() => setError('操作冲突且刷新失败，请重新加载后确认。'));
            }
        }
        finally {
            locked.current = false;
            setBusy(false);
        }
    }
    /** Submit compatible business fields without inventing coordinates or changing retained source history. */
    function save() {
        let typed: Typed | undefined;
        if (kind) {
            typed = { memory_type: kind };
            for (const field of fields[kind]) {
                const value = values[field];
                if (!value)
                    continue;
                typed[field] = field === 'weekdays' ? value.split(',').map(v => Number(v.trim())) : ['latitude', 'longitude'].includes(field) ? Number(value) : value;
            }
        }
        void mutate({ action: editing ? 'correct' : 'save', text, confirmed_sensitive: sensitive, ...(typed ? { typed_data: typed, confirmed_type: confirmedType } : {}), ...(editing ? { entry_id: editing.entry_id, expected_version: editing.version, ...(editing.app_data ? { app_data: editing.app_data } : {}) } : {}) });
    }
    return <div className="m-stack-14">
  {historyId ? <MemoryHistory key={historyId} entryId={historyId} onClose={() => setHistoryId(null)} /> : null}
  <section className="card m-stack-10" aria-label="个人记忆管理">
   <p>开启后，助手会参考这些习惯来安排日程。</p>
   {settings ? <button className="btn btn--secondary" disabled={busy || loading} onClick={() => { if (window.confirm(settings.enabled ? '关闭自动读取和保存？已有记忆会保留。' : '开启本应用记忆？'))
        void mutate({ action: 'settings', enabled: !settings.enabled, expected_version: settings.version }); }}>{settings.enabled ? '关闭记忆' : '开启记忆'}</button> : null}
   {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
   <button className="link-button" disabled={busy || loading} onClick={() => { setError(''); void load(offset).catch(e => setError(e.message)); }}>刷新记忆</button>
   {undo ? <button className="btn btn--secondary" disabled={busy} onClick={() => void mutate({ action: 'undo', entry_id: undo.entry_id, expected_version: undo.applied_version })}>撤销改动</button> : null}
   {loading ? <p role="status">正在读取记忆…</p> : page ? <>
    {!page.items.length ? <p>当前页没有记忆。</p> : page.items.map(entry => <article key={entry.entry_id} className="m-stack-10" style={{ overflowWrap: 'anywhere' }}>
     <p>{entry.text}</p><MemoryAttribution source={entry.source} text={entry.text} recordedAt={entry.updated_at} /><p className="caption">{entry.status === 'active' ? '使用中' : entry.status === 'candidate' ? '待确认' : '已忘记'} · 版本 {entry.version}</p>
     <div className="action-row action-row--compact"><button className="link-button" onClick={() => setHistoryId(entry.entry_id)}>历史</button><button className="link-button" disabled={busy} onClick={() => edit(entry)}>纠正</button><button className="link-button" disabled={busy} onClick={() => { if (window.confirm('忘记这条记忆？聊天、日历和事项不会删除。'))
                void mutate({ action: 'forget', entry_id: entry.entry_id, expected_version: entry.version }); }}>忘记</button></div>
    </article>)}
    <div className="action-row"><button className="btn btn--secondary btn--small" disabled={busy || offset === 0} onClick={() => void load(Math.max(0, offset - 20)).catch(e => setError(e.message))}>上一页</button><button className="btn btn--secondary btn--small" disabled={busy || page.next_offset === null} onClick={() => void load(page.next_offset!).catch(e => setError(e.message))}>下一页</button></div>
   </> : null}
  </section>
  <form id="new-memory" className="card m-stack-10" onSubmit={event => { event.preventDefault(); save(); }}>
   <h2>{editing ? '纠正记忆' : '新增记忆'}</h2>
   <label>内容<textarea className="textarea" required maxLength={4000} value={text} onChange={e => { changeForm(); setText(e.target.value); }}/></label>
   <label>类型<select value={kind} disabled={!!editing?.typed_data} onChange={e => { changeForm(); setKind(e.target.value); setValues({}); }}><option value="">普通文字</option><option value="place">地点</option><option value="commute_preference">通勤偏好</option><option value="time_preference">时间偏好</option><option value="experience">过往经历</option></select></label>
   {(fields[kind] ?? []).map(field => <label key={field}>{labels[field]}{options[field] ? <select required value={values[field] ?? ''} onChange={e => { changeForm(); setValues(v => ({ ...v, [field]: e.target.value })); }}><option value="">请选择</option>{options[field].map(v => <option key={v} value={v}>{optionLabels[v] ?? v}</option>)}</select> : <input className="input" type={field === 'occurred_on' ? 'date' : field.includes('time') && field !== 'timezone' || field === 'preferred_departure' ? 'time' : 'text'} required={!['latitude', 'longitude', 'preferred_departure', 'lesson'].includes(field)} value={values[field] ?? ''} onChange={e => { changeForm(); setValues(v => ({ ...v, [field]: e.target.value })); }}/>}</label>)}
   {kind ? <label><input type="checkbox" checked={confirmedType} onChange={e => { operation.current = null; setConfirmedType(e.target.checked); }}/>我已核对以上内容</label> : null}
   <label><input type="checkbox" checked={sensitive} onChange={e => { operation.current = null; setSensitive(e.target.checked); }}/>我确认保存此内容中的敏感信息（如地址）</label>
   <button className="btn btn--primary" disabled={busy || !text.trim() || !!kind && !confirmedType}>{busy ? '正在处理…' : editing ? '保存纠正' : '保存记忆'}</button>
   {editing ? <button type="button" className="link-button" onClick={() => { changeForm(); setEditing(null); setText(''); setKind(''); setValues({}); }}>取消纠正</button> : null}
  </form>
 </div>;
}
