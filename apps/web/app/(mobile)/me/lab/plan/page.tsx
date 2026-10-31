"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowDownRight, ArrowRight, Plus, Sparkles } from "lucide-react";

import { useDemo, useUI } from "@/components/mobile/providers";
import { PreviewBanner } from "@/components/mobile/work-plan-ui";
import { useWorkPlan } from "@/components/mobile/work-plan-store";
import { EmptyState, LoadingCard, ScreenHeader } from "@/components/mobile/ui";
import { WORK_STATUS_LABEL, dateTimeLabel, type WorkMatter, type WorkMatterStatus } from "@/lib/mobile/work-plan";

function MatterCard({ matter }: { matter: WorkMatter }) {
  return (
    <Link href={`/me/lab/plan/${matter.id}`} className="card matter-card">
      <span className="card-title" style={{ display: "block" }}>
        {matter.title}
      </span>
      <span className="matter-card__progress">{matter.progress}</span>
      <span className="matter-card__next">
        <ArrowDownRight size={13} />
        <span>{matter.nextAction || "下一步待定"}</span>
        {matter.nextAt ? <span className="matter-card__time">{dateTimeLabel(matter.nextAt)}</span> : null}
      </span>
      {matter.stopped ? <span className="micro">已停止跟进</span> : null}
    </Link>
  );
}

export default function MattersPage() {
  const router = useRouter();
  const demo = useDemo();
  const plan = useWorkPlan();
  const { openCapture, toast } = useUI();

  const groups: Array<{ status: WorkMatterStatus; items: WorkMatter[] }> = [
    { status: "active", items: plan.active },
    { status: "waiting", items: plan.waiting },
    { status: "archived", items: plan.archived },
  ];

  return (
    <div className="m-screen m-stack-18" style={{ paddingBottom: 28 }}>
      <ScreenHeader
        title="事项"
        subtitle="需要持续推进的事，以及下一步"
        action={
          <button type="button" className="icon-button" onClick={() => openCapture("choose")} aria-label="交给助手">
            <Plus size={18} />
          </button>
        }
      />

      {!demo.enabled ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <EmptyState
            title="实验室只展示当前设备概念"
            body="正式事项、日程和提醒已在“事项”页通过云端草稿确认；实验室不会把本地示例混入正式数据。"
          >
            <div className="m-stack-8">
              <Link href="/me/lab" className="btn btn--secondary">
                查看实验室与概念预览
              </Link>
              <Link href="/ask" className="btn btn--secondary">
                向助手交代一件事
              </Link>
              <button
                type="button"
                className="btn btn--quiet"
                onClick={() => {
                  demo.setEnabled(true);
                  toast("已开启概念预览，仅使用当前设备示例");
                }}
              >
                显式开启概念预览
              </button>
            </div>
          </EmptyState>
          <p className="micro text-center mt-16">
            需要真实写入时请回到事项页；这里的当前设备预览与云端数据保持隔离。
          </p>
        </div>
      ) : !plan.ready ? (
        <div style={{ padding: "0 var(--m-gutter)" }}>
          <LoadingCard label="正在读取当前设备预览…" />
        </div>
      ) : (
        <>
          <div style={{ padding: "0 var(--m-gutter)" }}>
            <PreviewBanner />
          </div>

          {groups.map((group) => (
            <section key={group.status} className="stack-12">
              <div className="flex items-center justify-between gap-12" style={{ padding: "0 var(--m-gutter)" }}>
                <h2 className="section-title" style={{ marginBottom: 0 }}>
                  {WORK_STATUS_LABEL[group.status]}
                </h2>
                <span className="micro">{group.items.length}</span>
              </div>
              {group.items.length === 0 ? (
                <p className="micro" style={{ padding: "0 var(--m-gutter)", margin: "4px 0 0" }}>
                  {group.status === "archived" ? "还没有已完成或归档的事项。" : "这一组暂时为空。"}
                </p>
              ) : (
                <div className="m-stack-10" style={{ padding: "0 var(--m-gutter)" }}>
                  {group.items.map((matter) => (
                    <MatterCard key={matter.id} matter={matter} />
                  ))}
                </div>
              )}
            </section>
          ))}

          <div style={{ padding: "0 var(--m-gutter)" }}>
            <button
              type="button"
              className="btn btn--secondary"
              onClick={() => router.push("/me/lab")}
            >
              <Sparkles size={14} /> 实验室与概念预览
              <ArrowRight size={14} />
            </button>
            <p className="micro text-center mt-8">
              预览数据只在本浏览器；停止、删除和确认都不会改变云端。
            </p>
          </div>
        </>
      )}
    </div>
  );
}
