"use client";

/**
 * Browser-local work-plan preview. It is intentionally not wired to /api/pa:
 * the backend has no matter/schedule/reminder write contract yet. Pages must
 * show the preview label and never describe this as server-saved or iOS-synced.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  applyConflictOption,
  seedWorkMatters,
  type WorkConflict,
  type WorkHistory,
  type WorkMatter,
  type WorkSchedule,
} from "@/lib/mobile/work-plan";
import { useAuth, useDemo } from "./providers";

const STORAGE_PREFIX = "pa.web.workplan.v1.";
const SEED_PREFIX = "pa.web.workplan.seeded.v1.";

interface WorkPlanValue {
  ready: boolean;
  matters: WorkMatter[];
  active: WorkMatter[];
  waiting: WorkMatter[];
  archived: WorkMatter[];
  getMatter: (id: string) => WorkMatter | undefined;
  addProgress: (id: string, text: string) => void;
  updateMatter: (
    id: string,
    patch: Partial<Pick<WorkMatter, "title" | "goal" | "status" | "progress" | "nextAction" | "nextAt">>,
  ) => void;
  updateSchedule: (matterId: string, scheduleId: string, patch: Partial<WorkSchedule>) => void;
  toggleTodo: (matterId: string, todoId: string) => void;
  stopFollowing: (id: string) => void;
  deleteMatter: (id: string) => void;
  applyOption: (conflict: WorkConflict, optionId: string) => void;
  resetPreview: () => void;
}

const WorkPlanContext = createContext<WorkPlanValue | null>(null);

export function useWorkPlan(): WorkPlanValue {
  const value = useContext(WorkPlanContext);
  if (!value) throw new Error("useWorkPlan must be used inside WorkPlanProvider");
  return value;
}

function readMatters(key: string): WorkMatter[] | null {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as WorkMatter[]) : null;
  } catch {
    return null;
  }
}

export function WorkPlanProvider({ children }: { children: React.ReactNode }) {
  const { state, user } = useAuth();
  const demo = useDemo();
  const accountId = user?.user_id ?? null;
  const storageKey = accountId ? `${STORAGE_PREFIX}${accountId}` : null;
  const seedKey = accountId ? `${SEED_PREFIX}${accountId}` : null;
  const [matters, setMatters] = useState<WorkMatter[]>([]);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setReady(false);
    if (!storageKey) {
      setMatters([]);
      setReady(true);
      return;
    }
    setMatters(readMatters(storageKey) ?? []);
    setReady(true);
  }, [storageKey]);

  useEffect(() => {
    if (!ready || !storageKey) return;
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(matters));
    } catch {
      // Preview still works in memory when storage is unavailable.
    }
  }, [matters, ready, storageKey]);

  useEffect(() => {
    if (!demo.enabled || !ready || !storageKey || !seedKey) return;
    if (matters.length > 0) return;
    try {
      if (window.localStorage.getItem(seedKey) === "1") return;
      window.localStorage.setItem(seedKey, "1");
    } catch {
      // fall through and seed in memory
    }
    setMatters(seedWorkMatters());
  }, [demo.enabled, matters.length, ready, seedKey, storageKey]);

  const getMatter = useCallback(
    (id: string) => matters.find((matter) => matter.id === id),
    [matters],
  );

  const addProgress = useCallback((id: string, text: string) => {
    const trimmed = text.trim();
    if (!trimmed) return;
    setMatters((current) =>
      current.map((matter) => {
        if (matter.id !== id) return matter;
        const entry: WorkHistory = {
          id: `hist-${Date.now()}`,
          at: new Date().toISOString(),
          text: trimmed,
        };
        return { ...matter, progress: trimmed, history: [entry, ...matter.history] };
      }),
    );
  }, []);

  const updateMatter = useCallback<WorkPlanValue["updateMatter"]>((id, patch) => {
    setMatters((current) =>
      current.map((matter) => (matter.id === id ? { ...matter, ...patch } : matter)),
    );
  }, []);

  const updateSchedule = useCallback<WorkPlanValue["updateSchedule"]>(
    (matterId, scheduleId, patch) => {
      setMatters((current) =>
        current.map((matter) =>
          matter.id === matterId
            ? {
                ...matter,
                schedules: matter.schedules.map((schedule) =>
                  schedule.id === scheduleId ? { ...schedule, ...patch } : schedule,
                ),
              }
            : matter,
        ),
      );
    },
    [],
  );

  const toggleTodo = useCallback((matterId: string, todoId: string) => {
    setMatters((current) =>
      current.map((matter) =>
        matter.id === matterId
          ? {
              ...matter,
              todos: matter.todos.map((todo) =>
                todo.id === todoId ? { ...todo, done: !todo.done } : todo,
              ),
            }
          : matter,
      ),
    );
  }, []);

  const stopFollowing = useCallback((id: string) => {
    setMatters((current) =>
      current.map((matter) =>
        matter.id === id ? { ...matter, status: "archived", stopped: true } : matter,
      ),
    );
  }, []);

  const deleteMatter = useCallback((id: string) => {
    setMatters((current) => current.filter((matter) => matter.id !== id));
  }, []);

  const applyOption = useCallback((conflict: WorkConflict, optionId: string) => {
    setMatters((current) => applyConflictOption(current, conflict, optionId));
  }, []);

  const resetPreview = useCallback(() => {
    if (seedKey) {
      try {
        window.localStorage.setItem(seedKey, "1");
      } catch {
        // ignore
      }
    }
    setMatters(seedWorkMatters());
  }, [seedKey]);

  const value = useMemo<WorkPlanValue>(() => {
    const visible = matters;
    return {
      ready: ready && (state !== "loading" || Boolean(storageKey)),
      matters,
      active: visible.filter((matter) => matter.status === "active"),
      waiting: visible.filter((matter) => matter.status === "waiting"),
      archived: visible.filter((matter) => matter.status === "archived"),
      getMatter,
      addProgress,
      updateMatter,
      updateSchedule,
      toggleTodo,
      stopFollowing,
      deleteMatter,
      applyOption,
      resetPreview,
    };
  }, [
    matters,
    ready,
    state,
    storageKey,
    getMatter,
    addProgress,
    updateMatter,
    updateSchedule,
    toggleTodo,
    stopFollowing,
    deleteMatter,
    applyOption,
    resetPreview,
  ]);

  return <WorkPlanContext.Provider value={value}>{children}</WorkPlanContext.Provider>;
}
