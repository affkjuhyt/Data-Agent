/**
 * Báo cáo định kỳ and Bản tin as the api sends them, without the mock's display fields: the
 * schedule said in words from its cron, period rule, formats and recipients, its latest run as the
 * one recent send, nothing offered that the api cannot store, and a digest read from the run that
 * its notification names.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { cronText, getReport, getReportRun, listMyDigests, runReportNow } from './reports';
import type { components } from './schema';

type Schemas = components['schemas'];

const run: Schemas['ReportRunView'] = {
  id: 'run-1',
  report_schedule_id: 'rep-1',
  kind: 'report',
  as_of: '2026-10-12',
  workflow_id: null,
  trigger: 'schedule',
  checks: {},
  outputs: [],
  status: 'blocked_by_checks',
  finished_at: '2026-10-13T00:05:00Z',
  created_at: '2026-10-13T00:00:00Z',
};

const schedule: Schemas['ReportScheduleDetail'] = {
  id: 'rep-1',
  data_space_id: 'ds-1',
  kind: 'report',
  workspace_id: 'ws-1',
  owner_id: 'u-me',
  title: 'Rủi ro tín dụng · tuần',
  cron: '30 7 * * 1',
  work_calendar_id: null,
  cutoff_time: '06:30:00',
  as_of_rule: 'end_of_previous_week',
  formats: ['pdf', 'html'],
  recipients: [
    {
      id: 'r-1',
      principal_type: 'user',
      principal_id: 'u-1',
      channels: ['email'],
      permission_group: 'default',
      created_at: '2026-10-01T00:00:00Z',
    },
    {
      id: 'r-2',
      principal_type: 'group',
      principal_id: 'g-1',
      channels: ['teams', 'email'],
      permission_group: 'default',
      created_at: '2026-10-01T00:00:00Z',
    },
  ],
  require_publish_check: true,
  status: 'active',
  latest_run: run,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
};

let runNowBody: unknown;
const server = setupServer(
  http.get('*/v1/reports/:id', () => HttpResponse.json(schedule)),
  http.post('*/v1/reports/:action', async ({ request }) => {
    runNowBody = await request.json();
    return HttpResponse.json(
      { id: 'run-2', status: 'running', poll_after_ms: 2000 },
      { status: 202 },
    );
  }),
  http.get('*/v1/me/digests', () => HttpResponse.json({ code: 'not_found' }, { status: 404 })),
  http.get('*/v1/me/notifications', ({ request }) => {
    expect(new URL(request.url).searchParams.get('kind')).toBe('digest');
    return HttpResponse.json({
      items: [
        {
          id: 'n-1',
          kind: 'digest',
          title: 'Bản tin tuần 41',
          body: null,
          link: null,
          priority: 'normal',
          source_ref: 'run-1',
          data_space_id: 'ds-1',
          batched_into: null,
          read_at: null,
          created_at: '2026-10-13T00:30:00Z',
        },
      ],
      next_page_token: '',
    });
  }),
  http.get('*/v1/report-runs/:id', () => HttpResponse.json(run)),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('reports on the api', () => {
  it('says the schedule in words and offers nothing the api cannot store', async () => {
    const report = await getReport('rep-1');
    expect(report.sections).toBeNull();
    expect(report.settings_editable).toBe(false);
    expect(report.test_to_me).toBe(false);
    expect(report.params).toEqual([
      { label: 'Kỳ số liệu', value: 'Hết tuần trước' },
      { label: 'Chốt số liệu', value: '06:30' },
      { label: 'Định dạng', value: 'PDF, Trong ứng dụng' },
    ]);
    expect(report.delivery).toEqual([
      { label: 'Lịch', value: 'Thứ Hai hằng tuần, 07:30' },
      { label: 'Người nhận', value: '1 người, 1 nhóm' },
      { label: 'Kênh', value: 'Email, Teams' },
    ]);
    expect(report.recent_runs).toHaveLength(1);
    expect(report.recent_runs[0]).toMatchObject({ id: 'run-1', status: 'blocked_by_checks' });
    expect(report.recent_runs[0].detail).toBe('Theo lịch · số liệu tới 12/10/2026');
    expect(report.summary_text).toMatch(/^Báo cáo định kỳ · lần chạy gần nhất /);
  });

  it('runs now without asking the api for a send to the caller alone', async () => {
    await runReportNow('rep-1');
    expect(runNowBody).toEqual({});
  });

  it('lists digests by the run their notification names and reads that run', async () => {
    const page = await listMyDigests();
    expect(page.items).toEqual([
      { id: 'run-1', title: 'Bản tin tuần 41', meta_text: '13/10/2026', unread: true },
    ]);
    const read = await getReportRun(page.items[0].id);
    expect(read).toMatchObject({ title: null, changed: null, kpis: [], decisions: [] });
    expect(read.meta_text).toMatch(/giữ lại vì chưa qua kiểm tra · số liệu tới 12\/10\/2026$/);
  });

  it('keeps a cron it cannot say as written', () => {
    expect(cronText('0 7 * * *')).toBe('Hằng ngày, 07:00');
    expect(cronText('0 8 1 * *')).toBe('Ngày 1 hằng tháng, 08:00');
    expect(cronText('*/15 * * * *')).toBe('*/15 * * * *');
  });
});
