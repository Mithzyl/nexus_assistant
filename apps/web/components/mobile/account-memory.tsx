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
 {open?<><p>勾选后只用于下一次提问，不会共享别的内容。</p>{error?<p role="alert">{error}</p>:null}{loading?<p role="status">正在读取共享记忆…</p>:!entries.length&&!error?<p>当前没有共享给本应用的记忆。</p>:null}{entries.map(e=><label key={e.grant_id} style={{overflowWrap:'anywhere'}}><input type="checkbox" checked={selected.some(x=>x.grant_id===e.grant_id)} onChange={event=>onChange(event.target.checked?[...selected,{grant_id:e.grant_id,expected_version:e.grant_version,confirmed:true}]:selected.filter(x=>x.grant_id!==e.grant_id))} disabled={!selected.some(x=>x.grant_id===e.grant_id)&&selected.length>=10} />{e.source_entry==='native'?'Nexus':e.source_entry} · {e.text??Object.values(e.typed_data??{}).map(String).join('，')}{e.sensitive?'（含敏感信息）':''}</label>)}{next!==null?<div className="action-row"><button className="link-button" disabled={loading} onClick={()=>void load(next)}>加载更多</button></div>:null}</>:null}
 </section>
}
interface Candidate {status:string;text?:string;message?:string;entry_id?:string}
/** Offer saved commute facts for confirmation without changing how Assistant stores them. */
export function MemoryCandidates({refresh=0}:{refresh?:number}){
 const [hiddenIds, setHiddenIds] = useState<string[]>([]);
 const [drafts,setDrafts]=useState<MatterDraft[]>([]),[values,setValues]=useState<Record<string,Candidate>>({}),[error,setError]=useState(''),[saving,setSaving]=useState('');
 useEffect(()=>{let active=true;apiRequest<MatterDraft[]>(pa('/matters/drafts?status=confirmed&limit=20')).then(r=>{if(active)setDrafts(r.filter(d=>d.payload.commute))}).catch(e=>{if(active)setError(e.message)});return()=>{active=false}},[refresh]);
 useEffect(()=>{if(!drafts.length)return;let active=true;let timer:ReturnType<typeof setTimeout>;
 const load=async()=>{try{const pairs=await Promise.all(drafts.map(async d=>[d.draft_id,await apiRequest<Candidate>(pa(`/matters/drafts/${d.draft_id}/memory-candidate`))] as const));if(!active)return;setValues(Object.fromEntries(pairs));if(pairs.some(([,v])=>['pending','running'].includes(v.status)))timer=setTimeout(load,2500);}catch(e){if(active)setError(e instanceof Error?e.message:'提取状态读取失败')}};
 void load();return()=>{active=false;clearTimeout(timer)}},[drafts]);
 async function save(id:string){setSaving(id);setError('');try{const v=await apiRequest<Candidate>(pa(`/matters/drafts/${id}/memory-candidate/confirm`),{method:'POST',body:{confirmed_sensitive:true}});setValues(x=>({...x,[id]:v}));}catch(e){setError(e instanceof Error?e.message:'保存失败')}finally{setSaving('')}}
 const visible = drafts.filter((draft) => !hiddenIds.includes(draft.draft_id) &&
   [undefined, "pending", "running", "candidate", "saved"].includes(values[draft.draft_id]?.status));
 if (!visible.length && !error) return null;
 return <section className="card m-stack-10" aria-label="通勤习惯">
   {error ? <p role="alert">通勤习惯没加载出来</p> : null}
   {visible.map((draft) => {
     const value = values[draft.draft_id];
     return <div key={draft.draft_id}>
       <p className="card-title">{draft.payload.title}</p>
       {!value || value.status === "pending" || value.status === "running" ? <p>正在整理通勤习惯…</p>
         : value.status === "candidate" ? <>
           <p>以后都按这个通勤习惯来记吗？</p>
           <p>{value.text}</p>
           <div className="action-row">
             <button type="button" className="btn btn--primary" disabled={Boolean(saving)} onClick={() => void save(draft.draft_id)}>记住</button>
             <button type="button" className="link-button" disabled={Boolean(saving)} onClick={() => setHiddenIds((ids) => [...ids, draft.draft_id])}>不用</button>
           </div>
         </> : <p>已经记下来了 <Link className="link-button" href="/me/memories">看看我的记忆</Link></p>}
     </div>;
   })}
 </section>;
}
