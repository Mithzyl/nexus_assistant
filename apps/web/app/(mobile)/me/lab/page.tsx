"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowRight, FlaskConical, RotateCcw } from "lucide-react";

import { useDemo, useUI } from "@/components/mobile/providers";
import { useWorkPlan } from "@/components/mobile/work-plan-store";
import { Notice, PageShell, SwitchRow } from "@/components/mobile/ui";

const CONSUMER_LINKS = [
  { href: "/matters/coffee", title: "退款归属", detail: "咖啡机与收纳架金额相同，展示旧版事务示例。" },
  { href: "/matters/movie", title: "电影票与出门清单", detail: "旧版票据、影院经验示例。" },
  { href: "/matters/wish", title: "愿望清单", detail: "尚未闭环的心愿示例。" },
  { href: "/matters/prepare", title: "出门准备", detail: "旧版准备清单示例。" },
];

export default function LabPage() {
  const router = useRouter();
  const demo = useDemo();
  const plan = useWorkPlan();
  const { toast } = useUI();

  const openConsumer = (href: string) => {
    if (!demo.enabled) {
      demo.setEnabled(true);
      toast("已开启演示模式，仅用于查看旧版示例");
    }
    router.push(href);
  };

  return (
    <PageShell title="实验室" subtitle="概念预览与旧版演示，不进入正式时间线" fallbackHref="/me">
      <div className="m-stack-18" style={{ padding: "0 var(--m-gutter)" }}>
        <Notice title="概念预览" tone="amber">
          这里的内容只在本浏览器演示，不写入 PA 服务器，不进入正式“今天”或“事项”，也不代表 iOS 日历已同步。
        </Notice>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>计划接续预览</h2>
          <div className="card">
            <p className="card-title" style={{ fontSize: 16 }}>事项、日程与冲突调整</p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              用本机示例检查分组、详情、补充进展、停止、删除和冲突方案。真实事项接口尚未提供。
            </p>
            <div className="m-stack-8 mt-12">
              <button type="button" className="btn btn--primary" onClick={() => {
                if (!demo.enabled) demo.setEnabled(true);
                router.push("/me/lab/plan");
              }}>
                打开事项预览 <ArrowRight size={14} />
              </button>
              <button type="button" className="btn btn--secondary" onClick={() => {
                if (!demo.enabled) demo.setEnabled(true);
                router.push("/today");
              }}>
                查看正式今天
              </button>
              <button type="button" className="btn btn--quiet" onClick={() => {
                plan.resetPreview();
                toast("已重置本机预览示例");
              }}>
                <RotateCcw size={14} /> 重置预览数据
              </button>
            </div>
            <p className="micro" style={{ margin: "10px 0 0" }}>
              {demo.enabled
                ? `当前本机预览有 ${plan.matters.length} 条记录。删除或停止后，重置可恢复示例。`
                : "开启预览后会生成三条本机示例；不会写入服务器。"}
            </p>
          </div>
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>旧版消费与生活演示</h2>
          <div className="group">
            {CONSUMER_LINKS.map((item) => (
              <button key={item.href} type="button" className="row" onClick={() => openConsumer(item.href)}>
                <span className="tile tile--sm"><FlaskConical size={14} /></span>
                <span className="row__body">
                  <span className="row__title" style={{ display: "block" }}>{item.title}</span>
                  <span className="row__detail" style={{ display: "block" }}>{item.detail}</span>
                </span>
                <span className="chevron">›</span>
              </button>
            ))}
          </div>
          <p className="micro" style={{ margin: "8px 0 0" }}>
            这些入口只从实验室打开；当前“事项”列表不再混入消费金额、退款阶段等旧演示信息。
          </p>
        </section>

        <section className="card card--tight">
          <SwitchRow
            title="演示模式"
            detail="关闭后，“今天”和“事项”回到真实空状态；本机预览数据保留但不再展示。"
            checked={demo.enabled}
            onChange={() => {
              demo.setEnabled(!demo.enabled);
              toast(demo.enabled ? "已关闭演示模式" : "已开启演示模式，仅使用本机示例");
            }}
          />
        </section>

        <Link href="/me" className="btn btn--quiet">返回我的</Link>
      </div>
    </PageShell>
  );
}
