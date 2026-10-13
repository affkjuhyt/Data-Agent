/**
 * Tích hợp as the api sends it, without the PROPOSAL display fields: a client row, its usage
 * summed into local days and tools, and the owner's confirmation with If-Match.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  confirmMyClient,
  getIntegrationUsage,
  listIntegrations,
  type MyClient,
} from './adminPlatform';

const soon = new Date(Date.now() + 10 * 86_400_000).toISOString();
const client: MyClient = {
  id: 'c-1',
  name: 'Copilot',
  kind: 'rest',
  identity_mode: 'on_behalf_of_user',
  owner_user_id: 'u-1',
  keycloak_client_id: 'da-c-1',
  tools: ['ask_question'],
  data_space_ids: ['ds-1'],
  quota_per_minute: 60,
  quota_per_day: 1000,
  expires_at: soon,
  status: 'active',
  owner_confirmed_at: null,
  secret_rotated_at: null,
  revoked_at: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-12T03:15:00.5Z',
};
const today = new Date();
const atDay = (back: number, hour: number) =>
  new Date(today.getFullYear(), today.getMonth(), today.getDate() - back, hour).toISOString();
let usageQuery: URLSearchParams | undefined;
let ifMatch: string | null = null;

const server = setupServer(
  http.get('*/v1/admin/clients', () => HttpResponse.json({ items: [client], next_page_token: '' })),
  http.get('*/v1/admin/users', () =>
    HttpResponse.json({
      items: [{ id: 'u-1', username: 'yen', display_name: 'Đỗ Hải Yến', kind: 'human', status: 'active', last_synced_at: null }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/admin/clients/:id/usage', ({ request }) => {
    usageQuery = new URL(request.url).searchParams;
    const row = (bucket: string, tool: string, calls: number, blocked = 0) => ({
      bucket,
      tool,
      data_space_id: 'ds-1',
      calls,
      errors: 0,
      blocked_by_permission: blocked,
      blocked_by_quota: 0,
      p95_ms: null,
    });
    return HttpResponse.json({
      items: [
        row(atDay(6, 9), 'search_metrics', 10),
        row(atDay(0, 8), 'ask_question', 300, 4),
        row(atDay(0, 15), 'ask_question', 100),
        row(atDay(0, 15), 'search_metrics', 100),
      ],
      next_page_token: '',
    });
  }),
  http.post(/\/v1\/me\/clients\/[^/:]+:confirm$/, ({ request }) => {
    ifMatch = request.headers.get('If-Match');
    return HttpResponse.json({ ...client, status: 'active' });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('integration clients', () => {
  it('reads ApiClientView: owner by name, rest as API, near expiry as expiring', async () => {
    const page = await listIntegrations();
    expect(page.items[0]).toEqual({
      id: 'c-1',
      name: 'Copilot',
      kind: 'api',
      owner_name: 'Đỗ Hải Yến',
      scopes: ['ask_question'],
      calls_7d: null,
      error_rate_7d: null,
      quota_per_day: 1000,
      expires_at: soon,
      status: 'expiring',
    });
  });

  it('sums the hourly usage into seven local days and the top tools', async () => {
    const [row] = (await listIntegrations()).items;
    const usage = await getIntegrationUsage(row);
    expect(usageQuery?.get('granularity')).toBe('hour');
    expect(usage.daily_calls).toEqual([10, 0, 0, 0, 0, 0, 500]);
    expect(usage.top_tools).toEqual([
      { name: 'ask_question', share: 400 / 510 },
      { name: 'search_metrics', share: 110 / 510 },
    ]);
    expect(usage.quota_used_ratio).toBe(0.5);
    expect(usage.blocked_by_permission).toBe(4);
  });

  it("confirms with the client's updated_at as listed in If-Match", async () => {
    await confirmMyClient({ ...client, status: 'pending_owner' });
    expect(ifMatch).toBe('"2026-10-12T03:15:00.5Z"');
  });
});
