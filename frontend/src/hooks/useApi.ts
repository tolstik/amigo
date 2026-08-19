import { useCallback, useEffect, useRef, useState } from "react";

export interface ApiState<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  refreshing: boolean;
  reload: () => void;
}

export function useApi<T>(loader: (signal: AbortSignal) => Promise<T>): ApiState<T> {
  const activeLoaderRef = useRef(loader);
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<Omit<ApiState<T>, "reload">>({
    data: null,
    error: null,
    loading: true,
    refreshing: false,
  });

  useEffect(() => {
    const controller = new AbortController();
    const loaderChanged = activeLoaderRef.current !== loader;
    activeLoaderRef.current = loader;
    setState((previous) => ({
      data: loaderChanged ? null : previous.data,
      error: null,
      loading: loaderChanged || previous.data === null,
      refreshing: !loaderChanged && previous.data !== null,
    }));
    loader(controller.signal)
      .then((data) => setState({ data, error: null, loading: false, refreshing: false }))
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const normalized = error instanceof Error ? error : new Error("Не удалось получить данные");
        setState((previous) => ({ ...previous, error: normalized, loading: false, refreshing: false }));
      });
    return () => controller.abort();
  }, [revision, loader]);

  const reload = useCallback(() => setRevision((value) => value + 1), []);
  return { ...state, reload };
}
