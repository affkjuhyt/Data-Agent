/**
 * Server-state cache for the V2 screens. Data is kept briefly so moving between
 * screens is instant, and goes stale fast: the notification inbox invalidates
 * the affected keys as soon as someone else changes them.
 */
import { QueryClient } from '@tanstack/react-query';
import { ApiError } from './errors';

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        // A 4xx (no permission, not found, bad link) will not change on retry.
        retry: (failures, error) =>
          !(error instanceof ApiError && error.status < 500) && failures < 2,
      },
    },
  });
}
