import { QueryClient } from '@tanstack/react-query';
import { V1Error } from './client';

/*
 * The one cache of v1 answers. A 4xx is the answer (refused, not found, invalid), so only a
 * network failure or a 5xx is tried once more. Answers are refetched whenever a screen mounts —
 * writes elsewhere do not invalidate them yet — and not on window focus, which would re-run
 * expensive queries (explore, previews) each time the user comes back to the tab.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failures, error) =>
        failures < 1 && !(error instanceof V1Error && error.status >= 400 && error.status < 500),
      refetchOnWindowFocus: false,
    },
  },
});
