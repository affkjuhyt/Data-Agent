/**
 * Thin client for indicator-api, the V2 contract API. Types come from
 * `contracts.d.ts` (generated, `npm run gen:api`); no V2 DTO is written by
 * hand. Auth is the same bearer token as every other call (apiClient) — the
 * identity and tenant are the gateway's job, not the client's.
 *
 * Routes are the contract mock's (tests/contract_mock), which indicator-api
 * serves under the same paths.
 */
import { authHeaders } from '../apiClient';
import { INDICATOR_API_URL } from '../config';
import type { components } from './contracts';
import { ApiError } from './errors';

type Schemas = components['schemas'];
export type ReviewCaseRef = Schemas['ReviewCaseRef'];
type SeriesRef = Schemas['SeriesRef'];

async function indicatorFetch<T>(
  path: string,
  init: { method?: 'GET' | 'POST'; body?: unknown } = {},
): Promise<T> {
  if (!INDICATOR_API_URL) throw new Error('VITE_INDICATOR_API_URL is not set');
  const method = init.method ?? 'GET';
  const headers: Record<string, string> = { ...authHeaders() };
  if (init.body !== undefined) headers['Content-Type'] = 'application/json';

  const res = await fetch(`${INDICATOR_API_URL}${path}`, {
    method,
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    // Same budgets as apiGet / apiMutate.
    signal: AbortSignal.timeout(method === 'GET' ? 5000 : 3000),
  });
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      /* no JSON body */
    }
    throw new ApiError(res.status, res.statusText, body);
  }
  return (await res.json()) as T;
}

export const getSeries = (seriesKey: string) =>
  indicatorFetch<SeriesRef>(`/series/${encodeURIComponent(seriesKey)}`);

export const getReviewCase = (caseId: string) =>
  indicatorFetch<ReviewCaseRef>(`/review/cases/${encodeURIComponent(caseId)}`);

/** Decide on the version the user saw; a newer one answers 409 VERSION_CONFLICT. */
export const decideReviewCase = (
  caseId: string,
  decision: 'approve' | 'reject',
  expectedVersion: number,
) =>
  indicatorFetch<ReviewCaseRef>(`/review/cases/${encodeURIComponent(caseId)}/${decision}`, {
    method: 'POST',
    body: { expected_version: expectedVersion } satisfies Schemas['VersionedWrite'],
  });
