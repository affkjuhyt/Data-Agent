import { useState, useEffect } from 'react';
import { apiGet } from './apiClient';

/**
 * Fetches a resource from the backend (through Kong) at /api/data/<resource>.
 * Falls back to `fallback` if the request is unreachable or times out.
 *
 * Usage:
 *   const { data: agents, loading } = useWsData<Agent>('agents', fallbackAgents);
 */
export function useWsData<T>(resource: string, fallback: T[] = []): { data: T[]; loading: boolean } {
  const [data, setData] = useState<T[]>(fallback);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    apiGet<T[]>(`/api/data/${resource}`).then((result) => {
      if (!cancelled && result && result.length > 0) {
        setData(result);
      }
    }).catch(() => {
      // server offline — keep fallback data
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [resource]);

  return { data, loading };
}
