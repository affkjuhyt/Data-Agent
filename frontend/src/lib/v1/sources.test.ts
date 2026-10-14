/**
 * Sources as the api sends them (catalog SourceView, DatasetView): the list rows derive what the
 * PROPOSAL fields would say, a source's detail is GET /sources/{id} plus its datasets, the connect form is
 * sent as SourceCreate and the connection test is read back from the source after the 202.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  createSource,
  getColumnStats,
  getSource,
  getTableProfile,
  listSourceDrifts,
  listSourceRows,
  previewTable,
  rescanSource,
  scanSummary,
  testSource,
  type ScanRun,
} from './sources';

const at = '2026-10-12T08:00:00Z';
const source = (extra: object = {}) => ({
  id: 's-1',
  data_space_id: 'ds-1',
  name: 'Core banking',
  kind: 'oracle',
  mode: 'federated',
  status: 'active',
  status_reason: null,
  authority_level: 'level_1',
  conn_options: {},
  connector_icon: null,
  host: 'core-db.noibo',
  port: 1521,
  database_name: 'CORE',
  db_version: null,
  freshness_sla: null,
  max_concurrency: 4,
  query_timeout_s: 60,
  profile_cron: null,
  scan_cron: null,
  scan_schemas: [],
  scan_window: null,
  work_calendar_id: null,
  last_checked_at: at,
  created_at: at,
  updated_at: at,
  ...extra,
});

let rows = [source()];
const server = setupServer(
  http.get('*/v1/sources', () => HttpResponse.json({ items: rows, next_page_token: '' })),
  http.get('*/v1/sources/:id', ({ params }) => {
    const row = rows.find((r) => r.id === params.id);
    return row
      ? HttpResponse.json(row)
      : HttpResponse.json(
          { status: 404, code: 'not_found', detail: 'Không tìm thấy nguồn.' },
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        );
  }),
  http.get('*/v1/data-spaces', () =>
    HttpResponse.json({ items: [{ id: 'ds-1', name: 'Rủi ro tín dụng' }], next_page_token: '' }),
  ),
  http.get('*/v1/datasets', ({ request }) =>
    HttpResponse.json({
      items:
        new URL(request.url).searchParams.get('source_id') === 's-1'
          ? [
              {
                id: 'd-1',
                schema_name: 'CORE',
                table_name: 'LOAN',
                description: 'Khoản vay',
                row_estimate: 1200,
                display_name: null,
              },
            ]
          : [],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/datasets/:id', ({ params }) =>
    params.id === 'd-1'
      ? HttpResponse.json({
          id: 'd-1',
          data_space_id: 'ds-1',
          source_id: 's-1',
          origin: 'source',
          schema_name: 'CORE',
          table_name: 'LOAN',
          display_name: 'Loan',
          row_estimate: 1200,
          columns: [
            {
              id: 'c-1',
              name: 'balance',
              data_type: 'NUMBER',
              normalized_type: 'number',
              profile: {
                sample_rows: 1200,
                null_ratio: '0.1',
                distinct_est: 900,
                min_value: '10',
                max_value: '1000',
                top_values: [{ value: '100', count: 4 }],
                created_at: at,
              },
            },
          ],
        })
      : undefined,
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  rows = [source()];
});
afterAll(() => server.close());

describe('sources from the api', () => {
  it('derives the connector, endpoint, status and Data Space of a row', async () => {
    rows = [source({ status: 'needs_fix', status_reason: 'Mật khẩu hết hạn' })];
    const [row] = (await listSourceRows()).items;
    expect(row).toMatchObject({
      connector: 'oracle',
      connector_label: 'Oracle',
      endpoint: 'core-db.noibo:1521/CORE',
      status_label: 'Cần sửa · Mật khẩu hết hạn',
      schema_drift: false,
      data_spaces: [{ id: 'ds-1', name: 'Rủi ro tín dụng' }],
    });
  });

  it('reads a source as its row and the datasets read from it', async () => {
    const detail = await getSource('s-1');
    expect(detail.name).toBe('Core banking');
    expect(detail.tables).toEqual([
      {
        id: 'd-1',
        name: 'CORE.LOAN',
        description: 'Khoản vay',
        row_count: 1200,
        column_count: null,
        refreshed_at: null,
      },
    ]);
    await expect(getSource('s-missing')).rejects.toMatchObject({ status: 404 });
  });

  it('sends the connect form as SourceCreate', async () => {
    let body: Record<string, unknown> = {};
    server.use(
      http.post('*/v1/sources', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json(source({ id: 's-2', status: 'draft', kind: 'rest_api' }), {
          status: 201,
        });
      }),
    );
    await createSource({
      name: 'Core banking',
      connector: 'rest',
      mode: 'ingest',
      config: {
        host: 'api.noibo',
        port: 443,
        database: '',
        username: 'agent_ro',
        secret_ref: 'kv/core',
      },
    });
    expect(body).toEqual({
      name: 'Core banking',
      kind: 'rest_api',
      mode: 'ingest',
      host: 'api.noibo',
      port: 443,
      database_name: null,
      credentials: { secret_ref: 'kv/core' },
    });
  });

  it('reads the connection test back from the source once the worker wrote it', async () => {
    rows = [source({ status: 'testing' })];
    server.use(
      http.post('*/v1/sources/:action', () => {
        // The worker finishes while the client waits.
        setTimeout(
          () => (rows = [source({ status: 'needs_fix', status_reason: 'Sai mật khẩu' })]),
          5,
        );
        return HttpResponse.json(
          { id: 's-1', status: 'testing', poll_after_ms: 10 },
          { status: 202 },
        );
      }),
    );
    const test = await testSource('s-1');
    expect(test.checks).toEqual([
      { key: 'login', title: 'Kết nối và đăng nhập', ok: false, detail: 'Sai mật khẩu' },
    ]);
    expect(test.tables).toEqual([]);
  });

  it('leaves the crawl sources’ drifts out of the connected sources’ list', async () => {
    server.use(
      http.get('*/v1/schema-drifts', () =>
        HttpResponse.json({
          items: [{ id: 'd-1', source_id: 'cs-1', drift_kind: 'structural', severity: 'major' }],
          next_page_token: '',
        }),
      ),
    );
    expect((await listSourceDrifts()).items).toEqual([]);
  });

  it('uses the canonical query run when the table preview route is not served', async () => {
    const run = {
      id: 'qr-1',
      data_space_id: 'ds-1',
      caller_kind: 'ad_hoc',
      caller_id: 'u-1',
      principal_id: 'u-1',
      source_id: 's-1',
      route: 'live',
      period_close_id: null,
      policies_applied: [],
      metric_version_ids: [],
      as_of: null,
      row_count: 1,
      result_hash: null,
      duration_ms: 12,
      status: 'succeeded',
      error: null,
      started_at: at,
    };
    server.use(
      http.post('*/v1/datasets/:action', () =>
        HttpResponse.json(
          { status: 404, code: 'not_found', detail: 'Not served.' },
          { status: 404 },
        ),
      ),
      http.post('*/v1/query-runs', () => HttpResponse.json(run, { status: 202 })),
      http.get('*/v1/query-runs/:id', () => HttpResponse.json(run)),
      http.get('*/v1/query-runs/:id/rows', () =>
        HttpResponse.json({
          columns: [{ name: 'balance', type: 'number' }],
          items: [[100]],
          next_page_token: '',
          truncated: false,
          expires_at: at,
        }),
      ),
    );
    await expect(previewTable('d-1', [])).resolves.toMatchObject({
      name: 'Loan',
      matched_rows: 1,
      rows: [[100]],
    });
  });

  it('derives column statistics from the dataset profile when the stats route is absent', async () => {
    server.use(
      http.get('*/v1/datasets/:id/columns/:column', () =>
        HttpResponse.json(
          { status: 404, code: 'not_found', detail: 'Not served.' },
          { status: 404 },
        ),
      ),
    );
    await expect(getColumnStats('d-1', 'balance')).resolves.toMatchObject({
      name: 'balance',
      distinct: 900,
      min: '10',
      max: '1000',
    });
  });
});

