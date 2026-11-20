/**
 * A metric as the api sends it (MetricDetail): a version lists the dimensions it allows by id,
 * and the screen says them by the names the entities give them.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getMetric } from './metrics';

const at = '2026-11-19T08:00:00Z';
const version = {
  id: 'mv-1',
  metric_id: 'm-1',
  version: 1,
  display_name: 'Doanh thu',
  expression: 'SUM(sales_amount)',
  unit: 'VND',
  decimals: 0,
  direction: 'higher_better',
  period_semantics: 'flow',
  time_grain: 'month',
  allowed_dimensions: ['dim-region', 'dim-gone'],
  change_note: null,
  status: 'certified',
  certified_at: at,
  certified_by: null,
  created_at: at,
};

const server = setupServer(
  http.get('*/v1/metrics/m-1', () =>
    HttpResponse.json({ id: 'm-1', code: 'REVENUE', status: 'active', current_version: 1, versions: [version] }),
  ),
  http.get('*/v1/entities', () =>
    HttpResponse.json({ items: [{ id: 'e-1', key: 'territory', name: 'Vùng' }], next_page_token: '' }),
  ),
  http.get('*/v1/entities/territory', () =>
    HttpResponse.json({
      id: 'e-1',
      key: 'territory',
      name: 'Vùng',
      dimensions: [{ id: 'dim-region', code: 'region', name: 'Khu vực', is_time: false }],
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('a metric from the api', () => {
  it('names the dimensions a version allows, never by their ids', async () => {
    const metric = await getMetric('m-1');
    expect(metric.versions[0].dimension_labels).toEqual([
      'Khu vực',
      'chiều không còn trong mô hình',
    ]);
  });
});
