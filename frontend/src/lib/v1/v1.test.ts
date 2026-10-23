/**
 * The v1 client against the dev mock: every endpoint the screens call answers at the path of
 * "Đặc tả API" A2 with the shape of lib/v1/types, and errors arrive as problem+json (A1.7).
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import {
  listAgents,
  listDataSpaces,
  listDatasets,
  listInbox,
  listMetrics,
  listModels,
  listMyRules,
  listRuns,
  listSources,
  listTerms,
  listUsers,
  listWorkspaces,
  getProposal,
  readHitl,
  resolveHitl,
} from './api';
import { inboxFixture } from '../../mocks/fixtures';
import { V1Error, api, pathId, setDataSpace, v1Get, v1Send } from './client';

const server = setupServer(...createHandlers());
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers(...createHandlers()));
afterAll(() => server.close());

describe('v1 client', () => {
  it('lists every resource the screens read as a cursor page', async () => {
    for (const list of [
      listInbox,
      listAgents,
      listRuns,
      listWorkspaces,
      listSources,
      listDatasets,
      listMetrics,
      listTerms,
      listMyRules,
      listDataSpaces,
      listUsers,
      listModels,
    ]) {
      const page = await list();
      expect(page.next_page_token).toBe('');
      expect(page.items.length).toBeGreaterThan(0);
    }
  });

  it('filters runs by status', async () => {
    const page = await listRuns({ status: 'waiting_human' });
    expect(page.items.every((r) => r.status === 'waiting_human')).toBe(true);
  });

  it('resolves a request once, then answers 409 invalid_state', async () => {
    const before = await listInbox({ status: 'open' });
    const first = before.items[0];
    const resolved = await resolveHitl(first.id, {
      decision: 'answer',
      option: first.options[0]?.id,
    });
    expect(resolved.status).toBe('answered');
    const after = await listInbox({ status: 'open' });
    expect(after.items.map((r) => r.id)).not.toContain(first.id);

    const again = await resolveHitl(first.id, { decision: 'answer' }).catch((e) => e);
    expect(again).toBeInstanceOf(V1Error);
    expect(again).toMatchObject({ status: 409, code: 'invalid_state' });
  });
});

describe('ids from the URL', () => {
  it('refuses ids that would leave the resource path, without calling the API', async () => {
    for (const id of ['..', '../../admin/users', '%2e%2e', 'a/b', 'x?y=1', '']) {
      const error = await getProposal(id).catch((e) => e);
      expect(error, id).toBeInstanceOf(V1Error);
      expect(error).toMatchObject({ status: 400, code: 'invalid_request' });
    }
  });

  it('accepts UUIDs and codes', async () => {
    await expect(getProposal('pr-0318')).resolves.toMatchObject({ code: 'PR-0318' });
    expect(pathId('0192f1a2-7c3d-7e4f-8a9b-0c1d2e3f4a5b')).toBe(
      '0192f1a2-7c3d-7e4f-8a9b-0c1d2e3f4a5b',
    );
  });
});

describe('paths built without pathId', () => {
  it('are still refused when they would leave the resource', async () => {
    for (const path of [
      '/proposals/..',
      '/proposals/../admin/users',
      '/proposals/%2e%2e',
      '/a%2Fb',
      '/a\\b',
      '/a?x=1',
      '/a//b',
      'proposals',
      // The URL parser strips tab and newline, so these would become "..".
      '/proposals/.\t.',
      '/proposals/.\n./admin',
      '/proposals/%252e%252e',
      '/proposals/．．',
      '/a b',
      '/a:',
      '/',
    ]) {
      const error = await v1Get(path).catch((e) => e);
      expect(error, path).toMatchObject({ status: 400, code: 'invalid_request' });
      const sent = await v1Send('POST', path).catch((e) => e);
      expect(sent, path).toMatchObject({ status: 400, code: 'invalid_request' });
    }
  });

  it('let normal paths and :verb actions through', async () => {
    await expect(v1Get('/proposals/pr-0318')).resolves.toMatchObject({ code: 'PR-0318' });
    await expect(v1Send('POST', '/runs/run-427:pause')).resolves.toMatchObject({
      status: 'paused',
    });
    await expect(v1Get('/admin/users')).resolves.toHaveProperty('items');
  });
});

describe('typed paths', () => {
  it('fill {id} through pathId, so a value cannot leave the resource', async () => {
    const seen = vi.fn();
    server.events.on('request:start', seen);
    for (const id of ['..', '../admin/users', '%2e%2e', 'a/b']) {
      const error = await api
        .GET('/v1/proposals/{id}', { params: { path: { id } } })
        .catch((e: unknown) => e);
      expect(error, id).toMatchObject({ status: 400, code: 'invalid_request' });
    }
    server.events.removeListener('request:start', seen);
    expect(seen).not.toHaveBeenCalled();
  });

  it('send the token headers and resolve with the body', async () => {
    let headers: Headers | undefined;
    server.use(
      http.post('*/v1/runs/:id\\:pause', ({ request }) => {
        headers = request.headers;
        return HttpResponse.json({ id: 'run-1', status: 'paused' });
      }),
    );
    setDataSpace('ds-1');
    try {
      const { data } = await api.POST('/v1/runs/{id}:pause', { params: { path: { id: 'run-1' } } });
      expect(data).toMatchObject({ status: 'paused' });
    } finally {
      setDataSpace(null);
    }
    expect(headers?.get('X-Data-Space')).toBe('ds-1');
    expect(headers?.get('Idempotency-Key')).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('reject with the problem+json, and tell the app only about a refused write', async () => {
    const problem = { code: 'forbidden', detail: 'Bạn không có quyền.', trace_id: 't-1' };
    server.use(
      http.get('*/v1/proposals/:id', () => HttpResponse.json(problem, { status: 403 })),
      http.post('*/v1/runs/:id\\:pause', () => HttpResponse.json(problem, { status: 403 })),
    );
    const told = vi.fn();
    window.addEventListener('app:forbidden', told);
    try {
      const read = await api
        .GET('/v1/proposals/{id}', { params: { path: { id: 'pr-1' } } })
        .catch((e: unknown) => e);
      expect(read).toBeInstanceOf(V1Error);
      expect(read).toMatchObject({ status: 403, code: 'forbidden', traceId: 't-1' });
      expect(told).not.toHaveBeenCalled();
      await api.POST('/v1/runs/{id}:pause', { params: { path: { id: 'run-1' } } }).catch(() => {});
      expect(told).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener('app:forbidden', told);
    }
  });
});

describe('readHitl', () => {
  it('keeps the options that have an id and drops the rest of the jsonb', () => {
    const raw = {
      ...inboxFixture()[0],
      context: ['not', 'an', 'object'],
      options: [
        'Có',
        { id: 'b', label: 'Không', consequence: 'dừng lượt' },
        { label: 'thiếu id' },
        3,
      ],
      agent: undefined,
    };
    const request = readHitl(raw);
    expect(request.context).toEqual({});
    expect(request.agent).toBeNull();
    expect(request.options).toEqual([
      { id: 'Có', label: 'Có', consequence: '' },
      { id: 'b', label: 'Không', consequence: 'dừng lượt', is_default: false },
    ]);
  });
});
