"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bell, MapPin, Plus } from "lucide-react";

import type { CommutePlan } from "@/lib/mobile/api";
import { useApiResource, errorText } from "@/lib/mobile/use-api-resource";
import { useAuth } from "@/components/mobile/providers";
import {
  EmptyState,
  ErrorCard,
  LoadingCard,
  Notice,
  PageShell,
} from "@/components/mobile/ui";

const DAYS = ["一", "二", "三", "四", "五", "六", "日"];

function weekdayText(weekdays: number[]): string {
  if (weekdays.length === 5 && [1, 2, 3, 4, 5].every((day) => weekdays.includes(day))) {
    return "工作日";
  }
  if (weekdays.length === 7) return "每天";
  return weekdays.map((day) => `周${DAYS[day - 1]}`).join("、");
}

export default function CommutePage() {
  const { state } = useAuth();
  const router = useRouter();
  const signedIn = state === "signedIn";
  const plans = useApiResource<CommutePlan[]>(
    signedIn ? "/api/pa/commute-plans" : null,
    { enabled: signedIn },
  );

  return (
    <div style={{ paddingBottom: 24 }}>
      <PageShell
        title="通勤设置"
        subtitle="固定地点与时间，由服务器检查天气和路况；浏览器不负责后台调度。"
        action={
          <button
            type="button"
            className="page-bar__action"
            onClick={() => router.push("/me/commute/new")}
          >
            新建
          </button>
        }
      >
        {!signedIn && state !== "loading" ? (
          <EmptyState title="登录后才能管理通勤" body="通勤计划、检查与 Bark 提醒都绑定到 PA 账号。">
            <Link href="/login" className="btn btn--primary">登录 PA 账号</Link>
          </EmptyState>
        ) : null}

        {signedIn ? (
          <>
            {plans.loading ? <LoadingCard label="正在读取通勤计划…" /> : null}
            {plans.error ? (
              <ErrorCard message={errorText(plans.error)} onRetry={plans.reload} />
            ) : null}

            {!plans.loading && !plans.error && (plans.data?.length ?? 0) === 0 ? (
              <EmptyState
                title="还没有通勤计划"
                body="设立固定起点、终点和时间后，服务器可以按计划生成检查与提醒。"
              >
                <Link href="/me/commute/new" className="btn btn--primary">
                  <Plus size={14} /> 新建计划
                </Link>
              </EmptyState>
            ) : null}

            <div className="m-stack-10">
              {(plans.data ?? []).map((plan) => (
                <button
                  key={plan.id}
                  type="button"
                  className="card"
                  style={{ width: "100%", textAlign: "left" }}
                  onClick={() => router.push(`/me/commute/${plan.id}`)}
                >
                  <span className="card-head">
                    <span className="tile tile--sm">
                      <MapPin size={15} />
                    </span>
                    <span className="grow">
                      <span className="card-title" style={{ display: "block", fontSize: 16 }}>
                        {plan.name}
                      </span>
                      <span className="caption" style={{ display: "block", marginTop: 2 }}>
                        {weekdayText(plan.weekdays)} · {plan.arrival_local_time} 到达 ·{" "}
                        {plan.transport_mode === "driving" ? "驾车" : plan.transport_mode === "transit" ? "公交" : "步行"}
                      </span>
                      <span className="micro" style={{ display: "block", marginTop: 2 }}>
                        {plan.origin.label} → {plan.destination.label}
                      </span>
                    </span>
                    <span className={`pill ${plan.enabled ? "pill--fog" : ""}`}>
                      {plan.enabled ? "启用" : "停用"}
                    </span>
                  </span>
                </button>
              ))}
            </div>

            <div className="mt-16">
              <Link href="/me/commute/bark" className="card card--tight" style={{ display: "block" }}>
                <span className="flex items-center gap-10">
                  <span className="tile tile--sm">
                    <Bell size={15} />
                  </span>
                  <span className="grow">
                    <span className="card-title" style={{ display: "block", fontSize: 15 }}>
                      Bark 提醒
                    </span>
                    <span className="caption" style={{ display: "block", marginTop: 2 }}>
                      绑定账号接收服务端推送；Bark 不是 Web Push，也不代表已送达
                    </span>
                  </span>
                  <span className="chevron">›</span>
                </span>
              </Link>
            </div>

            <Notice tone="amber" title="原生能力边界">
              网页组合草稿支持地点候选、真实驾车与公交测算及路线地图；手动计划表单仍需填写坐标。
              未来日期检查、天气与推送取决于服务器调度和通知配置，需另行核验。
            </Notice>
          </>
        ) : null}
      </PageShell>
    </div>
  );
}
