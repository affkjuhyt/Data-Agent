import createClient, { type Middleware } from 'openapi-fetch';
import keycloak from '../keycloak';
import type { paths } from './schema';

/*
 * Client of the v1 API of the system design ("Đặc tả API", A1): https://{host}/v1, JSON in
 * snake_case, bearer token from Keycloak, X-Data-Space for resources that belong to a Data
 * Space, cursor lists {items, next_page_token}, errors as application/problem+json (RFC 9457).
 *
 * Every request goes through one openapi-fetch client typed by schema.ts (`make api-types`):
 * `api.GET('/v1/…/{id}', …)` checks the path, its parameters and the body against the api's
 * OpenAPI, and the middleware below adds the headers and turns a failure into a V1Error, so a
 * call either resolves with the body or rejects. v1Get/v1Send are the same client for the
 * modules not yet written against the typed paths.
 *
 * In development `VITE_MOCK_V1=1` answers these calls from src/mocks (MSW) until the backend
 * serves them; nothing here knows about the mock.
 */
const ORIGIN = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:30100';
/** The path every v1 request starts with, behind a gateway that may mount the api under a prefix. */
const ROOT = `${new URL(ORIGIN).pathname.replace(/\/$/, '')}/v1`;

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

/** The Data Space set above, for the bodies that carry data_space_id as well as the header. */
export const currentDataSpace = () => dataSpace;

type Query = Record<string, string | number | boolean | undefined | null>;

/** An empty value means "not filtered", so it is left out rather than sent as `key=`. */
function serializeQuery(query: Record<string, unknown>): string {
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  });
  return params.toString();
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

const v1Middleware: Middleware = {
  onRequest({ request }) {
    const { pathname } = new URL(request.url);
    if (!pathname.startsWith(`${ROOT}/`))
      throw new V1Error(400, 'invalid_request', 'Đường dẫn API không hợp lệ.');
    safePath(pathname.slice(ROOT.length));
    const headers = request.headers;
    if (!headers.has('Accept')) headers.set('Accept', 'application/json');
    if (keycloak.token) headers.set('Authorization', `Bearer ${keycloak.token}`);
    if (dataSpace && !headers.has('X-Data-Space')) headers.set('X-Data-Space', dataSpace);
    if (request.method === 'POST' && !headers.has('Idempotency-Key'))
      headers.set('Idempotency-Key', crypto.randomUUID());
    return request;
  },
  async onResponse({ request, response }) {
    if (response.ok) return undefined;
    const error = await failure(response);
    // A refused write is told once, app-wide (useForbiddenToast); a refused read is drawn by its screen.
    if (response.status === 403 && request.method !== 'GET')
      window.dispatchEvent(new CustomEvent('app:forbidden', { detail: error.message }));
    throw error;
  },
};

/*
 * A typed path's {id} is filled here, through pathId(): openapi-fetch would only percent-encode
 * it, which leaves "..", and new Request() resolves dot segments before the middleware sees the
 * URL — so the check has to happen while the value is still a value.
 */
function fillPath(pathname: string, params: Record<string, unknown>): string {
  return pathname.replace(/\{([^}]+)\}/g, (_, name: string) => pathId(String(params[name] ?? '')));
}

/*
 * A request is abandoned after 15 s, or 60 s for an upload, a download or a stream; the caller's
 * own signal (a closed screen) still aborts it sooner. The two are combined by hand because the
 * tests' jsdom AbortSignal has no any().
 */
function clientWithin(ms: number) {
  const client = createClient<paths>({
    baseUrl: ORIGIN,
    querySerializer: serializeQuery,
    pathSerializer: fillPath,
    // Looked up per request, not captured once: MSW replaces globalThis.fetch after this module loads.
    fetch: async (request) => {
      const deadline = new AbortController();
      const timer = setTimeout(
        () => deadline.abort(new DOMException('The request took too long.', 'TimeoutError')),
        ms,
      );
      const cancel = () => deadline.abort(request.signal.reason);
      if (request.signal.aborted) cancel();
      else request.signal.addEventListener('abort', cancel, { once: true });
      try {
        return await globalThis.fetch(request, { signal: deadline.signal });
      } finally {
        clearTimeout(timer);
        request.signal.removeEventListener('abort', cancel);
      }
    },
  });
  client.use(v1Middleware);
  return client;
}

export const api = clientWithin(15_000);
const slowApi = clientWithin(60_000);

/**
 * The body of a typed call. The middleware has already turned every failure into a V1Error, so
 * what is left is the success, which openapi-fetch still types as optional.
 */
export async function ok<T>(request: Promise<{ data?: T }>): Promise<T> {
  return (await request).data as T;
}

/**
 * The body of a typed call whose screen reads more than the api's schema says it answers — fields
 * marked PROPOSAL in types.ts, which only the mocks send so far. The path, parameters and body are
 * still checked; the answer is taken as T unchecked, as v1Get did. Each use is a known gap
 * between a screen and the api: when the api serves the fields, the call becomes ok().
 */
export async function okAs<T>(request: Promise<unknown>): Promise<T> {
  return ((await request) as { data?: unknown }).data as T;
}

type Method = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

/*
 * The untyped door into the same client, for paths built as strings. The casts are the price of
 * taking any path: openapi-fetch only accepts the literal paths of schema.ts.
 */
async function untyped<T>(
  method: Method,
  path: string,
  init: {
    query?: Query;
    body?: unknown;
    headers?: Record<string, string>;
    parseAs?: 'json' | 'text' | 'blob';
    signal?: AbortSignal;
    slow?: boolean;
  },
): Promise<T> {
  const { query, slow, ...rest } = init;
  const request = (slow ? slowApi : api).request as unknown as (
    method: Method,
    url: string,
    init: Record<string, unknown>,
  ) => Promise<{ data?: unknown }>;
  const { data } = await request(method, `/v1${safePath(path)}`, {
    ...rest,
    params: { query: query ?? {} },
  });
  return data as T;
}

export async function v1Get<T>(path: string, query?: Query): Promise<T> {
  return untyped<T>('GET', path, { query });
}

export async function v1GetBlob(path: string): Promise<string> {
  const blob = await untyped<Blob>('GET', path, {
    parseAs: 'blob',
    slow: true,
  });
  return URL.createObjectURL(blob);
}

/** POST/PATCH/PUT/DELETE. `ifMatch` carries the ETag read before (A1.5); POST gets an idempotency key. */
export async function v1Send<T = unknown>(
  method: 'POST' | 'PATCH' | 'PUT' | 'DELETE',
  path: string,
  body?: unknown,
  options: { ifMatch?: string; query?: Query } = {},
): Promise<T> {
  return untyped<T>(method, path, {
    body,
    query: options.query,
    headers: options.ifMatch ? { 'If-Match': options.ifMatch } : undefined,
  });
}

/** POST multipart/form-data (a file upload); the browser sets the boundary, so no Content-Type here. */
export async function v1Upload<T = unknown>(
  path: string,
  form: FormData,
  options: { fileName?: string } = {},
): Promise<T> {
  return untyped<T>('POST', path, {
    body: form,
    headers: options.fileName ? { 'X-Upload-Filename': options.fileName } : undefined,
    slow: true,
  });
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
  const headers: Record<string, string> = { Accept: 'text/event-stream' };
  if (options.lastEventId) headers['Last-Event-ID'] = options.lastEventId;
  const text = await untyped<string>('GET', path, {
    headers,
    parseAs: 'text',
    signal: options.signal,
    slow: true,
  });
  return (text ?? '')
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
