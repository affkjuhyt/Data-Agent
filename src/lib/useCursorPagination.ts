import { useCallback, useEffect, useRef, useState } from 'react';
import { apiGet } from './apiClient';
import { PagedResponse } from '../types';

const PAGE_SIZE = 50;

export interface CursorPagination<T> {
  items: T[];
  page: number;
  total: number;
  totalPages: number;
  hasNext: boolean;
  isLoading: boolean;
  /** A page is reachable iff we hold the cursor that starts it (page 1, any visited
   *  page, or the immediate next page). Keyset can't teleport to an unvisited deep page. */
  canJump: (page: number) => boolean;
  goTo: (page: number) => void;
  next: () => void;
  prev: () => void;
  /** Re-fetch the current page (e.g. after a mutation). `silent` keeps the spinner off. */
  reload: (silent?: boolean) => void;
}

/**
 * Stateless cursor pagination, client-held.
 *
 * The server hands back an opaque `next_cursor` (the keyset boundary) with each page;
 * we cache, per page number, the cursor token that *starts* that page. Page 1 needs no
 * cursor. Navigation (next/prev/first/visited) replays the cached token → every hop is
 * an O(limit) keyset query, no offset. `total` (a cheap COUNT) drives the page numbers.
 *
 * `params` carries server-side filters (e.g. { tier: 'gold' }) — NOT the cursor.
 * Changing `deps` resets to page 1 and clears the cache.
 */
export function useCursorPagination<T>(
  path: string,
  params: Record<string, string | number | boolean>,
  deps: unknown[] = [],
): CursorPagination<T> {
  const [items, setItems] = useState<T[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  // pageNumber → cursor token that fetches that page (page 1 → null).
  const cursors = useRef<Map<number, string | null>>(new Map([[1, null]]));
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const fetchPage = useCallback(async (target: number, silent = false) => {
    const token = cursors.current.get(target);
    if (token === undefined) return; // not reachable via keyset — guarded by canJump
    if (!silent) setIsLoading(true);
    try {
      const q: Record<string, string | number | boolean> = { ...paramsRef.current };
      if (token) q.cursor = token;
      const res = await apiGet<PagedResponse<T>>(path, q);
      setItems(res.items ?? []);
      setHasNext(res.has_next ?? false);
      if (typeof res.total === 'number') setTotal(res.total);
      // Cache the cursor that starts the NEXT page (or mark there is none).
      if (res.has_next && res.next_cursor) cursors.current.set(target + 1, res.next_cursor);
      else cursors.current.delete(target + 1);
      setPage(target);
    } catch {
      setItems([]);
      setHasNext(false);
    } finally {
      if (!silent) setIsLoading(false);
    }
  }, [path]);

  // Reset + load page 1 whenever the filters change.
  useEffect(() => {
    cursors.current = new Map([[1, null]]);
    setPage(1);
    fetchPage(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  const canJump = useCallback(
    (target: number) => target >= 1 && cursors.current.has(target),
    [],
  );
  const goTo = useCallback((target: number) => { if (cursors.current.has(target)) fetchPage(target); }, [fetchPage]);
  const next = useCallback(() => { if (hasNext) fetchPage(page + 1); }, [hasNext, page, fetchPage]);
  const prev = useCallback(() => { if (page > 1) fetchPage(page - 1); }, [page, fetchPage]);
  const reload = useCallback((silent = false) => fetchPage(page, silent), [page, fetchPage]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return { items, page, total, totalPages, hasNext, isLoading, canJump, goTo, next, prev, reload };
}
