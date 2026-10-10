/**
 * HTTP client for the AeroFlow KB backend.
 *
 * Every KB request goes through this module, and from here through the Kong
 * gateway (VITE_API_URL). Kong validates the Keycloak JWT and injects the
 * verified identity headers the backend trusts (X-User-Id / X-Tenant-Id /
 * X-User-Roles / X-Role-Id) — this client only attaches the bearer token.
 *
 * There is no mock / dev-bypass path: with no token the request is sent without
 * Authorization and Kong rejects it (401). Real auth, real gateway, always.
 *
 * Responses are wrapped in { code, data, error }; unwrapEnvelope() strips it.
 */

import keycloak from './keycloak';

const BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  'http://localhost:8100'; // Kong gateway

/** Bearer-only auth header. Kong injects the authoritative X-* identity. */
const authHeaders = (): Record<string, string> =>
  keycloak.token ? { Authorization: `Bearer ${keycloak.token}` } : {};

/** Unwraps FastAPI ResponseModel envelope { code, data, error }. Raw responses pass through. */
function unwrapEnvelope<T>(json: unknown): T {
  if (
    typeof json === 'object' &&
    json !== null &&
    'code' in json &&
    'data' in json
  ) {
    const envelope = json as { code: number; data: unknown; error: { message: string; error_type: string } | null };
    if (envelope.error) throw new Error(envelope.error.message);
    if (envelope.code >= 400) throw new Error(`Server error ${envelope.code}`);
    return envelope.data as T;
  }
  return json as T;
}

export async function apiGet<T>(
  path: string,
  params?: Record<string, string | number | boolean>,
): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString(), {
    headers: authHeaders(),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return unwrapEnvelope<T>(await res.json());
}

/** Thrown when the row changed since it was read (HTTP 412). */
class StaleVersionError extends Error {
  constructor(message = 'This item was changed by someone else. Reload and try again.') {
    super(message);
    this.name = 'StaleVersionError';
  }
}
