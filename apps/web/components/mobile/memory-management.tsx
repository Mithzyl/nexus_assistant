"use client";
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
interface Instructions {
    text: string;
    enabled: boolean;
    version: number;
}
const labels: Record<string, string> = { label: "名称", address: "地址", coordinate_system: "坐标系", latitude: "纬度（可选）", longitude: "经度（可选）", origin: "出发地", destination: "目的地", travel_mode: "出行方式", weekdays: "星期（1 为周一，用逗号分隔）", timezone: "时区", preferred_departure: "出发时间（可选）", activity: "活动", preferred_time: "偏好时间", occurred_on: "发生日期", description: "经历", lesson: "经验（可选）" };
const fields: Record<string, string[]> = { place: ["label", "address", "coordinate_system", "latitude", "longitude"], commute_preference: ["origin", "destination", "travel_mode", "weekdays", "timezone", "preferred_departure"], time_preference: ["activity", "preferred_time", "timezone"], experience: ["occurred_on", "description", "lesson"] };
const options: Record<string, string[]> = { coordinate_system: ["WGS84", "GCJ02", "BD09"], travel_mode: ["walk", "bicycle", "transit", "drive"] };
const optionLabels: Record<string, string> = { walk: "步行", bicycle: "骑行", transit: "公共交通", drive: "驾车" };
/** Manage Nexus-owned memory with retained retry keys and exact applied-version undo. */
export function MemoryManagement() {
    const [page, setPage] = useState<Page | null>(null), [settings, setSettings] = useState<Settings | null>(null), [offset, setOffset] = useState(0);
    const [error, setError] = useState(""), [notice, setNotice] = useState(""), [loading, setLoading] = useState(true), [busy, setBusy] = useState(false);
    const [editing, setEditing] = useState<Entry | null>(null), [text, setText] = useState(""), [kind, setKind] = useState("");
    const [values, setValues] = useState<Record<string, string>>({}), [sensitive, setSensitive] = useState(false), [confirmedType, setConfirmedType] = useState(false);
    const [undo, setUndo] = useState<Result | null>(null), [shareEntry, setShareEntry] = useState<Entry | null>(null);
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
            setNotice(result.status === 'candidate' ? '内容待确认，尚未成为有效记忆。' : body.action === 'forget' ? '已忘记；聊天和业务记录仍保留。' : body.action === 'undo' ? '已撤销，已生成新版本。' : body.action === 'settings' ? '记忆设置已更新。' : '已保存。');
            if (body.action === 'save' || body.action === 'correct') {
                setText('');
                setEditing(null);
                setKind('');
                setValues({});
                setSensitive(false);
                setConfirmedType(false);
            }
            setShareEntry(null);
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
                setShareEntry(null);
                await load(0).catch(() => setError('操作冲突且刷新失败，请重新加载后确认。'));
            }
        }
        finally {
            locked.current = false;
            setBusy(false);
        }
    }
    /** Build only the current Nexus schema fields, leaving unknown coordinates absent. */
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
  <section className="card m-stack-10" aria-label="个人记忆管理">
   <p>开启时，助手可按当前任务需要读取和保存本应用记忆。关闭后保留记录，停止自动读取和保存。</p>
   {settings ? <button className="btn btn--secondary" disabled={busy || loading} onClick={() => { if (window.confirm(settings.enabled ? '关闭自动读取和保存？已有记忆会保留。' : '开启本应用记忆？'))
        void mutate({ action: 'settings', enabled: !settings.enabled, expected_version: settings.version }); }}>{settings.enabled ? '关闭记忆' : '开启记忆'}</button> : null}
   {error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}
   <button className="link-button" disabled={busy || loading} onClick={() => { setError(''); void load(offset).catch(e => setError(e.message)); }}>刷新记忆</button>
   {undo ? <button className="btn btn--secondary" disabled={busy} onClick={() => void mutate({ action: 'undo', entry_id: undo.entry_id, expected_version: undo.applied_version })}>撤销刚才的改动</button> : null}
   {loading ? <p role="status">正在读取记忆…</p> : page ? <>
    {!page.items.length ? <p>当前页没有记忆。</p> : page.items.map(entry => <article key={entry.entry_id} className="m-stack-10" style={{ overflowWrap: 'anywhere' }}>
     <p>{entry.text}</p><p className="caption">{entry.status === 'active' ? '有效记忆' : entry.status === 'candidate' ? '待确认' : '已忘记'} · 版本 {entry.version}</p>
     <div className="flex gap-10"><button className="link-button" disabled={busy} onClick={() => edit(entry)}>纠正</button><button className="link-button" disabled={busy} onClick={() => { if (window.confirm('忘记这条记忆？聊天、日历和事项不会删除。'))
                void mutate({ action: 'forget', entry_id: entry.entry_id, expected_version: entry.version }); }}>忘记</button><button className="link-button" disabled={busy} onClick={() => setShareEntry(entry)}>共享</button></div>
    </article>)}
    <div className="flex gap-10"><button disabled={busy || offset === 0} onClick={() => void load(Math.max(0, offset - 20)).catch(e => setError(e.message))}>上一页</button><button disabled={busy || page.next_offset === null} onClick={() => void load(page.next_offset!).catch(e => setError(e.message))}>下一页</button></div>
   </> : null}
  </section>
  <form id="new-memory" className="card m-stack-10" onSubmit={event => { event.preventDefault(); save(); }}>
   <h2>{editing ? '纠正记忆' : '新增记忆'}</h2>
   <label>内容<textarea className="textarea" required maxLength={4000} value={text} onChange={e => { changeForm(); setText(e.target.value); }}/></label>
   <label>类型<select value={kind} disabled={!!editing?.typed_data} onChange={e => { changeForm(); setKind(e.target.value); setValues({}); }}><option value="">普通文字</option><option value="place">地点</option><option value="commute_preference">通勤偏好</option><option value="time_preference">时间偏好</option><option value="experience">过往经历</option></select></label>
   {(fields[kind] ?? []).map(field => <label key={field}>{labels[field]}{options[field] ? <select required value={values[field] ?? ''} onChange={e => { changeForm(); setValues(v => ({ ...v, [field]: e.target.value })); }}><option value="">请选择</option>{options[field].map(v => <option key={v} value={v}>{optionLabels[v] ?? v}</option>)}</select> : <input className="input" type={field === 'occurred_on' ? 'date' : field.includes('time') && field !== 'timezone' || field === 'preferred_departure' ? 'time' : 'text'} required={!['latitude', 'longitude', 'preferred_departure', 'lesson'].includes(field)} value={values[field] ?? ''} onChange={e => { changeForm(); setValues(v => ({ ...v, [field]: e.target.value })); }}/>}</label>)}
   {kind ? <label><input type="checkbox" checked={confirmedType} onChange={e => { operation.current = null; setConfirmedType(e.target.checked); }}/>我已核对以上类型和字段</label> : null}
   <label><input type="checkbox" checked={sensitive} onChange={e => { operation.current = null; setSensitive(e.target.checked); }}/>我确认保存此内容中的敏感信息（如地址）</label>
   <button className="btn btn--primary" disabled={busy || !text.trim() || !!kind && !confirmedType}>{busy ? '正在处理…' : editing ? '保存纠正' : '保存记忆'}</button>
   {editing ? <button type="button" onClick={() => { changeForm(); setEditing(null); setText(''); setKind(''); setValues({}); }}>取消纠正</button> : null}
  </form>
  {shareEntry ? <MemorySharing key={`${shareEntry.entry_id}:${shareEntry.version}`} entry={shareEntry}/> : null}
  <InstructionEditor />
  <p className="caption">允许 Nexus 自动使用关联 Assistant 记忆的总开关由 Nexus 管理。逐条共享与该总开关分别生效。</p>
 </div>;
}
/** Edit only Assistant entry instructions; existing Runs retain their pinned version. */
function InstructionEditor() {
    const [profile, setProfile] = useState<Instructions | null>(null), [text, setText] = useState(''), [enabled, setEnabled] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState(''), [busy, setBusy] = useState(false);
    const lock = useRef(false);
    /** Restore the authoritative version after load or conflict. */
    async function load() { const p = await apiRequest<Instructions>(pa('/memory/instructions')); setProfile(p); setText(p.text); setEnabled(p.enabled); }
    useEffect(() => { void load().catch(e => setError(e.message)); }, []);
    /** Save valid text even when disabling; never inject it into user requests locally. */
    async function save() { if (!profile || lock.current)
        return; lock.current = true; setBusy(true); setError(''); setNotice(''); try {
        await apiRequest(pa('/memory/instructions'), { method: 'POST', body: { text, enabled, expected_version: profile.version } });
        await load();
        setNotice('已保存，仅影响之后创建的对话。');
    }
    catch (e) {
        setError(e instanceof Error ? e.message : '保存失败');
        if (e instanceof ApiError && e.status === 409 && ['memory_version_conflict', 'memory_request_conflict', 'nexus_conflict'].includes(e.code))
            await load().catch(() => setError('版本冲突，刷新失败，请重试。'));
    }
    finally {
        lock.current = false;
        setBusy(false);
    } }
    return <section className="card m-stack-10"><h2>Assistant 用户指令</h2><p>设置回答风格和个人要求，仅影响之后创建的对话。</p>{error ? <p role="alert">{error}</p> : null}{notice ? <p role="status">{notice}</p> : null}{profile ? <><textarea className="textarea" aria-label="用户指令" maxLength={4000} value={text} onChange={e => setText(e.target.value)}/><label><input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)}/>启用用户指令</label><button className="btn btn--primary" disabled={busy || !text.trim()} onClick={() => void save()}>保存用户指令</button></> : <button onClick={() => void load().catch(e => setError(e.message))}>重新读取指令</button>}</section>;
}
interface Grant {
    grant_id: string;
    version: number;
    target_entry: string;
    fields: string[];
    automatic: boolean;
    active?: boolean;
}
/** Display and revoke real per-field grants, independently of broad native-use consent. */
function MemorySharing({ entry }: {
    entry: Entry;
}) {
    const [grants, setGrants] = useState<Grant[]>([]), [selected, setSelected] = useState<string[]>([]), [automatic, setAutomatic] = useState(false), [error, setError] = useState(''), [busy, setBusy] = useState(false);
    const lock = useRef(false);
    const paths = ['text', ...Object.keys(entry.typed_data ?? {}).map(k => `typed_data.${k}`)];
    /** Refresh grants after every mutation rather than assuming authorization remains active. */
    async function load() { const r = await apiRequest<{
        items: Grant[];
    }>(pa(`/memory/shares?entry_id=${encodeURIComponent(entry.entry_id)}`)); setGrants(r.items); }
    useEffect(() => { void load().catch(e => setError(e.message)); }, [entry.entry_id]);
    /** Use the entry version for creation and the grant version for revocation. */
    async function change(path: string, body: unknown) { if (lock.current)
        return; lock.current = true; setBusy(true); setError(''); try {
        await apiRequest(pa(path), { method: 'POST', body });
        await load();
    }
    catch (e) {
        setError(e instanceof Error ? e.message : '共享操作失败');
        await load().catch(() => { });
    }
    finally {
        lock.current = false;
        setBusy(false);
    } }
    return <section className="card m-stack-10"><h2>共享给关联的 Nexus</h2><p>仅共享选中的字段。敏感共享信息用于模型时，仍可能需要针对对话确认。</p>{error ? <p role="alert">{error}</p> : null}{paths.map(path => <label key={path}><input type="checkbox" checked={selected.includes(path)} onChange={e => setSelected(v => e.target.checked ? [...v, path] : v.filter(x => x !== path))}/>{path === 'text' ? '记忆正文' : labels[path.slice(11)] ?? '记忆类型'}</label>)}<label><input type="checkbox" checked={automatic} onChange={e => setAutomatic(e.target.checked)}/>允许按需使用这条共享许可</label><button disabled={busy || !selected.length} onClick={() => { if (window.confirm('将选中字段共享给关联的 Nexus 账号？'))
        void change('/memory/shares', { entry_id: entry.entry_id, expected_version: entry.version, target_entry: 'native', fields: selected, automatic }); }}>保存共享许可</button>{grants.map(g => <div key={g.grant_id}><p>{g.active ? '有效许可' : '已失效许可'} · {g.target_entry === 'native' ? 'Nexus' : g.target_entry}：{g.fields.map(p => p === 'text' ? '记忆正文' : labels[p.slice(11)] ?? '记忆类型').join('、')}</p><button disabled={busy} onClick={() => void change('/memory/shares/revoke', { grant_id: g.grant_id, expected_version: g.version })}>撤销共享</button></div>)}</section>;
}
