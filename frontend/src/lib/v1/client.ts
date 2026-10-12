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
/**
 * PROPOSAL, for "v3.1 · Trạng thái · Thiếu quyền" (2076:5267): a 403 on a read says what was
 * refused, whose it is, and which access levels may be asked for — never the data itself.
 */
export interface ForbiddenAccess {
  title: string;
  detail: string;
  data_space_id: string;
  owner_name: string;
  options: { capability: 'view_aggregate' | 'view_detail' | 'ask' | 'run_agent'; label: string }[];
  /** The context the request came from, to prefill its reason. */
  reason_hint: string;
  /** What the agent can still answer meanwhile, or null. */
  fallback_text: string | null;
}

export class V1Error extends Error {
  readonly status: number;
  readonly code: string;
  readonly traceId?: string;
  readonly access?: ForbiddenAccess;

  constructor(
    status: number,
    code: string,
    detail: string,
    traceId?: string,
    access?: ForbiddenAccess,
  ) {
    super(detail);
    this.name = 'V1Error';
    this.status = status;
    this.code = code;
    this.traceId = traceId;
    this.access = access;
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
 * the request is built. A blacklist of "..", %2e and backslash was not enough — the URL parser
 * strips tabs and newlines (".\t." becomes ".."), and a server that decodes twice turns %252e
 * into a dot — so a path is accepted only when every segment is a plain name, optionally with a
 * ":verb" action (A1), the same alphabet as pathId(). Queries go in `query`.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9_-]{1,128}(:[a-z][a-z-]{0,63})?$/;

function safePath(path: string): string {
  const segments = path.split('/');
  if (
    segments[0] !== '' ||
    segments.length < 2 ||
    !segments.slice(1).every((s) => SAFE_SEGMENT.test(s))
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
      res.status === 403 ? body.access : undefined,
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

/** POST multipart/form-data (a file upload); the browser sets the boundary, so no Content-Type here. */
export async function v1Upload<T = unknown>(path: string, form: FormData): Promise<T> {
  const res = await fetch(`${BASE}${safePath(path)}`, {
    method: 'POST',
    headers: headers({ 'Idempotency-Key': crypto.randomUUID() }),
    body: form,
    signal: AbortSignal.timeout(60_000),
  });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as T;
}

/** One Server-Sent Event; `id` is what Last-Event-ID resumes after (A1.6). */
export interface V1Event {
  id?: string;
  event: string;
  data: string;
}

/**
 * GET a short Server-Sent Events stream and return its events once it ends — for streams that
 * send the state recorded so far and close (A1.6: questions, P16 Hỏi riêng). Polling it again
 * with the last id returns only what is new.
 */
export async function v1Events(
  path: string,
  options: { lastEventId?: string; signal?: AbortSignal } = {},
): Promise<V1Event[]> {
  const extra: Record<string, string> = { Accept: 'text/event-stream' };
  if (options.lastEventId) extra['Last-Event-ID'] = options.lastEventId;
  const res = await fetch(`${BASE}${safePath(path)}`, {
    headers: headers(extra),
    signal: options.signal ?? AbortSignal.timeout(60_000),
  });
  if (!res.ok) throw await failure(res);
  return (await res.text())
    .split(/\r?\n\r?\n/)
    .filter((block) => block.trim())
    .map((block) => {
      const lines = block.split(/\r?\n/);
      const field = (name: string) =>
        lines
          .filter((l) => l.startsWith(`${name}:`))
          .map((l) => l.slice(name.length + 1).trimStart());
      return {
        id: field('id')[0],
        event: field('event')[0] ?? 'message',
        data: field('data').join('\n'),
      };
    });
}
