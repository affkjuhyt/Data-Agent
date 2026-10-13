/**
 * Job nạp · Thiết lập where the PROPOSAL candidates are not served: the tables of ingest sources
 * that no job reads, their cursor columns from the dataset, and the job created on the source.
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
  http.get('*/v1/datasets/:id', () =>
    HttpResponse.json({
      id: 'd-1',
      columns: [
        { name: 'txn_id', data_type: 'VARCHAR2(16)', normalized_type: 'text' },
        { name: 'txn_date', data_type: 'DATE', normalized_type: 'date' },
        { name: 'updated_at', data_type: 'TIMESTAMP', normalized_type: 'timestamp' },
      ],
    }),
  ),
  http.post('*/v1/sources/:id/jobs', async ({ params, request }) => {
    created = { source: params.id, body: await request.json() };
    return HttpResponse.json({ id: 'j-2' }, { status: 201 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('job candidates from the api', () => {
  it('lists the ingest tables no job reads, timestamps first as cursors', async () => {
    const { items } = await listJobCandidates();
    expect(items).toEqual([
      {
        dataset_id: 'd-1',
        source_id: 's-core',
        source_object: 'dbo.card_transactions',
        table_name: 'card_transactions',
        source_name: 'Core banking',
        cursor_columns: [
          { name: 'updated_at', data_type: 'TIMESTAMP', hint: null },
          { name: 'txn_date', data_type: 'DATE', hint: null },
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
        dlq_retention_days: 30,
      },
    });
  });
});
