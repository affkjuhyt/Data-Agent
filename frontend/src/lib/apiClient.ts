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

export async function apiMutate<T = unknown>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
  ifMatch?: string,
): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  if (ifMatch) headers['If-Match'] = `"${ifMatch}"`;

  const res = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(3000),
  });

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.error?.message) msg = j.error.message;
      else if (j?.detail) msg = j.detail;
      else if (j?.error) msg = j.error;
    } catch { /* ignore */ }
    if (res.status === 403) {
      window.dispatchEvent(new CustomEvent('app:forbidden', { detail: msg }));
    }
    if (res.status === 412) throw new StaleVersionError();
    throw new Error(msg);
  }
  if (res.status === 204 || res.headers.get('content-length') === '0') return undefined as T;
  return unwrapEnvelope<T>(await res.json());
}

/** Upload form data (multipart/form-data). */
export async function apiUpload<T = unknown>(
  path: string,
  formData: FormData,
): Promise<T> {
  const headers: Record<string, string> = { ...authHeaders() };
  // Don't set Content-Type — the browser sets it with the multipart boundary.

  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: formData,
    signal: AbortSignal.timeout(10000),
  });

  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      if (j?.error?.message) msg = j.error.message;
      else if (j?.detail) msg = j.detail;
      else if (j?.error) msg = j.error;
    } catch { /* ignore */ }
    if (res.status === 403) {
      window.dispatchEvent(new CustomEvent('app:forbidden', { detail: msg }));
    }
    throw new Error(msg);
  }
  return unwrapEnvelope<T>(await res.json());
}

/**
 * Fetch a binary resource (the original uploaded file, for preview/download)
 * as a Blob. A plain `<img src="...">`/`<iframe src="...">` can't carry the
 * bearer token — the browser doesn't attach custom headers for resource-
 * loading tags — so authenticated media has to go through `fetch()` here and
 * get handed to the DOM as an object URL (`URL.createObjectURL(blob)`,
 * revoked by the caller when no longer needed).
 */
export async function apiBlob(path: string): Promise<Blob> {
  const res = await fetch(`${BASE}${path}`, {
    headers: authHeaders(),
    signal: AbortSignal.timeout(30000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.blob();
}

/**
 * Streaming GET (Server-Sent Events / chunked) over the same gateway path:
 * attaches the bearer token and returns the raw Response so the caller can read
 * `response.body` as a stream. No JSON unwrap, no timeout — streams are
 * long-lived and the caller owns the lifecycle via the optional `signal`.
 */
export async function apiStream(
  path: string,
  init?: { signal?: AbortSignal },
): Promise<Response> {
  return fetch(`${BASE}${path}`, {
    headers: authHeaders(),
    signal: init?.signal,
  });
}
