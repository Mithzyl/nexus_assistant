"use client";

import Link from "next/link";

import { PageShell } from "@/components/mobile/ui";

export default function AboutPage() {
  return (
    <PageShell title="关于生活助手" subtitle="记住要事，理清安排，持续跟进">
      <section className="card">
        <p className="serif" style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
          把日程、待办和个人记忆放在一个地方
        </p>
        <p className="body-text muted mt-8" style={{ marginBottom: 0 }}>
          生活助手帮你收拢每天要做的事，整理零散的想法，并持续跟进那些需要时间才能完成的事情。
        </p>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>你可以用它做什么</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li><strong>今天</strong>：查看日程、提醒和需要处理的事，也可以把消息或待办交给助手整理。</li>
          <li><strong>事项</strong>：把长期的事拆成下一步，记录进展，随时回看变化。</li>
          <li><strong>问助手</strong>：用日常语言提问，助手会先给出一份待确认草稿，经你确认后再更新安排。</li>
          <li><strong>个人记忆</strong>：保存你主动写下的经验，之后按关键词找回。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>你的内容</h2>
        <p className="body-text" style={{ margin: "8px 0 0" }}>
          日程与提醒会同步到你的账号；个人记忆只有在你确认后才会保存；删除后不再引用。
        </p>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>使用边界</h2>
        <p className="body-text" style={{ margin: "8px 0 0" }}>
          网页版保持打开时才能接收页面内提醒；日历订阅的提醒由日历应用负责；地图与路线需要先设置常用地点。
        </p>
      </section>

      <div className="mt-16" style={{ display: "grid", gap: 8 }}>
        <Link href="/me/privacy" className="btn btn--secondary">隐私说明</Link>
        <Link href="/me/sources" className="btn btn--secondary">内容来源</Link>
      </div>

      <p className="micro mt-16" style={{ lineHeight: 1.7 }}>
        生活助手 · 版本 0.1.0
      </p>
    </PageShell>
  );
}
