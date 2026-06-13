/**
 * Thin fetch wrapper for the AeroFlow KB FastAPI server.
 * Base URL from VITE_API_URL env var, defaults to http://localhost:8000 (FastAPI).
 *
 * Every request includes the current role, tenant_id, and user_id via headers.
 * These are used by the backend to validate permissions and scope queries to tenant.
 *
 * Responses from the FastAPI server are wrapped in { code, data, error }.
 * unwrapEnvelope() strips the envelope and returns data, or throws on error.
 */

const BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ??
  'http://localhost:8050';

let _role = 'AI_ENGINEER';
let _userId = 'dev-user';
let _tenantId = '00000000-0000-0000-0000-000000000001';

/** Call this whenever the app role/user context changes (e.g. from AppStateContext). */
export const setApiContext = (role: string, userId: string, tenantId: string) => {
  _role = role;
  _userId = userId;
  _tenantId = tenantId;
};

/** Legacy method for setting just the role */
export const setApiRole = (role: string) => {
  _role = role;
};

const contextHeaders = () => ({
  'X-Role': _role,
  'X-Tenant-Id': _tenantId,
  'X-User-Id': _userId,
});

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

export async function mockGet<T>(
  path: string,
  params?: Record<string, string | number | boolean>,
): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)));
  }
  const res = await fetch(url.toString(), {
    headers: contextHeaders(),
    signal: AbortSignal.timeout(5000),
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return unwrapEnvelope<T>(await res.json());
}

export async function mockMutate<T = unknown>(
  method: 'POST' | 'PUT' | 'PATCH' | 'DELETE',
  path: string,
  body?: unknown,
): Promise<T> {
  const headers: Record<string, string> = { ...contextHeaders() };
  if (body !== undefined) headers['Content-Type'] = 'application/json';

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
    throw new Error(msg);
  }
  return unwrapEnvelope<T>(await res.json());
}

/** Upload form data (for file uploads, multipart/form-data). */
export async function mockUpload<T = unknown>(
  path: string,
  formData: FormData,
): Promise<T> {
  const headers: Record<string, string> = { ...contextHeaders() };
  // Don't set Content-Type — browser will set it with boundary

  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers,
    body: formData,
    signal: AbortSignal.timeout(10000), // Longer timeout for uploads
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
