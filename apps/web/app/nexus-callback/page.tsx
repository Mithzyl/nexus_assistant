"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { loginDestination, loginHref } from "@/lib/mobile/login-return.mjs";

/** Consume a fragment callback without placing the code in HTTP logs or persistent browser storage. */
export default function NexusCallback() {
  const started = useRef(false);
  const [error,setError] = useState("");
  const [retryHref, setRetryHref] = useState(() => loginHref(null));
  useEffect(()=>{
    if(started.current)return; started.current=true;
    const params=new URLSearchParams(window.location.hash.slice(1));
    window.history.replaceState(null,"","/nexus-callback");
    void fetch("/api/auth/nexus",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action:"exchange",state:params.get("state"),code:params.get("code")})})
      .then(async response => {
        const body = await response.json();
        if (!response.ok) {
          setRetryHref(loginHref(body.next));
          throw Error(body.error?.message || "登录失败。");
        }
        window.location.replace(loginDestination(body.next));
      })
      .catch(()=>setError("登录失败了"));
  },[]);
  return <main className="m-app" style={{padding:24}}><h1>登录生活助手</h1>
    {error?<><p role="alert">{error}</p><Link href={retryHref}>回到登录页</Link></>:<p role="status">正在确认登录…</p>}
  </main>;
}
