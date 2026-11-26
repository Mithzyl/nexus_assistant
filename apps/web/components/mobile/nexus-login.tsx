"use client";
import { useEffect, useState } from "react";
import { loginDestinationFromSearch } from "@/lib/mobile/login-return.mjs";

/** Add optional Nexus login and explicit association without replacing local authentication. */
export function NexusLogin({link=false}:{link?:boolean}) {
  const [enabled,setEnabled]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  useEffect(()=>{let alive=true;void fetch("/api/auth/nexus").then(r=>r.json()).then(v=>{if(alive)setEnabled(v.enabled===true);}).catch(()=>{});return()=>{alive=false;};},[]);
  /** User confirmation starts only the selected login/link operation. */
  async function start() {
    setBusy(true);setError("");
    try {
      const request = link ? { action: "link" } : {
        action: "start",
        next: loginDestinationFromSearch(window.location.search),
      };
      const response = await fetch("/api/auth/nexus", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error?.message || "请求失败。");
      window.location.assign(result.url);
    }
    catch{setError("登录失败了");setBusy(false);}
  }
  if(!enabled)return null;
  return <div className="mt-20"><button type="button" className="auth-switch" disabled={busy} onClick={()=>void start()}>{busy?"正在连接…":link?"关联外部账号":"使用外部账号登录"}</button>
    {link&&<p className="caption">关联后仍可分别管理两边的历史和记忆。</p>}
    {error&&<div role="alert">{error} <button type="button" className="link-button" onClick={()=>setError("")}>{link?"回到账号设置":"回到登录页"}</button></div>}</div>;
}
