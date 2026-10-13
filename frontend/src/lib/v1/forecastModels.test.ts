/**
 * A forecast model as the api sends it (ForecastModelDetail): the version waiting for approval is
 * the candidate with its card, and the model's MASE figures are its monitoring.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getForecastModel, rejectModelVersion } from './forecast';

let rejected: unknown;
const server = setupServer(
  http.get('*/v1/forecast-models/:id', () =>
    HttpResponse.json({
      id: 'fm-1',
      metric_code: 'GIA_XANG_RON95',
      frequency: 'week',
      status: 'active',
      degraded: true,
      rolling_mase: '1.234',
      backtest_mase: 0.91,
      current_version: {
        id: 'mv-7',
        version: 7,
        status: 'pending_approval',
        algorithm: 'ETS',
        training_from: '2023-01-01',
        training_to: '2026-09-30',
        model_card: { 'Mục tiêu': 'Giá kỳ điều hành tới', notes: { nested: true } },
      },
    }),
  ),
  http.post('*/v1/forecast-model-versions/:action', async ({ request }) => {
    rejected = await request.json();
    return HttpResponse.json({ id: 'mv-7', status: 'rejected' });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('forecast models from the api', () => {
  it('reads the candidate, its card and the model’s MASE', async () => {
    const m = await getForecastModel('fm-1');
    expect(m).toMatchObject({
      metric_name: 'GIA_XANG_RON95',
      frequency_label: 'tuần',
      status_text: 'v7 chờ duyệt · suy giảm',
    });
    expect(m.candidate?.card).toEqual([
      { label: 'Thuật toán', value: 'ETS' },
      { label: 'Dữ liệu huấn luyện', value: '2023-01-01 – 2026-09-30' },
      { label: 'Mục tiêu', value: 'Giá kỳ điều hành tới' },
    ]);
    expect(m.can_approve.allowed).toBe(true);
    expect(m.monitoring?.stats.map((s) => s.value)).toEqual(['1,23', '0,91']);
    expect(m.monitoring?.status).toBe('degraded');
  });

  it('rejects with a reason', async () => {
    await rejectModelVersion('mv-7', 'Thiếu biến tỷ giá');
    expect(rejected).toEqual({ reason: 'Thiếu biến tỷ giá' });
  });
});
