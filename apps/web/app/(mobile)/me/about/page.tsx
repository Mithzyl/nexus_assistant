"use client";

import Link from "next/link";

import { PageShell, Pill } from "@/components/mobile/ui";

const ROWS: Array<{ area: string; real: string; local: string }> = [
  {
    area: "今天",
    real: "日程、提醒、截止事项、待确认草稿、路线状态、已生效变更与通勤建议",
    local: "本机“收下内容/记一句”记录；不进入正式时间线",
  },
  {
    area: "事项",
    real: "事项分组与详情、结构化日程/待办草稿、变更历史、撤回与服务器确认",
    local: "实验室中的工作安排概念预览；与正式事项隔离",
  },
  {
    area: "问助手",
    real: "真实登录、提交问答、状态、SSE、取消、刷新恢复",
    local: "无示例回答；网络失败不会回退到演示答案",
  },
  {
    area: "我的",
    real: "账号与会话、内容来源与云端内容、Bark、通勤计划/检查/建议",
    local: "个人记忆演示、收下内容的本地记录、实验室与同步状态说明",
  },
  {
    area: "记忆检索",
    real: "遗留只读代理，取决于上游是否可用",
    local: "无；不提供新增、编辑、停用或自动学习",
  },
];

export default function AboutPage() {
  return (
    <PageShell title="关于生活助手" subtitle="Personal Assistant 的移动 Web 客户端">
      <section className="card">
        <p className="serif" style={{ fontSize: 22, fontWeight: 600, margin: 0 }}>
          今天、事项、问助手、我的。
        </p>
        <p className="body-text muted mt-8" style={{ marginBottom: 0 }}>
          这是现有 PA 后端的浏览器客户端，不是管理后台、统计看板或 AI 平台控制台。
          视觉采用暖白背景与雾蓝主色，跟随系统深色模式。
        </p>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>当前能力</h2>
        <div className="mt-10" style={{ display: "grid", gap: 12 }}>
          {ROWS.map((row) => (
            <div key={row.area}>
              <div className="flex items-center gap-8">
                <Pill tone="fog">{row.area}</Pill>
              </div>
              <p className="caption" style={{ margin: "6px 0 0" }}>
                <strong style={{ color: "var(--m-ink)", fontWeight: 600 }}>已接入：</strong>
                {row.real}
              </p>
              <p className="caption" style={{ margin: "2px 0 0" }}>
                <strong style={{ color: "var(--m-ink)", fontWeight: 600 }}>本地/演示：</strong>
                {row.local}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>原生能力的 Web 处理</h2>
        <ul className="body-text" style={{ paddingLeft: "1.2em", margin: "8px 0 0" }}>
          <li>日历/提醒事项/备忘录：只读取已同步到服务器的云端副本，不声称获得系统读取权限。</li>
          <li>地图与路线：浏览器没有地图选点和路线 ETA；通勤坐标需手动填写，检查不上传路线快照。</li>
          <li>APNs：不使用原生设备注册；Web Push 未接入，不构造“已送达”列表。</li>
          <li>Bark：复用现有账号绑定接口，只从服务器侧发送，浏览器不直接调用 Bark。</li>
          <li>分享入口：先实现粘贴与文本文件选择；系统分享与 Apple 备忘录不在此范围。</li>
          <li>Keychain：改用 httpOnly Cookie 保存会话；与原生 Keychain 不同，这是浏览器的合理替代。</li>
          <li>已提供 manifest、图标和 standalone 启动；离线写入、后台同步和关闭网页后的提醒仍未支持。</li>
        </ul>
      </section>

      <section className="card mt-14">
        <h2 className="section-title" style={{ fontSize: 18 }}>调用关系</h2>
        <p className="body-text" style={{ margin: "8px 0 0" }}>
          移动浏览器 → Web 同源转发层 → assistant-api → AI 调用平台。
          浏览器只会发起同源请求；平台令牌、模型配置和角色绑定都留在服务端。
        </p>
        <p className="caption" style={{ margin: "8px 0 0" }}>
          问答失败时页面显示真实失败或“结果不确定”，不会自动重试成第二次业务执行，也不会把演示答案当作服务器结果。
        </p>
      </section>

      <div className="mt-16" style={{ display: "grid", gap: 8 }}>
        <Link href="/me/privacy" className="btn btn--secondary">内容与隐私</Link>
        <Link href="/me/sources" className="btn btn--secondary">内容来源</Link>
        <Link href="/board" className="btn btn--quiet">打开旧版看板</Link>
      </div>

      <p className="micro text-center mt-16" style={{ lineHeight: 1.7 }}>
        生活助手 · 移动 Web · 版本 0.1.0
        <br />
        界面与交互以当前 iOS 客户端为主要参考；服务器能力以 assistant-api 实际接口为准。
      </p>
    </PageShell>
  );
}
