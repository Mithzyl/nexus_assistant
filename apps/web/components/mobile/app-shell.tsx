"use client";

/** Four-tab mobile shell, safe-area padding and on-screen keyboard handling. */

import { useEffect, useRef } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Layers, MessageCircle, Sun, UserRound } from "lucide-react";

import { AppProviders } from "./providers";
import { AskProvider } from "./ask-store";

const TABS = [
  { href: "/today", label: "今天", icon: Sun },
  { href: "/matters", label: "事务", icon: Layers },
  { href: "/ask", label: "助手", icon: MessageCircle },
  { href: "/me", label: "我的", icon: UserRound },
];

function isActive(pathname: string, href: string): boolean {
  if (href === "/today") return pathname === "/today" || pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

function TabBar() {
  const pathname = usePathname();
  return (
    <nav className="tabbar" aria-label="主导航">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const active = isActive(pathname, tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className="tab"
            aria-current={active ? "page" : undefined}
          >
            <Icon size={21} strokeWidth={active ? 2.1 : 1.7} aria-hidden="true" />
            <span>{tab.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  const shellRef = useRef<HTMLDivElement>(null);
  const pathname = usePathname();

  useEffect(() => {
    window.scrollTo({ top: 0, behavior: "instant" as ScrollBehavior });
  }, [pathname]);

  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;
    const update = () => {
      const offset = Math.max(
        0,
        window.innerHeight - (viewport?.height ?? window.innerHeight) - (viewport?.offsetTop ?? 0),
      );
      root.style.setProperty("--m-keyboard-offset", `${Math.round(offset)}px`);
      if (shellRef.current) {
        shellRef.current.dataset.keyboard = offset > 90 ? "true" : "false";
      }
    };
    update();
    viewport?.addEventListener("resize", update);
    viewport?.addEventListener("scroll", update);
    window.addEventListener("resize", update);
    return () => {
      viewport?.removeEventListener("resize", update);
      viewport?.removeEventListener("scroll", update);
      window.removeEventListener("resize", update);
    };
  }, []);

  return (
    <div className="m-app" ref={shellRef} data-keyboard="false">
      <main className="m-content">{children}</main>
      <TabBar />
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <AppProviders>
      <AskProvider>
        <Shell>{children}</Shell>
      </AskProvider>
    </AppProviders>
  );
}
