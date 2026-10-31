"use client";

import Link from "next/link";

import { PageShell } from "@/components/mobile/ui";

export default function PrivacyPage() {
  return (
    <PageShell
      title="隐私说明"
      subtitle="我们如何保存、使用和删除你的内容"
    >
      <section className="card">
        <h2 className="section-title" style={{ fontSize: 18 }}>保存在账号里的内容</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>账号信息与登录状态（不保存明文密码）。</li>
          <li>你向助手提交的问题、回答与完成时间。</li>
          <li>导入的笔记、内容来源与授权范围。</li>
          <li>通勤计划、提醒绑定与通勤建议。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>只保存在当前设备</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>当前设备的登录状态。</li>
          <li>“本机暂存”里的内容，按账号隔离保存。</li>
          <li>问答草稿、外观选择与安装标识。</li>
        </ul>
        <p className="caption" style={{ margin: "8px 0 0" }}>
          退出登录会结束当前设备的登录状态；本机暂存会保留，直到你删除它或清理浏览器数据。
        </p>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>我们不会做</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>不读取你的屏幕、相册、麦克风或其他应用。</li>
          <li>不擅自修改正式日程；每次变更都需要你确认。</li>
          <li>不把当前设备的日历、提醒事项写回。</li>
          <li>不向页面暴露平台密钥或内部凭据。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>删除与退出</h2>
        <p className="body-text" style={{ margin: "8px 0 0" }}>
          在来源详情里可以删除上传记录或整个来源；本机暂存可以在“今天”的最近整理中删除；退出登录只影响当前设备。
        </p>
        <p className="caption" style={{ margin: "8px 0 0" }}>
          删除后，相关内容不会再被引用。账号中的其他内容会继续保留，直到你主动删除。
        </p>
        <div className="mt-10" style={{ display: "grid", gap: 8 }}>
          <Link href="/me/sources" className="btn btn--secondary">管理内容来源</Link>
          <Link href="/me/about" className="btn btn--secondary">返回关于生活助手</Link>
        </div>
      </section>
    </PageShell>
  );
}
