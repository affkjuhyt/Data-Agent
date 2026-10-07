/**
 * A write guarded by optimistic concurrency (contract `VersionedWrite`). When
 * the server answers 409 VERSION_CONFLICT, someone else saved first: the hook
 * loads the record as it is now, puts it in the cache (so the screen shows it)
 * and reports what changed. It never re-sends with the new version — the user
 * decides again on what they now see, nothing is silently overwritten.
 */
import { useMutation, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { useState } from 'react';
import { isVersionConflict } from './errors';

export interface FieldChange {
  field: string;
  before: unknown;
  after: unknown;
}

interface VersionConflict<T> {
  /** what the screen showed when the user saved */
  previous: T | undefined;
  /** the record as it is now, already in the cache */
  latest: T;
  changes: FieldChange[];
}

/** Top-level fields whose value differs between two versions of a record. */
export function diffFields(before: unknown, after: unknown): FieldChange[] {
  const a = (before && typeof before === 'object' ? before : {}) as Record<string, unknown>;
  const b = (after && typeof after === 'object' ? after : {}) as Record<string, unknown>;
  const fields = [...new Set([...Object.keys(a), ...Object.keys(b)])];
  return fields
    .filter((f) => JSON.stringify(a[f]) !== JSON.stringify(b[f]))
    .map((f) => ({ field: f, before: a[f], after: b[f] }));
}

interface Options<TData, TVars> {
  /** the cached query holding the record being written */
  queryKey: QueryKey;
  /** reads the record again after a conflict */
  queryFn: () => Promise<TData>;
  mutationFn: (vars: TVars) => Promise<TData>;
}

export function useVersionedMutation<TData, TVars>({
  queryKey,
  queryFn,
  mutationFn,
}: Options<TData, TVars>) {
  const queryClient = useQueryClient();
  const [conflict, setConflict] = useState<VersionConflict<TData> | null>(null);

  const mutation = useMutation<TData, Error, TVars>({
    mutationFn,
    onMutate: () => setConflict(null),
    onSuccess: (data) => queryClient.setQueryData(queryKey, data),
    onError: async (error) => {
      if (!isVersionConflict(error)) return;
      const previous = queryClient.getQueryData<TData>(queryKey);
      const latest = await queryClient.fetchQuery({ queryKey, queryFn, staleTime: 0 });
      setConflict({ previous, latest, changes: diffFields(previous, latest) });
    },
  });

  return { ...mutation, conflict, dismissConflict: () => setConflict(null) };
}
