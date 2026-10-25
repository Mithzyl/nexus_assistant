"use client";

import Link from "next/link";

import { PageShell } from "@/components/mobile/ui";

export default function PrivacyPage() {
  return (
    <PageShell
      title="内容与隐私"
      subtitle="按当前实现描述：只说明实际保存与删除的行为，不对未接入的能力作承诺。"
    >
      <section className="card">
        <h2 className="section-title" style={{ fontSize: 18 }}>服务器保存什么</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>PA 本地账号、密码校验摘要与当前设备会话（不保存明文密码）。</li>
          <li>你向助手提交的问题、状态、最终回答与完成时间。</li>
          <li>内容来源登记、已上传的副本与字段授权；浏览器导入的笔记也存放在这里。</li>
          <li>通勤计划、手动检查、建议、动作记录与 Bark 绑定状态（Bark Key 只存服务器、不回显）。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>只在这台浏览器里</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>httpOnly Cookie 里的会话令牌；JavaScript 读不到它。</li>
          <li>“收下内容 / 记一句”产生的本机记录，按账号隔离保存在 localStorage。</li>
          <li>问答草稿（sessionStorage）、问答本地缓存、外观选择、演示模式开关与安装 ID。</li>
        </ul>
        <p className="caption" style={{ margin: "8px 0 0" }}>
          退出登录会撤销当前设备会话并清理页面内的问答状态；按账号隔离的本机记录会保留，直到你在本机删除或清除浏览器数据。
        </p>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>不会做的事</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>不读取真实的屏幕、通知、相册或麦克风；收下内容只能由你粘贴、输入或选择文本文件。</li>
          <li>不因为“这次没有出现”就推断某条内容被删除；删除必须走显式操作。</li>
          <li>不把设备上的日历、提醒事项或 Apple 备忘录写回；浏览器也没有这些系统权限。</li>
          <li>不向浏览器暴露平台令牌、模型密钥或角色绑定；AI 调用由 PA 服务端转发。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>删除与退出</h2>
        <p className="body-text" style={{ margin: "8px 0 0" }}>
          在来源详情里可以删除“已上传副本”（保留来源与授权），也可以删除整个来源及其副本。
          本机记录可以在“今天”的最近整理中删除。退出登录撤销当前设备会话，其他设备不受影响。
        </p>
        <p className="caption" style={{ margin: "8px 0 0" }}>
          当前没有面向用户的“原文保留 N 天”设置接口，因此页面不提供无法实际生效的保留期限开关。
          服务器侧的通勤数据保留策略由部署配置决定，不在浏览器里承诺。
        </p>
        <div className="mt-10" style={{ display: "grid", gap: 8 }}>
          <Link href="/me/sources" className="btn btn--secondary">管理内容来源与副本</Link>
          <Link href="/me/about" className="btn btn--secondary">能力与原生限制</Link>
        </div>
      </section>
    </PageShell>
  );
}
