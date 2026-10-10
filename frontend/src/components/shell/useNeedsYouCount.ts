import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/apiClient';

/*
 * The live "Cần bạn" count of the top bar. The spec wants it pushed by events
 * (hitl.requested / hitl.resolved); until the event stream reaches the browser it polls the
 * pending-review queue. undefined = unknown, and the badge is hidden rather than showing 0.
 */
const POLL_MS = 30_000;

export function useNeedsYouCount(): number | undefined {
  const [count, setCount] = useState<number | undefined>(undefined);
  useEffect(() => {
    let cancelled = false;
    const load = () =>
      apiGet<unknown[]>('/api/reviews/pending')
        .then((list) => !cancelled && setCount(Array.isArray(list) ? list.length : undefined))
        .catch(() => !cancelled && setCount(undefined));
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);
  return count;
}
