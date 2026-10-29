"use client";

import { useCallback, useEffect, useState } from "react";

import type { TodayView } from "./api";
import { getToday, TODAY_REFRESH_EVENT } from "./matters-api";

export function useToday(enabled: boolean, date?: string) {
  const [data, setData] = useState<TodayView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const [token, setToken] = useState(0);

  const refresh = useCallback(() => setToken((value) => value + 1), []);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener(TODAY_REFRESH_EVENT, refresh);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(TODAY_REFRESH_EVENT, refresh);
      window.removeEventListener("focus", refresh);
    };
  }, [enabled, refresh]);

  useEffect(() => {
    if (!enabled) {
      setData(null);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getToday(date, controller.signal)
      .then((next) => setData(next))
      .catch((caught) => {
        if (!controller.signal.aborted) setError(caught instanceof Error ? caught : new Error("读取今天失败"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [date, enabled, token]);

  return { data, loading, error, refresh };
}
