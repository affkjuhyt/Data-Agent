/**
 * Alerts and notifications as the api sends them (AlertView, NotificationView): one alert is its
 * row in the list, its details come from the value and threshold, and a notification's kind
 * picks its category, icon and target.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getAlert, reopenAlert } from './alerts';
import { listNotifications } from './notifications';

const at = '2026-10-12T07:15:00Z';
let reopenBody: unknown;
const server = setupServer(
  http.post('*/v1/alerts/:action', async ({ request }) => {
    reopenBody = await request.json();
    return HttpResponse.json({
      id: '0192f0aa-2222-7000-8000-000000000001',
      monitor_id: 'm-1',
      status: 'open',
      summary: 'Nợ xấu bán lẻ vượt 2,3%',
      value: null,
      threshold_value: null,
      period_label: null,
      occurrences: 1,
      fired_at: at,
      closed_at: null,
      close_note: null,
    });
  }),
  http.get('*/v1/alerts', () =>
    HttpResponse.json({
      items: [
        {
          id: '0192f0aa-2222-7000-8000-000000000001',
          monitor_id: 'm-1',
          kind: 'metric',
          status: 'open',
          summary: 'Nợ xấu bán lẻ vượt 2,3%\nDH Bắc Bộ tăng mạnh\nXem phân rã',
          value: '2.41',
          threshold_value: '2.3',
          period_label: 'T9/2026',
          occurrences: 1,
          fired_at: at,
          closed_at: null,
          close_note: null,
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/me/notifications', () =>
    HttpResponse.json({
      items: [
        {
          id: 'n-1',
          kind: 'hitl',
          title: 'Cần bạn duyệt 214 khoản',
          source_ref: 'h-1',
          created_at: at,
          read_at: null,
        },
        {
          id: 'n-2',
          kind: 'task_done',
          title: 'Đã xong báo cáo',
          source_ref: 'r-1',
          created_at: at,
          read_at: at,
        },
      ],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('monitoring from the api', () => {
  it('reads one alert from the list, titled by its summary', async () => {
    const alert = await getAlert('0192f0aa-2222-7000-8000-000000000001');
    expect(alert).toMatchObject({
      code: '#0192f0aa',
      title: 'Nợ xấu bán lẻ vượt 2,3%',
      chart: null,
      analysis: null,
      closed: null,
    });
    expect(alert.details).toEqual([
      { label: 'Giá trị', value: '2,41', mono: true },
      { label: 'Ngưỡng', value: '2,3', mono: true },
      { label: 'Kỳ', value: 'T9/2026' },
    ]);
    await expect(getAlert('missing')).rejects.toMatchObject({ status: 404 });
  });

  it('reopens an alert with the reason A3 requires', async () => {
    const alert = await reopenAlert('0192f0aa-2222-7000-8000-000000000001', 'Số vượt ngưỡng lại.');
    expect(reopenBody).toEqual({ reason: 'Số vượt ngưỡng lại.' });
    expect(alert.status).toBe('open');
  });

  it('routes a HITL notification to its request and leaves the others on the feed', async () => {
    const [hitl, done] = (await listNotifications()).items;
    expect(hitl).toMatchObject({
      category: 'needs_you',
      icon: 'approve',
      text: 'Cần bạn duyệt 214 khoản',
      target: { kind: 'hitl', id: 'h-1' },
    });
    expect(done).toMatchObject({ category: 'agent', icon: 'check', target: null });
  });
});
