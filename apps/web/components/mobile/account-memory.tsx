"use client";
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {apiRequest,pa,type MatterDraft} from '@/lib/mobile/api';
export interface SelectedMemory {entry_id:string;expected_version:number;confirmed_sensitive:true}
interface Entry {entry_id:string;version:number;status:string;text:string;sensitive:boolean;app_data?:{fields:Record<string,unknown>}}
export function MemoryPicker({selected,onChange,viewOnly=false}:{selected:SelectedMemory[];onChange:(items:SelectedMemory[])=>void;viewOnly?:boolean}){
 const [open,setOpen]=useState(viewOnly),[entries,setEntries]=useState<Entry[]>([]),[error,setError]=useState('');
 useEffect(()=>{if(!open)return;let active=true;apiRequest<{items:Entry[]}>(pa('/memory?limit=50')).then(r=>{if(active)setEntries(r.items)}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[open,selected.length]);
 return <section className="card m-stack-10" aria-label="账号记忆">
 {!viewOnly?<button className="btn btn--secondary" onClick={()=>setOpen(!open)}>选择已有记忆并授权本次对话读取{selected.length?`（${selected.length}条）`:''}</button>:<h2>已保存的账号记忆</h2>}
 {open?<><p>{viewOnly?'这里读取 Nexus 中当前账号的真实记忆。':'勾选后，这些记忆将提供给模型用于下一次提问；不授权其他对话。含地点等敏感信息，请确认后选择。'}</p>{error?<p role="alert">{error}</p>:entries.length===0?<p>当前页没有记忆记录。</p>:entries.map(e=><div className="card" key={e.entry_id}>{!viewOnly&&e.status==='active'?<label><input type="checkbox" checked={selected.some(x=>x.entry_id===e.entry_id)} onChange={event=>onChange(event.target.checked?[...selected,{entry_id:e.entry_id,expected_version:e.version,confirmed_sensitive:true}]:selected.filter(x=>x.entry_id!==e.entry_id))} />授权本次读取此记忆</label>:null}<p>{e.status==='active'?'已生效':'待确认'} · 版本{e.version}</p><p>{e.text}</p>{viewOnly&&e.app_data?<details><summary>查看保存的数据</summary><pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(e.app_data.fields,null,2)}</pre></details>:null}</div>)}</>:null}
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
 return <section className="card m-stack-10" aria-label="通勤记忆确认"><h3>保存为账号记忆</h3><p>业务确认后由真实模型提取；只有您确认后才写入长期记忆。</p>{error?<p role="alert">{error}</p>:null}{drafts.map(d=>{const v=values[d.draft_id];return <div key={d.draft_id}><p>{d.payload.title}</p>{!v||['pending','running'].includes(v.status)?<p>正在提取记忆…</p>:v.status==='candidate'?<><p>{v.text}</p><button className="btn btn--primary" disabled={!!saving} onClick={()=>void save(d.draft_id)}>确认将这些通勤信息保存为账号记忆</button></>:v.status==='saved'?<p>记忆已保存。<Link href="/me/memories">查看账号记忆</Link></p>:<p role="alert">提取未完成：{v.message??v.status}。业务记录已保留。</p>}</div>})}</section>
}
