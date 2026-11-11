import {
  keepPreviousData,
  skipToken,
  useMutation,
  useQuery,
  type MutateOptions,
  type QueryKey,
} from '@tanstack/react-query';
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

interface V1Options<T> {
  /** false drops the answer as soon as no screen shows it — for what is decrypted on this device. */
  cache?: boolean;
  /**
   * Poll: ms between reads, or a function of the last answer (false stops). TanStack Query pauses
   * it while the tab is hidden and stops it with the screen, which a setInterval did not.
   */
  refetchInterval?: number | ((data: T | undefined) => number | false);
  /** false: a new key starts empty instead of showing the previous key's answer meanwhile. */
  keepPrevious?: boolean;
}

export function useV1<T>(
  key: QueryKey,
  load: (() => Promise<T>) | null,
  options: V1Options<T> = {},
): V1State<T> {
  const query = useQuery(
    {
      queryKey: ['v1', currentDataSpace(), ...key],
      queryFn: load ?? skipToken,
      ...(options.keepPrevious === false ? {} : { placeholderData: keepPreviousData }),
      ...(options.cache === false ? { gcTime: 0 } : {}),
      ...(options.refetchInterval === undefined
        ? {}
        : {
            refetchInterval: (q: { state: { data: T | undefined } }) =>
              typeof options.refetchInterval === 'function'
                ? options.refetchInterval(q.state.data)
                : (options.refetchInterval ?? false),
          }),
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

/*
 * Write through TanStack Query, on the same client as useV1. `pending` and `error` replace a
 * screen's own busy/error state (a new run clears the last error), and on success every v1 query
 * under each key prefix in `invalidates` is marked stale and refetched if a screen shows it, so a
 * screen no longer reloads by hand what it just changed. The prefixes are matched under the
 * current Data Space, like the keys useV1 builds.
 *
 * `run` keeps a failure in `error` and never rejects; `runAsync` rejects as well, for a screen
 * that awaits the answer. The invalidation is not awaited: `pending` ends with the write, as the
 * hand-written busy flags did, and the region refreshes behind it.
 */
interface V1Mutation<TData, TVars> {
  run: (vars: TVars, options?: MutateOptions<TData, Error, TVars>) => void;
  runAsync: (vars: TVars, options?: MutateOptions<TData, Error, TVars>) => Promise<TData>;
  pending: boolean;
  error: V1Error | Error | undefined;
  /** The answer of the last successful run. */
  data: TData | undefined;
  /** What the running (or last) run was called with — e.g. which row's button is busy. */
  variables: TVars | undefined;
  reset: () => void;
}

interface V1MutationOptions<TData, TVars> {
  /** v1 key prefixes (without 'v1' and the Data Space) to refetch after a success. */
  invalidates?: QueryKey[];
  onSuccess?: (data: TData, vars: TVars) => void;
  onError?: (error: Error, vars: TVars) => void;
}

export function invalidateV1(prefixes: QueryKey[]): void {
  for (const prefix of prefixes) {
    void queryClient.invalidateQueries({ queryKey: ['v1', currentDataSpace(), ...prefix] });
  }
}

export function useV1Mutation<TData, TVars = void>(
  fn: (vars: TVars) => Promise<TData>,
  options: V1MutationOptions<TData, TVars> = {},
): V1Mutation<TData, TVars> {
  const { invalidates, onSuccess, onError } = options;
  const mutation = useMutation<TData, Error, TVars>(
    {
      mutationFn: fn,
      onSuccess: (data, vars) => {
        if (invalidates) invalidateV1(invalidates);
        onSuccess?.(data, vars);
      },
      onError: (error, vars) => onError?.(error, vars),
    },
    queryClient,
  );
  return {
    run: mutation.mutate,
    runAsync: mutation.mutateAsync,
    pending: mutation.isPending,
    error: mutation.error ?? undefined,
    data: mutation.data,
    variables: mutation.variables,
    reset: mutation.reset,
  };
}
