import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, isAbortError } from "../api/http";

interface QueryState<T> {
  data: T | null;
  loading: boolean;
  error: ApiError | null;
}

/**
 * GET 系 API の読み込み状態を管理する。
 * - deps が変わると前のリクエストを中断して取り直す
 * - 読み込み中も直前のデータを残す（画面がちらつかない）
 * - enabled=false の間は何もしない
 */
export function useApiQuery<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { enabled?: boolean; keepDataOnError?: (e: ApiError) => boolean } = {},
) {
  const { enabled = true } = options;
  const [state, setState] = useState<QueryState<T>>({ data: null, loading: enabled, error: null });
  const [reloadKey, setReloadKey] = useState(0);
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const keepRef = useRef(options.keepDataOnError);
  keepRef.current = options.keepDataOnError;

  useEffect(() => {
    if (!enabled) {
      setState({ data: null, loading: false, error: null });
      return;
    }
    const controller = new AbortController();
    setState((s) => ({ ...s, loading: true, error: null }));
    fetcherRef
      .current(controller.signal)
      .then((data) => setState({ data, loading: false, error: null }))
      .catch((e: unknown) => {
        if (isAbortError(e)) return;
        const error = e instanceof ApiError ? e : new ApiError(String(e), 0, "UNKNOWN");
        const keep = keepRef.current ? keepRef.current(error) : true;
        setState((s) => ({ data: keep ? s.data : null, loading: false, error }));
      });
    return () => controller.abort();
  }, [enabled, reloadKey, ...deps]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return { ...state, reload };
}
