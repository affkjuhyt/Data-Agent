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
  resolveHitl,
} from './api';
import { V1Error } from './client';

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
