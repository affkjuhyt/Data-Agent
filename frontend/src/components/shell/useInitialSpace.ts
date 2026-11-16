import { useCallback, useEffect, useRef } from 'react';

interface SpaceRef {
  id: string;
  name: string;
}

/**
 * The Data Space the shell opens on, applied once. The list of spaces is a v1 query whose key
 * carries the selected space, so every switch reloads it; re-applying the opening choice on each
 * reload took the viewer straight back to their default space (switching to NYC Taxi landed on
 * AdventureWorks again). Once a space is applied, or the viewer picks one, the list's reloads
 * change nothing. `markChosen` is for the viewer's own pick.
 */
export function useInitialSpace(
  candidateId: string,
  items: SpaceRef[] | undefined,
  apply: (space: SpaceRef) => void,
): { markChosen: () => void } {
  const settled = useRef(false);
  useEffect(() => {
    if (settled.current || !candidateId) return;
    const space = items?.find((s) => s.id === candidateId);
    if (space) {
      settled.current = true;
      apply({ id: space.id, name: space.name });
    }
  }, [candidateId, items, apply]);
  const markChosen = useCallback(() => {
    settled.current = true;
  }, []);
  return { markChosen };
}
