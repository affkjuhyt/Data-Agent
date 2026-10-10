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

/*
 * Ids reach the API paths from the URL (/needs-you/proposal/<id>), so they are untrusted: an id
 * like "../../admin/users" (or "%2e%2e", which URL parsing also treats as "..") would send the
 * bearer token to another endpoint. Ids are UUIDv7 or business codes (A1.1), so anything other
 * than letters, digits, "-" and "_" is refused before a request is made.
 */
const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/;

export function pathId(id: string): string {
  if (!SAFE_ID.test(id))
    throw new V1Error(400, 'invalid_request', 'Đường dẫn chứa mã không hợp lệ.');
  return id;
}

/*
 * The second line of defence, for a module that forgets pathId(): every path is checked before
 * the request is built. It must be "/" followed by plain segments — no "." or ".." segment, no
 * encoded dot, slash or backslash, no backslash, query or fragment (queries go in `query`).
 */
const UNSAFE_PATH = /%2e|%2f|%5c|\\|[?#]/i;

function safePath(path: string): string {
  const segments = path.split('/').slice(1);
  if (
    !path.startsWith('/') ||
    UNSAFE_PATH.test(path) ||
    segments.some((segment) => segment === '' || segment === '.' || segment === '..')
  ) {
    throw new V1Error(400, 'invalid_request', 'Đường dẫn API không hợp lệ.');
  }
  return path;
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
  const url = new URL(`${BASE}${safePath(path)}`);
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
  const target = `${BASE}${safePath(path)}`;
  const extra: Record<string, string> = {};
  if (body !== undefined) extra['Content-Type'] = 'application/json';
  if (options.ifMatch) extra['If-Match'] = options.ifMatch;
  if (method === 'POST') extra['Idempotency-Key'] = crypto.randomUUID();
  const res = await fetch(target, {
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