describe('UC-04-02 scans and profiles', () => {
  const run = (extra: Partial<ScanRun> = {}): ScanRun => ({
    id: 'r-1',
    source_id: 's-1',
    kind: 'schema',
    trigger: 'manual',
    workflow_id: null,
    status: 'succeeded',
    tables_total: 12,
    tables_scanned: 12,
    tables_skipped: [],
    resume_from: null,
    started_at: at,
    finished_at: at,
    created_at: at,
    ...extra,
  });

  it('AC-04-02-04 says which tables a scan left aside and why', () => {
    const summary = scanSummary(
      run({
        status: 'partial',
        tables_scanned: 11,
        tables_skipped: [
          { table: 'credit.giao_dich', reason: 'timeout' },
          { table: 'credit.khach_hang', reason: 'schema_changed', schema_hash: 'ab' },
        ],
      }),
    );
    expect(summary.text).toBe('Đã quét 11/12 bảng.');
    expect(summary.skipped).toEqual([
      'credit.giao_dich: thống kê quá thời gian cho phép',
      'credit.khach_hang: cấu trúc đã đổi, chờ duyệt',
    ]);
  });

  it('AC-04-02-05 says where the next scan goes on', () => {
    const summary = scanSummary(
      run({ status: 'partial', tables_scanned: 7, resume_from: 'credit.tai_san' }),
    );
    expect(summary.text).toBe('Đã quét 7/12 bảng. Lượt sau quét tiếp từ credit.tai_san.');
  });

  it('rescans a source and reads the run back until it ends', async () => {
    let reads = 0;
    server.use(
      http.post('*/v1/sources/:id/scans', () =>
        HttpResponse.json({ id: 'r-1', status: 'running', poll_after_ms: 1 }, { status: 202 }),
      ),
      http.get('*/v1/scan-runs/:id', () => {
        reads += 1;
        return HttpResponse.json(run({ status: reads < 3 ? 'running' : 'succeeded' }));
      }),
    );
    await expect(rescanSource('s-1')).resolves.toMatchObject({ status: 'succeeded' });
    expect(reads).toBe(3);
  });

  it('AC-04-02-07 reads the value pattern and marks an agent description as a suggestion', async () => {
    server.use(
      http.get('*/v1/datasets/:id', () =>
        HttpResponse.json({
          id: 'd-1',
          data_space_id: 'ds-1',
          source_id: 's-1',
          origin: 'source',
          schema_name: 'CORE',
          table_name: 'CUSTOMER',
          display_name: null,
          row_estimate: 3,
          columns: [
            {
              id: 'c-1',
              name: 'lien_he',
              data_type: 'text',
              normalized_type: 'string',
              description: 'Số liên hệ của khách',
              description_status: 'suggested',
              pii_label: 'phone',
              profile: {
                sample_rows: 3,
                null_ratio: '0',
                distinct_est: 3,
                min_value: null,
                max_value: null,
                top_values: [],
                pattern_hint: 'số điện thoại di động',
                created_at: at,
              },
            },
            {
              id: 'c-2',
              name: 'branch',
              data_type: 'text',
              normalized_type: 'string',
              description: 'Chi nhánh',
              description_status: 'approved',
              pii_label: null,
              profile: null,
            },
          ],
        }),
      ),
    );
    const profile = await getTableProfile('d-1');
    expect(profile.columns.map((c) => [c.name, c.pattern, c.suggested, c.histogram_note])).toEqual([
      ['lien_he', 'số điện thoại di động', true, 'che'],
      ['branch', null, false, null],
    ]);
  });
});
