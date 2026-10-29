"use client";

/** Login form used by `/login` and by inline "login required" prompts. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { ApiError, registrationOptions } from "@/lib/mobile/api";
import { useAuth } from "@/components/mobile/providers";
import { PrimaryButton } from "@/components/mobile/buttons";

export function LoginScreen() {
  const router = useRouter();
  const { state, user, login, register } = useAuth();
  const [creating, setCreating] = useState(false);
  const [displayName, setDisplayName] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [options, setOptions] = useState<{enabled: boolean; invite_required: boolean} | null>(null);
  const [optionsError, setOptionsError] = useState(false);
  useEffect(() => {
    let active = true;
    registrationOptions().then((value) => { if (active) setOptions(value); })
      .catch(() => { if (active) setOptionsError(true); });
    return () => { active = false; };
  }, []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (state === "signedIn" && user) {
      router.replace("/today");
    }
  }, [state, user, router]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    if (!email.trim() || !password) {
      setError("请填写邮箱和密码。");
      return;
    }
    if (creating && (!displayName.trim() || password.length < 8)) {
      setError("请填写称呼，密码至少需要8个字符。"); return;
    }
    if (creating && password !== confirmPassword) {
      setError("两次输入的密码不一致，请再确认一下。"); return;
    }
    if (creating && options?.invite_required && !inviteCode) {
      setError("请填写注册邀请码。"); return;
    }
    setBusy(true);
    setError(null);
    try {
      if (creating) await register({ email, password, display_name: displayName, invite_code: inviteCode });
      else await login(email, password);
      router.replace("/today");
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(
          !creating && caught.status === 401
            ? "邮箱或密码不正确。"
            : caught.message || "登录未能完成。",
        );
      } else {
        setError("无法连接服务器，请稍后再试。");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="m-app" style={{ minHeight: "100dvh" }}>
      <div style={{ padding: "calc(env(safe-area-inset-top) + 10px) 18px 0" }}>
        <Link href="/today" className="icon-button" aria-label="返回">
          <ArrowLeft size={19} />
        </Link>
      </div>
      <main style={{ padding: "28px 22px 40px" }}>
        <h1 className="screen-title">{creating ? "创建新账号" : "登录"}</h1>
        <p className="caption mt-6" style={{ fontSize: 13 }}>
          {creating ? "从一张空白的生活手册开始。创建后即可进入，不会添加示例安排。" : "登录后，继续整理你的安排与生活记忆。"}
        </p>

        <form onSubmit={submit} className="card mt-20">
          <label className="field" htmlFor="login-email">
            <span className="field__label">邮箱</span>
            <input
              id="login-email"
              className="input"
              type="email"
              inputMode="email"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
            />
          </label>
          {creating ? <label className="field" htmlFor="register-name">
            <span className="field__label">称呼 / 昵称</span>
            <input id="register-name" className="input" autoComplete="nickname" maxLength={120} value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder="希望助手怎么称呼你" />
          </label> : null}
          <label className="field" htmlFor="login-password">
            <span className="field__label">密码</span>
            <input
              id="login-password"
              className="input"
              type="password"
              autoComplete={creating ? "new-password" : "current-password"}
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={creating ? "至少8个字符" : "账号密码"}
            />
          </label>

          {creating ? <label className="field" htmlFor="register-confirm">
            <span className="field__label">确认密码</span>
            <input id="register-confirm" className="input" type="password" autoComplete="new-password" maxLength={256} value={confirmPassword} onChange={(event) => setConfirmPassword(event.target.value)} placeholder="再输入一次密码" />
          </label> : null}
          {creating && options?.invite_required ? <label className="field" htmlFor="register-invite">
            <span className="field__label">注册邀请码</span>
            <input id="register-invite" className="input" type="password" autoComplete="off" maxLength={256} value={inviteCode} onChange={(event) => setInviteCode(event.target.value)} placeholder="填写管理员提供的邀请码" />
          </label> : null}
          {error ? (
            <p className="caption red" role="alert" style={{ margin: "2px 0 12px" }}>
              {error}
            </p>
          ) : null}

          <PrimaryButton type="submit" busy={busy} disabled={busy}>
            {busy ? (creating ? "正在创建…" : "正在登录…") : (creating ? "创建并进入" : "登录")}
          </PrimaryButton>
        </form>

        {(creating || options?.enabled) ? <button type="button" className="btn btn--quiet mt-20" disabled={busy} onClick={() => { setCreating(!creating); setError(null); setPassword(""); setConfirmPassword(""); setInviteCode(""); }}>
          {creating ? "已有账号？返回登录" : "还没有账号？创建新账号"}
        </button> : null}
        {options && !options.enabled ? <p className="caption mt-20">暂未开放注册，请联系管理员。</p> : null}
        {optionsError ? <p className="caption mt-20">暂时无法读取注册设置，请稍后刷新重试。已有账号仍可登录。</p> : null}
      </main>
    </div>
  );
}
