"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import type { CommutePlan, CommutePlanDraft } from "@/lib/mobile/api";
import { ApiError, apiRequest } from "@/lib/mobile/api";
import { useAuth } from "@/components/mobile/providers";
import { CommuteForm, type SavedPlace } from "@/components/mobile/commute-form";
import { useApiResource } from "@/lib/mobile/use-api-resource";
import { EmptyState, PageShell } from "@/components/mobile/ui";

export default function NewCommutePage() {
  const router = useRouter();
  const { state } = useAuth();
  const signedIn = state === "signedIn";
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const plans = useApiResource<CommutePlan[]>(
    signedIn ? "/api/pa/commute-plans" : null,
    { enabled: signedIn },
  );
  const savedPlaces = useMemo<SavedPlace[]>(() => {
    const map = new Map<string, SavedPlace>();
    for (const plan of plans.data ?? []) {
      for (const location of [plan.origin, plan.destination]) {
        if (!location.label.trim()) continue;
        map.set(`${location.label}|${location.latitude}|${location.longitude}`, location);
      }
    }
    return Array.from(map.values());
  }, [plans.data]);

  const submit = async (draft: CommutePlanDraft) => {
    setSaving(true);
    setError(null);
    try {
      const created = await apiRequest<CommutePlan>("/api/pa/commute-plans", {
        method: "POST",
        body: draft,
      });
      router.replace(`/me/commute/${created.id}`);
    } catch (caught) {
      setError(
        caught instanceof ApiError
          ? caught.status === 401
            ? "登录状态已失效，请重新登录。"
            : caught.message
          : "保存未能完成，请稍后再试。",
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <PageShell title="新建通勤计划" subtitle="地点坐标需手动填写；浏览器不生成路线。">
      {!signedIn && state !== "loading" ? (
        <EmptyState title="登录后保存通勤计划" body="登录账号后，通勤计划会同步到云端。">
          <Link href="/login" className="btn btn--primary">登录</Link>
        </EmptyState>
      ) : null}
      {signedIn ? (
        <CommuteForm
          savedPlaces={savedPlaces}
          submitLabel="保存并继续"
          saving={saving}
          serverError={error}
          onSubmit={(draft) => void submit(draft)}
        />
      ) : null}
    </PageShell>
  );
}
