"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArrowRight, ChevronRight, FlaskConical, RotateCcw } from "lucide-react";

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
    <PageShell title="实验室" subtitle="概念预览与旧版演示，独立于正式数据" fallbackHref="/me">
      <div className="m-stack-18" style={{ padding: "0 var(--m-gutter)" }}>
        <Notice title="概念预览" tone="amber">
          示例只存在这台设备。
        </Notice>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>计划接续预览</h2>
          <div className="card">
            <p className="card-title" style={{ fontSize: 16 }}>事项、日程与冲突调整</p>
            <p className="caption" style={{ margin: "4px 0 0" }}>
              用本机示例试试分组、详情、补充内容和冲突调整；正式事项请回到“事项”页。
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
                toast("已重置当前设备预览示例");
              }}>
                <RotateCcw size={14} /> 重置预览数据
              </button>
            </div>
            <p className="micro" style={{ margin: "10px 0 0" }}>
              {demo.enabled
                ? `本机预览有 ${plan.matters.length} 条记录。删除或停止后，重置可恢复示例。`
                : "开启后会生成三条本机示例，只留在这台设备。"}
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
                <ChevronRight size={16} className="chevron" aria-hidden="true" />
              </button>
            ))}
          </div>
          <p className="micro" style={{ margin: "8px 0 0" }}>
            示例只在这里出现，和正式事项分开。
          </p>
        </section>

        <section className="card card--tight">
          <SwitchRow
            title={demo.enabled ? "退出示例，看回我自己的数据" : "查看示例"}
            detail="示例和你的正式事项分开保存。"
            checked={demo.enabled}
            onChange={() => {
              demo.setEnabled(!demo.enabled);
              toast(demo.enabled ? "已退出示例" : "已开启演示模式，仅使用当前设备示例");
            }}
          />
        </section>

        <section>
          <h2 className="section-title" style={{ marginBottom: 8 }}>高级选项</h2>
          <div className="group">
            <Link href="/board" className="row">
              <span className="tile tile--sm"><Archive size={14} strokeWidth={1.5} /></span>
              <span className="row__body">
                <span className="row__title" style={{ display: "block" }}>旧版看板</span>
                <span className="row__detail" style={{ display: "block" }}>只读示例</span>
              </span>
              <ChevronRight size={16} className="chevron" aria-hidden="true" />
            </Link>
          </div>
        </section>

        <Link href="/me" className="btn btn--quiet">返回我的</Link>
      </div>
    </PageShell>
  );
}
