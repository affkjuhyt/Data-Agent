import { useEffect, useState } from 'react';

/*
 * "Mẫu tương tác": loading under 300 ms shows nothing, to avoid a flash; from 300 ms a skeleton.
 * Returns true only once `active` has stayed true for `delayMs`.
 */
export function useDelayedFlag(active: boolean, delayMs = 300): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), delayMs);
    return () => window.clearTimeout(timer);
  }, [active, delayMs]);
  return shown;
}
