"use client";

/** Login form used by `/login` and by inline "login required" prompts. */

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";

import { ApiError } from "@/lib/mobile/api";
import { useAuth } from "@/components/mobile/providers";
import { PrimaryButton } from "@/components/mobile/buttons";

export function LoginScreen() {
  const router = useRouter();
  const { state, user, login } = useAuth();
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
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      router.replace("/today");
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(
          caught.status === 401
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
        <h1 className="screen-title">登录</h1>
        <p className="caption mt-6" style={{ fontSize: 13 }}>
          使用 PA 本地账号。登录后才能向服务器提问、读取已同步内容和使用通勤提醒。
        </p>

        <form onSubmit={submit} className="mt-20">
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
          <label className="field" htmlFor="login-password">
            <span className="field__label">密码</span>
            <input
              id="login-password"
              className="input"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="PA 账号密码"
            />
          </label>

          {error ? (
            <p className="caption red" role="alert" style={{ margin: "2px 0 12px" }}>
              {error}
            </p>
          ) : null}

          <PrimaryButton type="submit" busy={busy} disabled={busy}>
            {busy ? "正在登录…" : "登录"}
          </PrimaryButton>
        </form>

        <div className="notice mt-20">
          <strong>关于登录态</strong>
          <p style={{ margin: "4px 0 0" }}>
            令牌保存在 httpOnly Cookie 中，网页脚本读不到。退出时会撤销当前设备的会话；无效或过期的凭据会让你重新登录。
          </p>
        </div>

        <p className="micro text-center mt-20" style={{ lineHeight: 1.7 }}>
          不需要注册新的平台账号，也没有第三方登录。
          <br />
          登录只用于复用现有 PA 账号与服务器上的数据。
        </p>
      </main>
    </div>
  );
}
