/**
 * Sổ dự báo on A2's ledger: a series is a model and a product code, the bands are the stored
 * quantiles, the calibration counts come back from the coverage shares over the scored periods.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { getForecastLedger, listForecastSeries } from './forecast';

const MODEL = '0192aaaa-0000-7000-8000-000000000001';

const line = (
  id: string,
  period: string,
  product: string,
  extra: Record<string, unknown> = {},
) => ({
  id,
  forecast_model_id: MODEL,
  model_version_id: 'mv-2',
  period,
  scope: { product_code: product },
  issued_at: `${period}T08:00:00+07:00`,
  issued_via: 'bulletin',
  baseline_value: '19730',
  point: '20100',
  quantiles: { p02_5: 19500, p10: 19800, p50: 20100, p90: 20400, p97_5: 20700 },
  status: 'open',
  actual: null,
  in80: null,
  in95: null,
  miss: null,
  miss_attribution: null,
  error_book_entry_id: null,
  ...extra,
});

let asked: URLSearchParams[] = [];
const server = setupServer(
  http.get('*/v1/forecast-ledger', ({ request }) => {
    const q = new URL(request.url).searchParams;
    asked.push(q);
    const product = q.get('product_code');
    const items = [
      line('l-3', '2026-07-23', 'e5ron92'),
      line('l-2', '2026-07-09', 'e5ron92', {
        status: 'scored',
        actual: '20700',
        in80: false,
        in95: true,
        miss: true,
        miss_attribution: { component: 'fund', predicted_mode: 200, actual: 0 },
        error_book_entry_id: 'eb-31',
      }),
      line('l-1', '2026-07-09', 'mazut'),
    ].filter((l) => !product || l.scope.product_code === product);
    return HttpResponse.json({
      items,
      next_page_token: '',
      calibration: q.get('forecast_model_id')
        ? {
            forecast_model_id: MODEL,
            product_code: product,
            last_n: 20,
            n_scored: 12,
            coverage80: '0.75',
            coverage95: '0.9167',
            mae: '210.5',
            crps: '124',
            crps_naive: '200',
            miss_count: 2,
            insufficient: false,
          }
        : null,
    });
  }),
  http.get('*/v1/forecast-models/:id', () =>
    HttpResponse.json({ id: MODEL, metric_code: 'fuel_ceiling', current_version: { version: 2 } }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('the forecast ledger from the api', () => {
  it('lists one series per model and product', async () => {
    expect((await listForecastSeries()).items).toEqual([
      { id: `ledger~${MODEL}~e5ron92`, label: 'E5RON92' },
      { id: `ledger~${MODEL}~mazut`, label: 'Mazut' },
    ]);
  });

  it('reads the lines, their bands and the calibration of one series', async () => {
    asked = [];
    const ledger = await getForecastLedger(`ledger~${MODEL}~e5ron92`, 10);
    expect(asked[0].get('product_code')).toBe('e5ron92');
    expect(asked[0].get('page_size')).toBe('10');
    expect(ledger).toMatchObject({
      product_label: 'E5RON92',
      model_version_label: 'fuel_ceiling v2',
      unit_label: 'đ/lít',
      following_schedule: null,
      window_periods: 10,
      calibration: {
        periods: 12,
        inside80: 9,
        inside95: 11,
        mae: 210.5,
        crps_vs_naive: -0.38,
        missed: 2,
      },
    });
    expect(ledger.products.map((p) => p.label)).toEqual(['E5RON92', 'Mazut']);
    expect(ledger.rows[1]).toEqual({
      period_date: '2026-07-09',
      published_at: '2026-07-09T08:00:00+07:00',
      channel_label: 'bản tin',
      median: 20100,
      lo80: 19800,
      hi80: 20400,
      lo95: 19500,
      hi95: 20700,
      actual: 20700,
      error: 600,
      inside80: false,
      inside95: true,
      missed: true,
      main_driver: 'Quỹ bình ổn khác dự kiến',
      error_book_ref: 'eb-31',
    });
  });
});
