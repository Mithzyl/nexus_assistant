"use client";
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {apiRequest,pa,type MatterDraft} from '@/lib/mobile/api';
export interface SelectedMemory {grant_id:string;expected_version:number;confirmed:true}
interface SharedEntry {grant_id:string;grant_version:number;source_entry:string;text?:string;typed_data?:Record<string,unknown>;sensitive:boolean}
/** Grant only selected cross-entry fields to the next Run; own memory needs no checkbox. */
export function MemoryPicker({selected,onChange}:{selected:SelectedMemory[];onChange:(items:SelectedMemory[])=>void}){
 const [open,setOpen]=useState(false),[entries,setEntries]=useState<SharedEntry[]>([]),[next,setNext]=useState<number|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false);
 /** Fetch only authorized projections and retain selected grant versions across pages. */
 async function load(offset:number){setLoading(true);setError('');try{const r=await apiRequest<{items:SharedEntry[];next_offset:number|null}>(pa(`/memory/shared?offset=${offset}&limit=20`));setEntries(v=>offset?[...v,...r.items]:r.items);setNext(r.next_offset)}catch(e){setError(e instanceof Error?e.message:'共享记忆读取失败')}finally{setLoading(false)}}
 return <section className="card m-stack-10" aria-label="共享记忆">
 <p>助手会按需使用本应用已开启的个人记忆。<Link href="/me/memories">管理记忆与用户指令</Link></p>
 <button className="btn btn--secondary" onClick={()=>{setOpen(!open);if(!open)void load(0)}}>选择其他入口共享的记忆{selected.length?`（${selected.length}条）`:''}</button>
 {open?<><p>勾选后仅允许下一次提问使用这些已共享字段，不会共享其他内容。</p>{error?<p role="alert">{error}</p>:null}{loading?<p role="status">正在读取共享记忆…</p>:!entries.length&&!error?<p>当前没有共享给本应用的记忆。</p>:null}{entries.map(e=><label key={e.grant_id} style={{overflowWrap:'anywhere'}}><input type="checkbox" checked={selected.some(x=>x.grant_id===e.grant_id)} onChange={event=>onChange(event.target.checked?[...selected,{grant_id:e.grant_id,expected_version:e.grant_version,confirmed:true}]:selected.filter(x=>x.grant_id!==e.grant_id))} disabled={!selected.some(x=>x.grant_id===e.grant_id)&&selected.length>=10} />{e.source_entry==='native'?'Nexus':e.source_entry} · {e.text??Object.values(e.typed_data??{}).map(String).join('，')}{e.sensitive?'（含敏感信息）':''}</label>)}{next!==null?<button disabled={loading} onClick={()=>void load(next)}>加载更多</button>:null}</>:null}
 </section>
}
interface Candidate {status:string;text?:string;message?:string;entry_id?:string}
export function MemoryCandidates({refresh=0}:{refresh?:number}){
 const [drafts,setDrafts]=useState<MatterDraft[]>([]),[values,setValues]=useState<Record<string,Candidate>>({}),[error,setError]=useState(''),[saving,setSaving]=useState('');
 useEffect(()=>{let active=true;apiRequest<MatterDraft[]>(pa('/matters/drafts?status=confirmed&limit=20')).then(r=>{if(active)setDrafts(r.filter(d=>d.payload.commute))}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[refresh]);
 useEffect(()=>{if(!drafts.length)return;let active=true;let timer:ReturnType<typeof setTimeout>;
 const load=async()=>{try{const pairs=await Promise.all(drafts.map(async d=>[d.draft_id,await apiRequest<Candidate>(pa(`/matters/drafts/${d.draft_id}/memory-candidate`))] as const));if(!active)return;setValues(Object.fromEntries(pairs));if(pairs.some(([,v])=>['pending','running'].includes(v.status)))timer=setTimeout(load,2500);}catch(e){if(active)setError(e instanceof Error?e.message:'提取状态读取失败')}};
 void load();return()=>{active=false;clearTimeout(timer)}},[drafts]);
 async function save(id:string){setSaving(id);setError('');try{const v=await apiRequest<Candidate>(pa(`/matters/drafts/${id}/memory-candidate/confirm`),{method:'POST',body:{confirmed_sensitive:true}});setValues(x=>({...x,[id]:v}));}catch(e){setError(e instanceof Error?e.message:'保存失败')}finally{setSaving('')}}
 if(!drafts.length&&!error)return null;
 return <section className="card m-stack-10" aria-label="通勤记忆确认"><h3>保存为个人记忆</h3><p>业务确认后由真实模型提取；只有您确认后才写入长期记忆。</p>{error?<p role="alert">{error}</p>:null}{drafts.map(d=>{const v=values[d.draft_id];return <div key={d.draft_id}><p>{d.payload.title}</p>{!v||['pending','running'].includes(v.status)?<p>正在提取记忆…</p>:v.status==='candidate'?<><p>{v.text}</p><button className="btn btn--primary" disabled={!!saving} onClick={()=>void save(d.draft_id)}>确认将这些通勤信息保存为个人记忆</button></>:v.status==='saved'?<p>记忆已保存。<Link href="/me/memories">查看个人记忆</Link></p>:<p role="alert">提取未完成：{v.message??v.status}。业务记录已保留。</p>}</div>})}</section>
}
