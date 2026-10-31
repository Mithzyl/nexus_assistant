"use client";

import { useCallback, useEffect, useState } from "react";

import { ApiError, apiRequest } from "./api";

interface Resource<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
  setData: (value: T | null) => void;
}

/**
 * Minimal GET resource hook with abort-on-unmount and explicit reload.
 * A null path (or `enabled: false`) means "not ready yet" rather than an error.
 */
export function useApiResource<T>(
  path: string | null,
  options: { enabled?: boolean } = {},
): Resource<T> {
  const enabled = options.enabled ?? true;
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path) && enabled);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!path || !enabled) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    apiRequest<T>(path, { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) setData(result);
      })
      .catch((caught: unknown) => {
        if (controller.signal.aborted) return;
        if (caught instanceof DOMException && caught.name === "AbortError") return;
        setError(caught instanceof Error ? caught : new Error("请求未能完成。"));
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [path, enabled, tick]);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  return { data, error, loading, reload, setData };
}

/** Human copy for an API failure without exposing internals. */
export function errorText(error: ApiError | Error | null): string {
  if (!error) return "";
  if (error instanceof ApiError) {
    if (error.status === 401) return "登录状态已失效，请重新登录。";
    if (error.status === 404) return "这条记录在云端上已不存在。";
    return error.message;
  }
  return error.message || "请求未能完成。";
}
