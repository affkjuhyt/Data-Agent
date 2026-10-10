import keycloak from '../keycloak';

/*
 * Client of the v1 API of the system design ("Đặc tả API", A1): https://{host}/v1, JSON in
 * snake_case, bearer token from Keycloak, X-Data-Space for resources that belong to a Data
 * Space, cursor lists {items, next_page_token}, errors as application/problem+json (RFC 9457).
 *
 * In development `VITE_MOCK_V1=1` answers these calls from src/mocks (MSW) until the backend
 * serves them; nothing here knows about the mock.
 */
const BASE = `${(import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8100'}/v1`;

/** A1.4: one page of a list. An empty next_page_token means the last page. */
export interface Page<T> {
  items: T[];
  next_page_token: string;
}

/** A1.7: the problem+json body, kept whole so a screen can branch on `code`. */
export class V1Error extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId?: string;

  constructor(status: number, code: string, detail: string, traceId?: string) {
    super(detail);
    this.name = 'V1Error';
    this.status = status;
    this.code = code;
    this.traceId = traceId;
  }
}

let dataSpace: string | null = null;

/** The Data Space the user works in (top bar switcher); null = the whole organisation. */
export function setDataSpace(id: string | null) {
  dataSpace = id;
}

type Query = Record<string, string | number | boolean | undefined | null>;

function headers(extra?: Record<string, string>): Record<string, string> {
  return {
    Accept: 'application/json',
    ...(keycloak.token ? { Authorization: `Bearer ${keycloak.token}` } : {}),
    ...(dataSpace ? { 'X-Data-Space': dataSpace } : {}),
    ...extra,
  };
}

async function failure(res: Response): Promise<V1Error> {
  try {
    const body = await res.json();
    return new V1Error(
      res.status,
      body.code ?? 'unknown',
      body.detail ?? body.title ?? res.statusText,
      body.trace_id,
    );
  } catch {
    return new V1Error(res.status, 'unknown', res.statusText);
  }
}

export async function v1Get<T>(path: string, query?: Query): Promise<T> {
  const url = new URL(`${BASE}${path}`);
  Object.entries(query ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '')
      url.searchParams.set(key, String(value));
  });
  const res = await fetch(url, { headers: headers(), signal: AbortSignal.timeout(15_000) });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

/** POST/PATCH/PUT/DELETE. `ifMatch` carries the ETag read before (A1.5); POST gets an idempotency key. */
export async function v1Send<T = unknown>(
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
  path: string,
  body?: unknown,
  options: { ifMatch?: string } = {},
): Promise<T> {
  const extra: Record<string, string> = {};
  if (body !== undefined) extra['Content-Type'] = 'application/json';
  if (options.ifMatch) extra['If-Match'] = options.ifMatch;
  if (method === 'POST') extra['Idempotency-Key'] = crypto.randomUUID();
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: headers(extra),
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(15_000),
  });
  if (!res.ok) {
    const error = await failure(res);
    if (res.status === 403)
      window.dispatchEvent(new CustomEvent('app:forbidden', { detail: error.message }));
    throw error;
  }
  if (res.status === 202 || res.status === 204)
    return (res.status === 204 ? undefined : await res.json()) as T;
  return (await res.json()) as T;
}
