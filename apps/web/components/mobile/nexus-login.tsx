"use client";
import { useEffect, useState } from "react";

/** Add optional Nexus login and explicit association without replacing local authentication. */
export function NexusLogin({link=false}:{link?:boolean}) {
  const [enabled,setEnabled]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{let alive=true;void fetch("/api/auth/nexus").then(r=>r.json()).then(v=>{if(alive)setEnabled(v.enabled===true);}).catch(()=>{});return()=>{alive=false;};},[]);
  /** User confirmation starts only the selected login/link operation. */
  async function start() {
    setBusy(true);setError("");
    try {const r=await fetch("/api/auth/nexus",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({action:link?"link":"start"})});const b=await r.json();if(!r.ok)throw Error(b.error?.message||"请求失败。");window.location.assign(b.url);}
    catch(e){setError(e instanceof Error?e.message:"请求失败。");setBusy(false);}
  }
  if(!enabled)return null;
  return <div className="mt-20"><button type="button" className="auth-switch" disabled={busy} onClick={()=>void start()}>{busy?"正在连接…":link?"关联 Nexus 账号":"使用 Nexus 登录"}</button>
    {link&&<p className="caption">将前往 Nexus 确认关联；不会自动合并历史数据或共享记忆。</p>}
    {error&&<p role="alert">{error}</p>}</div>;
}
