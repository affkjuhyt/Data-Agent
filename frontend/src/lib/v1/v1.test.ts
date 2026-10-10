/**
 * The v1 client against the dev mock: every endpoint the screens call answers at the path of
 * "Đặc tả API" A2 with the shape of lib/v1/types, and errors arrive as problem+json (A1.7).
 */
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
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
  resolveHitl,
} from './api';
import { V1Error, pathId, v1Get, v1Send } from './client';

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
  });
});
