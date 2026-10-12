/**
 * Thu thập as the api sends it: a source's health comes from what the summary reports, its review
 * queue links to its run in review; a schema's fields come from the jsonb; a run's counts come
 * from its records, of which the undecided ones block publishing; bulk review sends A3's filter.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import {
  approveCrawlSchema,
  bulkReviewRecords,
  getCrawlRun,
  listCrawlSchemas,
  listCrawlSources,
  listRunRecords,
} from './crawl';

const source = (id: string, extra: Record<string, unknown>) => ({
  id,
  name: `Nguồn ${id}`,
  kind: 'web_listing',
  status: 'active',
  allowed_domains: ['a.vn', 'b.vn'],
  schedule_cron: '0 8 * * *',
  review_gate: 'exceptions',
  consecutive_blocked: 0,
  open_drifts: 0,
  pending_review: 0,
  active_schema_version_id: 'v-1',
  last_run_at: null,
  sample_accuracy: null,
  ...extra,
});

const record = (id: string, status: string, extra: Record<string, unknown> = {}) => ({
  id,
  run_id: 'run-1',
  entity_key: 'Ngân hàng K',
  period: '12 tháng',
  values: { rate: 6.85, channel: 'Tại quầy' },
  field_confidence: { rate: 0.96 },
  record_confidence: 0.9,
  status,
  review_reasons: ['cross_source_anomaly'],
  is_random_sample: false,
  source_page: {
    page_id: 'p',
    url: 'nganhangk.vn/lai-suat',
    object_key: null,
    content_type: null,
    sha256: 'x',
  },
  ...extra,
});

let approveBody: unknown;
let bulkBody: unknown;
const server = setupServer(
  http.get('*/v1/crawl-sources', () =>
    HttpResponse.json({
      items: [
        source('s-1', { pending_review: 3, sample_accuracy: '0.991' }),
        source('s-2', {
          consecutive_blocked: 3,
          last_fetch_error: { url: 'x', http_status: 403, fetched_at: '2026-10-07T00:00:00Z' },
        }),
        source('s-3', { active_schema_version_id: null }),
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/crawl-runs', ({ request }) => {
    expect(new URL(request.url).searchParams.get('status')).toBe('in_review');
    return HttpResponse.json({ items: [{ id: 'run-1', source_id: 's-1' }], next_page_token: '' });
  }),
  http.get('*/v1/crawl-sources/:id/schemas', () =>
    HttpResponse.json({
      items: [
        {
          id: 'v-2',
          version: 2,
          status: 'testing',
          record_selector: 'table > tr',
          sample_page_ids: ['p1', 'p2'],
          test_required_accuracy: '0.979',
          fields: [
            {
              name: 'rate',
              type: 'percent',
              unit: '%/năm',
              required: true,
              is_key: false,
              locator: 'td:nth-child(2)',
              canonical_attr: 'Lãi suất',
            },
            { name: 'channel', type: 'text', required: false, is_key: true, canonical_attr: null },
          ],
        },
      ],
      next_page_token: '',
    }),
  ),
  http.post('*/v1/crawl-schemas/:action', async ({ request }) => {
    approveBody = await request.json();
    return HttpResponse.json({ id: 'v-2', status: 'active' });
  }),
  http.get('*/v1/crawl-runs/:id/records', () =>
    HttpResponse.json({
      items: [
        record('r-1', 'flagged'),
        record('r-2', 'pending', { is_random_sample: true, review_reasons: ['random_sample'] }),
        record('r-3', 'approved', { is_random_sample: true }),
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/crawl-runs/:id', () =>
    HttpResponse.json({
      id: 'run-1',
      source_id: 's-1',
      status: 'in_review',
      records_extracted: 100,
      sample_accuracy: '1',
      started_at: '2026-10-10T01:05:00Z',
      created_at: '2026-10-10T01:00:00Z',
    }),
  ),
  http.get('*/v1/crawl-sources/:id', () =>
    HttpResponse.json({ id: 's-1', name: 'Lãi suất huy động', review_gate: 'exceptions' }),
  ),
  http.post('*/v1/extracted-records\\:bulk-review', async ({ request }) => {
    bulkBody = await request.json();
    return HttpResponse.json({ updated: 2, by_status: { approved: 2 } });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('crawl from the api', () => {
  it('reads each source health and links its run in review', async () => {
    const [onSchedule, blocked, pending] = (await listCrawlSources()).items;
    expect(onSchedule).toMatchObject({
      health: 'on_schedule',
      scope_text: '2 miền',
      schedule_text: '0 8 * * *',
      sample_accuracy_pct: 99.1,
      pending_review: 3,
      review_run_id: 'run-1',
      drift_id: null,
    });
    expect(blocked).toMatchObject({
      health: 'blocked',
      health_text: 'Bị chặn 403 · 3 lượt',
      review_run_id: null,
    });
    expect(pending.health).toBe('pending_schema');
  });

  it('reads the schema fields and its test, and approves with the choices as the note', async () => {
    const [schema] = (await listCrawlSchemas('s-1')).items;
    expect(schema.sample).toBeNull();
    expect(schema.test).toEqual({ pages: 2, records: null, required_correct_pct: 97.9 });
    expect(schema.fields).toEqual([
      {
        name: 'rate',
        type: 'numeric',
        unit: '%/năm',
        required: true,
        key: false,
        locator: 'td:nth-child(2)',
        canonical: 'Lãi suất',
        canonical_options: [],
        confidence: null,
      },
      expect.objectContaining({ name: 'channel', key: true, canonical: null, locator: '—' }),
    ]);
    await approveCrawlSchema('v-2', { channel: 'Kênh giao dịch' });
    expect(approveBody).toEqual({ note: 'Thuộc tính chuẩn: channel → Kênh giao dịch' });
  });

  it('counts the run from its records; only undecided ones are queued', async () => {
    const run = await getCrawlRun('run-1');
    expect(run).toMatchObject({
      source_name: 'Lãi suất huy động',
      status: 'extracted',
      record_count: 100,
      review_count: 2,
      blocking_count: 2,
      sample_count: 2,
      sample_pct: 2,
      accuracy: {
        sample_reviewed: 1,
        sample_accuracy_pct: 100,
        recent_runs_ok: null,
        gate: 'exceptions',
      },
    });
    const records = (await listRunRecords('run-1')).items;
    expect(records.map((r) => r.id)).toEqual(['r-1', 'r-2']);
    expect(records[0]).toMatchObject({
      title: 'Ngân hàng K · 12 tháng',
      value_text: '6.85',
      reasons: ['cross_source_anomaly'],
      reason_text: 'Bất thường chéo nguồn',
      blocking: true,
      source_url: 'nganhangk.vn/lai-suat',
      raw: null,
      values: [
        { field: 'rate', value: '6.85', confidence: 0.96 },
        { field: 'channel', value: 'Tại quầy', confidence: 0.9 },
      ],
    });
  });

  it('sends the bulk review as a filter', async () => {
    const result = await bulkReviewRecords({
      run_id: 'run-1',
      review_reasons: ['random_sample'],
      decision: 'confirm',
      reason: 'Đã đối chiếu',
    });
    expect(result.updated).toBe(2);
    expect(bulkBody).toEqual({
      decision: 'confirm',
      filter: { run_id: 'run-1', review_reasons: ['random_sample'] },
      reason: 'Đã đối chiếu',
    });
  });
});
