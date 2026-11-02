"use client";
import { useEffect, useState } from "react";
import { apiRequest, pa } from "@/lib/mobile/api";
interface History { current_version: number; items: {version:number; status:string; text?:string}[]; next_offset:number|null }
export function MemoryHistory({entryId,onClose}:{entryId:string;onClose:()=>void}) {
 const [data,setData]=useState<History|null>(null),[offset,setOffset]=useState(0),[error,setError]=useState(""),[busy,setBusy]=useState(false);
 useEffect(()=>{let alive=true;setBusy(true);setError("");apiRequest<History>(pa(`/memory/history/${encodeURIComponent(entryId)}?offset=${offset}`)).then(v=>{if(alive)setData(v)}).catch(e=>{if(alive)setError(e.message)}).finally(()=>{if(alive)setBusy(false)});return()=>{alive=false}},[entryId,offset]);
 return <section className="card m-stack-10" aria-label="记忆版本历史"><h3>版本历史</h3><p>历史只供查看；撤销只能针对当前版本，不会用旧内容覆盖后来修改。</p><button onClick={onClose}>关闭历史</button>{error?<p role="alert">{error}</p>:busy?<p>正在读取历史…</p>:data?<><p>当前版本 {data.current_version}</p>{data.items.map(v=><article key={v.version}><p>版本 {v.version} · {({active:'有效',forgotten:'已忘记',candidate:'待确认'} as Record<string,string>)[v.status]??'状态待核对'}</p><p style={{overflowWrap:"anywhere"}}>{v.text??"此版本没有可展示正文"}</p></article>)}<button disabled={!offset} onClick={()=>setOffset(Math.max(0,offset-20))}>上一页</button><button disabled={data.next_offset===null} onClick={()=>setOffset(data.next_offset!)}>下一页</button></>:null}</section>
}
