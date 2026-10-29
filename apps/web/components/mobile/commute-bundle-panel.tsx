"use client";
import { useState } from "react";
import { apiRequest, pa, type MatterDraft, type PlaceCandidate, type RouteEstimate } from "@/lib/mobile/api";

export interface CommuteBundleState {
  status: string;
  intent: { origin_query: string; destination_query: string; arrival_local_time: string; buffer_minutes: number | null; effective_from: string | null; weekdays: number[] | null };
  origin_search?: { candidates: PlaceCandidate[]; reason_code: string | null };
  destination_search?: { candidates: PlaceCandidate[]; reason_code: string | null };
  routes?: Record<string, RouteEstimate>;
  selected_mode?: string;
  route_quote_id?: string;
  suggested_departure_at?: string;
  target_arrival_at?: string;
  quote_inputs?: { effective_from: string; weekdays: number[]; buffer_minutes: number; test_only: boolean; origin: PlaceCandidate; destination: PlaceCandidate };
}

export function CommuteBundlePanel({draft,onChanged,onDirty}:{draft:MatterDraft;onChanged:(draft:MatterDraft)=>void;onDirty:()=>void}) {
 const c=draft.payload.commute!;
 const [origin,setOrigin]=useState(c.quote_inputs?.origin.selection_token ?? "");
 const [destination,setDestination]=useState(c.quote_inputs?.destination.selection_token ?? "");
 const [day,setDay]=useState(c.quote_inputs?.effective_from ?? c.intent.effective_from ?? "");
 const [days,setDays]=useState<number[]>(c.quote_inputs?.weekdays ?? c.intent.weekdays ?? []);
 const [buffer,setBuffer]=useState(c.quote_inputs?.buffer_minutes ?? c.intent.buffer_minutes ?? 10);
 const [mode,setMode]=useState(c.selected_mode ?? "driving");
 const [testOnly,setTestOnly]=useState(c.quote_inputs?.test_only ?? false);
 const [busy,setBusy]=useState(false);const [error,setError]=useState<string|null>(null);
 async function compare(){
  setBusy(true);setError(null);
  try{onChanged(await apiRequest<MatterDraft>(pa(`/matters/drafts/${draft.draft_id}/commute-quote`),{method:"POST",body:{expected_version:draft.version,origin_token:origin,destination_token:destination,effective_from:day,weekdays:days,buffer_minutes:buffer,mode,test_only:testOnly}}));}
  catch(e){setError(e instanceof Error?e.message:"测算失败");try{onChanged(await apiRequest<MatterDraft>(pa(`/matters/drafts/${draft.draft_id}`)));}catch{}}
  finally{setBusy(false);}
 }
 return <section className="card m-stack-10" aria-label="通勤组合草稿">
  <h3>确认通勤条件，比较真实路线</h3>
  <p>目标 {c.intent.arrival_local_time} 前到达。首日、重复范围和出行方式经您确认后才保存。</p>
  {([['origin','起点',origin,setOrigin],['destination','终点',destination,setDestination]] as const).map(([side,label,value,set])=><fieldset key={side}><legend>{label}候选</legend>{c[`${side}_search`]?.candidates.map((p)=><label className="field" key={p.selection_token}><span><input type="radio" name={`commute-${side}`} checked={value===p.selection_token} onChange={()=>{set(p.selection_token);onDirty();}} /> {p.name} · {p.city} · {p.address}（{p.longitude}, {p.latitude}）</span></label>)}{!c[`${side}_search`]?.candidates.length?<p role="alert">未取得候选：{c[`${side}_search`]?.reason_code ?? c.status}</p>:null}</fieldset>)}
  <label className="field">首次生效日期<input className="input" type="date" value={day} onChange={e=>{setDay(e.target.value);onDirty();}} /></label>
  <fieldset><legend>重复星期</legend>{['一','二','三','四','五','六','日'].map((label,i)=><label key={label} style={{marginRight:12}}><input type="checkbox" checked={days.includes(i+1)} onChange={e=>{setDays(e.target.checked?[...days,i+1]:days.filter(d=>d!==i+1));onDirty();}} />周{label}</label>)}</fieldset>
  <label className="field">提前到达余量（分钟）<input className="input" type="number" min={0} max={120} value={buffer} onChange={e=>{setBuffer(Number(e.target.value));onDirty();}} /></label>
  <label className="field">保存的出行方式<select className="input" value={mode} onChange={e=>{setMode(e.target.value);onDirty();}}><option value="driving">驾车</option><option value="transit">公交</option></select></label>
  <label><input type="checkbox" checked={testOnly} onChange={e=>{setTestOnly(e.target.checked);onDirty();}} />仅为联调测试数据，不是真人永久习惯</label>
  <button className="btn btn--primary" disabled={busy||!origin||!destination||!day||!days.length} onClick={()=>void compare()}>{busy?'正在比较真实路线…':c.routes?'应用选择（复用有效测算）':'确认地点并比较驾车与公交'}</button>
  {error?<p role="alert">{error}</p>:null}
  {Object.entries(c.routes??{}).map(([key,r])=><div className="card" key={key}><strong>{key==='driving'?'驾车':'公交'}</strong>{r.status==='available'?<p>约 {Math.ceil((r.duration_seconds??0)/60)} 分钟 · {r.distance_meters} 米{r.fare_cny!=null?` · 费用估计 ¥${r.fare_cny}`:''}</p>:<p>无法取得路线：{r.reason_code ?? r.status}</p>}<p>{r.accuracy_notice}</p></div>)}
  {c.status==='quoted'?<p>确认后同时保存事项、首日日程及重复计划，并绑定所选路线。建议出发：{new Date(c.suggested_departure_at!).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}。未来耗时可能变化，不保证准点。</p>:null}
 </section>;
}
