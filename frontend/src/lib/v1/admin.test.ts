/**
 * Quản trị as the api sends it, without the PROPOSAL display fields: users and their roles,
 * model providers, the routing audit of a week and the notification preferences.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listAdminUsers } from './adminAccess';
import {
  getNotificationPreferences,
  getRoutingLog,
  listModelRoutes,
  listProviders,
} from './adminPlatform';

const at = '2026-10-12T08:00:00Z';
const provider = {
  id: 'p-1',
  code: 'frontier-a',
  display_name: 'Nhà cung cấp A',
  kind: 'frontier',
  endpoint: 'https://api.a.example',
  data_region: 'ap-southeast-1',
  zero_retention: true,
  approved_at: null,
  status: 'active',
  monthly_cost_vnd: '120000',
  monthly_budget_vnd: '5000000',
  price: { 'model-x': {}, 'model-y': {} },
};
let routingQuery: URLSearchParams | undefined;

const server = setupServer(
  http.get('*/v1/admin/users', () =>
    HttpResponse.json({
      items: [
        {
          id: 'u-1',
          username: 'yen',
          display_name: 'Đỗ Hải Yến',
          kind: 'human',
          status: 'active',
          last_synced_at: at,
          org_roles: [{ id: 'r-1', code: 'AC01', name: 'Chuyên viên phân tích' }],
          data_space_roles: [
            {
              data_space_id: 'ds-1',
              data_space_code: 'RUI_RO',
              roles: [{ id: 'r-1', code: 'AC01', name: 'Chuyên viên phân tích' }],
            },
          ],
        },
        {
          id: 'svc-1',
          username: 'svc',
          display_name: 'Client BI',
          kind: 'service',
          status: 'active',
          last_synced_at: null,
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/admin/models', () =>
    HttpResponse.json([
      {
        id: 'm-1',
        task: 'sql',
        model_name: 'qwen-72b',
        status: 'active',
        fallback_model: 'qwen-14b',
        latency_p95_ms: 820,
        latest_eval: { pass_rate: '0.94' },
      },
    ]),
  ),
  http.get('*/v1/admin/model-providers', () =>
    HttpResponse.json({ items: [provider], next_page_token: '' }),
  ),
  http.get('*/v1/admin/model-route-decisions', ({ request }) => {
    routingQuery = new URL(request.url).searchParams;
    return HttpResponse.json({
      items: [
        {
          id: 'd-1',
          decided_at: at,
          task: 'answer',
          decision: 'frontier_blocked',
          reasons: ['internal_data'],
          chosen_provider_id: 'p-1',
          model_name: 'model-x',
          caller_kind: 'user',
        },
        {
          id: 'd-2',
          decided_at: at,
          task: 'answer',
          decision: 'on_prem_default',
          reasons: [],
          chosen_provider_id: 'p-0',
          model_name: 'qwen-72b',
          caller_kind: 'agent',
        },
      ],
      next_page_token: '',
    });
  }),
  http.get('*/v1/me/notification-prefs', () =>
    HttpResponse.json({
      items: [
        {
          kind: 'hitl',
          channels: ['in_app', 'teams'],
          batch_mode: 'instant',
          quiet_hours: { from: '22:00', to: '07:00' },
        },
        { kind: 'digest', channels: ['email'], batch_mode: 'daily', quiet_hours: null },
      ],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('Quản trị from the api', () => {
  it('says a user’s roles and spaces, and reads a service account as a non-person', async () => {
    const [yen, svc] = (await listAdminUsers()).items;
    expect(yen).toMatchObject({
      kind: 'person',
      role_name: 'Chuyên viên phân tích',
      data_space_text: 'RUI_RO',
    });
    expect(svc.kind).toBe('agent');
  });

  it('reads the model list, an array, with its fallback and eval score', async () => {
    const [m] = (await listModelRoutes()).items;
    expect(m).toMatchObject({ fallback_name: 'qwen-14b', latency_p95_ms: 820, eval_score: 0.94 });
  });

  it('reads a frontier provider: models from its price list, pending until AC06 approves', async () => {
    const [p] = (await listProviders()).items;
    expect(p).toMatchObject({
      kind: 'frontier',
      model_name: 'model-x',
      model_note: 'model-y',
      zero_retention: 'committed',
      ac06_status: 'pending',
      month_cost_vnd: 120000,
      month_budget_vnd: 5000000,
    });
  });

  it('asks for a week of route decisions and counts them', async () => {
    const log = await getRoutingLog({ week: '2026-W42', page_size: 50 });
    expect(routingQuery?.get('from')).toBe('2026-10-12T00:00:00.000Z');
    expect(routingQuery?.get('to')).toBe('2026-10-19T00:00:00.000Z');
    expect(log).toMatchObject({
      week: '2026-W42',
      blocked_internal: 1,
      local_calls: 1,
      frontier_calls: 0,
    });
    expect(log.items[0]).toMatchObject({
      decision: 'blocked',
      provider_name: 'Nhà cung cấp A',
      question: 'answer',
    });
  });

  it('reads the preferences per kind, with the daily batch as "Bản tin"', async () => {
    const prefs = await getNotificationPreferences();
    expect(prefs.events[0]).toMatchObject({
      key: 'hitl',
      label: 'Cần bạn quyết',
      channels: { in_app: true, teams: true, zalo: false, email: false, digest: false },
      locked: ['in_app'],
    });
    expect(prefs.events[1].channels.digest).toBe(true);
    expect(prefs.quiet_hours_text).toBe('Không gửi từ 22:00 đến 07:00');
    expect(prefs.grouping_text).toBe('Gộp vào bản tin hằng ngày');
  });
});
