"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { MatterSummary } from "./api";
import { ApiError } from "./api";
import { listMatters } from "./matters-api";

export interface MattersListState {
  items: MatterSummary[];
  loading: boolean;
  loadingMore: boolean;
  error: ApiError | Error | null;
  nextCursor: string | null;
  refresh: () => void;
  loadMore: () => void;
}

export function useMatters(enabled: boolean, accountId: string | null): MattersListState {
  const [items, setItems] = useState<MatterSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | Error | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [token, setToken] = useState(0);
  const seq = useRef(0);
  const controller = useRef<AbortController | null>(null);

  const load = useCallback(
    async (cursor: string | null, append: boolean) => {
      if (!enabled) return;
      controller.current?.abort();
      const current = new AbortController();
      controller.current = current;
      const requestId = ++seq.current;
      if (append) setLoadingMore(true);
      else {
        setLoading(true);
        setItems([]);
      }
      setError(null);
      try {
        const page = await listMatters("all", cursor, current.signal);
        if (requestId !== seq.current) return;
        setItems((existing) => (append ? [...existing, ...page.items] : page.items));
        setNextCursor(page.next_cursor);
      } catch (caught) {
        if (current.signal.aborted || requestId !== seq.current) return;
        setError(caught instanceof Error ? caught : new Error("读取事项失败"));
      } finally {
        if (requestId === seq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [enabled],
  );

  useEffect(() => {
    seq.current += 1;
    controller.current?.abort();
    setItems([]);
    setNextCursor(null);
    setError(null);
    if (!enabled) return;
    void load(null, false);
    return () => {
      seq.current += 1;
      controller.current?.abort();
    };
  }, [accountId, enabled, load, token]);

  return {
    items,
    loading,
    loadingMore,
    error,
    nextCursor,
    refresh: () => setToken((value) => value + 1),
    loadMore: () => {
      if (nextCursor && !loadingMore) void load(nextCursor, true);
    },
  };
}
