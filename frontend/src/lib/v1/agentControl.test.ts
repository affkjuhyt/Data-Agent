import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { isLocked, lockAgent, unlockAgent } from './agentControl';
import type { AgentDef } from './types';
import { setDataSpace } from './client';

const server = setupServer();

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  setDataSpace(null);
});
afterAll(() => server.close());

describe('agent control API', () => {
  it('sends the required reason when locking an agent', async () => {
    let body: unknown;
    server.use(
      http.post('*/v1/agents/agent-1:lock', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ id: 'agent-1' });
      }),
    );

    await lockAgent('agent-1');

    expect(body).toEqual({ reason: 'Khoá theo yêu cầu của người dùng.' });
  });

  it('does not send a body when unlocking an agent', async () => {
    let body: unknown = 'not-read';
    server.use(
      http.post('*/v1/agents/agent-1:unlock', async ({ request }) => {
        body = await request.text();
        return HttpResponse.json({ id: 'agent-1' });
      }),
    );

    await unlockAgent('agent-1');

    expect(body).toBe('');
  });

  it('reads a locked agent as the api marks it, not a pause with a reason', () => {
    const agent = (status: string, paused_reason: string | null) =>
      ({ status, paused_reason }) as AgentDef;
    expect(isLocked(agent('locked', 'Sai số liệu nợ xấu'))).toBe(true);
    expect(isLocked(agent('paused', 'locked'))).toBe(false);
  });
});
