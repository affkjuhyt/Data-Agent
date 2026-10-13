/**
 * Theo dõi and Cảnh báo as the api sends them, without the mock's display fields: a monitor named
 * by its metric and alerting while it has an open alert, pausing through PATCH status, and an
 * investigation that is a new workspace.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { investigateAlert } from './alerts';
import { listMonitors, muteMonitor } from './monitors';
import type { components } from './schema';

type Schemas = components['schemas'];

const monitor = (id: string, status: string): Schemas['MonitorView'] => ({
  id,
  data_space_id: 'ds-1',
  owner_id: 'u-me',
  agent_def_id: null,
  metric_id: 'm-npl',
  scope_filter: { split: 'region' },
  rule_kind: 'threshold',
  rule: { direction: 'above', value: 2, window: '1d' },
  schedule_cron: '0 7 * * *',
  notify_mode: 'alert_only',
  recipients: [{ principal_type: 'user', principal_id: 'u-me', channels: ['teams', 'email'] }],
  backtest: { periods: 12, fired: 5 },
  noise_warning: true,
  status,
  paused_reason: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
});

const alert = (id: string, monitorId: string, status: string, firedAt: string) => ({
  id,
  monitor_id: monitorId,
  fired_at: firedAt,
  period_label: null,
  value: '2.3',
  threshold_value: '2',
  summary: 'Nợ xấu vượt ngưỡng',
  skill_run_id: null,
  dedup_key: id,
  occurrences: 1,
  kind: 'threshold',
  status,
  assigned_to: null,
  close_note: null,
  closed_at: null,
  created_at: firedAt,
  updated_at: firedAt,
});

let patched: unknown;
let created: unknown;
const server = setupServer(
  http.get('*/v1/monitors', () =>
    HttpResponse.json({
      items: [monitor('mon-1', 'active'), monitor('mon-2', 'paused')],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/metrics', () =>
    HttpResponse.json({
      items: [{ id: 'm-npl', code: 'NPL_RATIO', display_name: 'Nợ xấu', current_version: 4 }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/me', () => HttpResponse.json({ id: 'u-me' })),
  http.get('*/v1/alerts', () =>
    HttpResponse.json({
      items: [
        alert('a-old', 'mon-1', 'closed', '2026-10-01T07:00:00Z'),
        alert('a-new', 'mon-1', 'open', '2026-10-10T07:00:00Z'),
      ],
      next_page_token: '',
    }),
  ),
  http.patch('*/v1/monitors/:id', async ({ request }) => {
    patched = await request.json();
    return HttpResponse.json(monitor('mon-1', 'paused'));
  }),
  http.post('*/v1/workspaces', async ({ request }) => {
    created = await request.json();
    return HttpResponse.json({ id: 'ws-9' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  patched = undefined;
  created = undefined;
});
afterAll(() => server.close());

describe('listMonitors', () => {
  it('reads MonitorView with its metric, the viewer and its newest alert', async () => {
    const { items } = await listMonitors();
    expect(items[0]).toMatchObject({
      name: 'Nợ xấu',
      metric_text: 'NPL_RATIO v4 · tách theo region',
      condition_text: '> 2 trong 1 ngày',
      state: 'alerting',
      last_alert_id: 'a-new',
      channels_text: 'Teams, Email',
      owner_is_me: true,
      last_checked_at: null,
      noise_warning: true,
    });
    expect(items[1]).toMatchObject({ state: 'muted', last_alert_id: null });
  });

  it('pauses and resumes through PATCH status', async () => {
    await muteMonitor('mon-1', true);
    expect(patched).toEqual({ status: 'paused' });
    await muteMonitor('mon-1', false);
    expect(patched).toEqual({ status: 'active' });
  });
});

describe('investigateAlert', () => {
  it('creates a workspace that carries the alert', async () => {
    const result = await investigateAlert({
      id: 'a-new',
      code: '#A-1',
      title: 'Nợ xấu vượt ngưỡng',
      summary: 'Nợ xấu vượt ngưỡng',
    });
    expect(result).toEqual({ workspace_id: 'ws-9' });
    expect(created).toMatchObject({
      title: 'Điều tra #A-1 · Nợ xấu vượt ngưỡng',
      created_from: 'blank',
      params: { alert_id: 'a-new' },
    });
  });
});
