import { keepPreviousData, skipToken, useQuery, type QueryKey } from '@tanstack/react-query';
import { useCallback } from 'react';
import { currentDataSpace, V1Error } from './client';
import { queryClient } from './queryClient';

/*
 * Load one v1 resource for a screen, through TanStack Query. `key` names what is loaded — the
 * loader's function, then every value it reads — so two screens asking for the same thing share
 * one request and one cache entry, and a screen opened again draws the last answer while it
 * refetches. `data` stays from the last success while reloading, so a refresh does not blank the
 * region; `error` is the problem+json of the last failure. Pass null as the loader to wait (e.g.
 * until an id is known).
 *
 * The Data Space goes into every key: X-Data-Space is sent from module state (client.ts), so an
 * answer read in one space must never be drawn in another.
 */
export interface V1State<T> {
  data: T | undefined;
  error: V1Error | Error | undefined;
  loading: boolean;
  reload: () => void;
}

interface V1Options {
  /** false drops the answer as soon as no screen shows it — for what is decrypted on this device. */
  cache?: boolean;
}

export function useV1<T>(
  key: QueryKey,
  load: (() => Promise<T>) | null,
  options: V1Options = {},
): V1State<T> {
  const query = useQuery(
    {
      queryKey: ['v1', currentDataSpace(), ...key],
      queryFn: load ?? skipToken,
      placeholderData: keepPreviousData,
      ...(options.cache === false ? { gcTime: 0 } : {}),
    },
    queryClient,
  );
  const { refetch } = query;
  const waiting = load === null;
  const reload = useCallback(() => {
    if (!waiting) void refetch();
  }, [refetch, waiting]);
  return {
    data: query.data,
    error: query.error ?? undefined,
    loading: query.isFetching,
    reload,
  };
}
