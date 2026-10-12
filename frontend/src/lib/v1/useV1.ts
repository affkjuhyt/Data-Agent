import { useCallback, useEffect, useRef, useState } from 'react';
import { V1Error } from './client';

/*
 * Load one v1 resource for a screen. `data` stays from the last success while reloading,
 * so a refresh does not blank the region; `error` is the problem+json of the last failure.
 * Pass null as the loader to wait (e.g. until an id is known).
 */
export interface V1State<T> {
  data: T | undefined;
  error: V1Error | Error | undefined;
  loading: boolean;
  reload: () => void;
}

export function useV1<T>(load: (() => Promise<T>) | null, deps: unknown[]): V1State<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<V1Error | Error | undefined>(undefined);
  const [loading, setLoading] = useState(load !== null);
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const run = loadRef.current;
    if (!run) return;
    let alive = true;
    setLoading(true);
    run()
      .then((result) => {
        if (!alive) return;
        setData(result);
        setError(undefined);
      })
      .catch((e: unknown) => alive && setError(e instanceof Error ? e : new Error(String(e))))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
    // deps are the caller's: the loader closes over them.
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload };
}
