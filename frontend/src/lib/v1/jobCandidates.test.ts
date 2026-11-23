/**
 * Job nạp · Thiết lập where the PROPOSAL candidates are not served: the tables of ingest sources
 * that no job reads and the login can read, their cursor candidates as the api ranks them (DF-13),
 * and the job created on the source.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { createJob, listJobCandidates } from './sources';

let created: unknown;
const server = setupServer(
  http.get('*/v1/ingestion-job-candidates', () =>
    HttpResponse.json(
      { type: 'about:blank', title: 'not_found', status: 404, code: 'not_found' },
      { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
    ),
  ),
  http.get('*/v1/sources', () =>
    HttpResponse.json({
      items: [
        { id: 's-core', name: 'Core banking', mode: 'ingest' },
        { id: 's-dw', name: 'Kho dữ liệu', mode: 'federated' },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/datasets', () =>
    HttpResponse.json({
      items: [
        { id: 'd-1', source_id: 's-core', schema_name: 'dbo', table_name: 'card_transactions' },
        { id: 'd-2', source_id: 's-core', schema_name: 'dbo', table_name: 'loans' },
        { id: 'd-3', source_id: 's-dw', schema_name: 'mart', table_name: 'kpi' },
        { id: 'd-4', source_id: 's-core', schema_name: 'dbo', table_name: 'audit_log' },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/jobs', () =>
    HttpResponse.json({
      items: [{ id: 'j-1', source_id: 's-core', source_object: 'dbo.loans' }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/datasets/:id', ({ params }) =>
    HttpResponse.json(
      params.id === 'd-4'
        ? // The login has no SELECT on it: a job would fail at its first run.
          { id: 'd-4', columns: [], can_select: false, cursor_candidates: [] }
        : {
            id: 'd-1',
            columns: [
              { name: 'txn_id', data_type: 'VARCHAR2(16)', normalized_type: 'string' },
              { name: 'txn_date', data_type: 'DATE', normalized_type: 'date' },
              { name: 'updated_at', data_type: 'TIMESTAMP', normalized_type: 'timestamp' },
            ],
            can_select: true,
            cursor_candidates: [
              {
                name: 'updated_at',
                data_type: 'TIMESTAMP',
                normalized_type: 'timestamp',
                hint: 'thời điểm sửa dòng',
              },
              { name: 'txn_date', data_type: 'DATE', normalized_type: 'date', hint: 'ngày' },
            ],
          },
    ),
  ),
  http.post('*/v1/sources/:id/jobs', async ({ params, request }) => {
    created = { source: params.id, body: await request.json() };
    return HttpResponse.json({ id: 'j-2' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('job candidates from the api', () => {
  it('lists the readable ingest tables no job reads, with the cursors the api ranks', async () => {
    const { items } = await listJobCandidates();
    expect(items).toEqual([
      {
        dataset_id: 'd-1',
        source_id: 's-core',
        source_object: 'dbo.card_transactions',
        table_name: 'card_transactions',
        source_name: 'Core banking',
        cursor_columns: [
          { name: 'updated_at', data_type: 'TIMESTAMP', hint: 'thời điểm sửa dòng' },
          { name: 'txn_date', data_type: 'DATE', hint: 'ngày' },
        ],
        cursor_value: null,
        suggested_method: 'incremental',
        target_text: 'dbo.card_transactions',
        estimate: null,
      },
    ]);

    await createJob(
      {
        dataset_id: 'd-1',
        method: 'incremental',
        cursor_column: 'updated_at',
        schedule: 'hourly',
        freshness_sla_hours: 4,
        retry: 'backoff_3',
        no_overlap: true,
        on_schema_change: 'accept_new_columns',
        on_invalid_record: 'quarantine_30d',
      },
      items[0],
    );
    expect(created).toEqual({
      source: 's-core',
      body: {
        name: 'card_transactions',
        mode: 'incremental',
        source_object: 'dbo.card_transactions',
        cursor_column: 'updated_at',
        schedule_cron: '0 * * * *',
        freshness_sla: 'PT4H',
        retry_policy: 'backoff_3',
        on_schema_change: 'accept_new_columns',
        on_invalid_record: 'quarantine_30d',
        dlq_retention_days: 30,
      },
    });
  });
});
