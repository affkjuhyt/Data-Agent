import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  readPipelineDetail,
  sendPrepMessage,
  readWriteback,
  type PipelineDetailWire,
  type WritebackWire,
} from './dataPrep';

describe('readPipelineDetail', () => {
  it('adapts the API pipeline and latest run into the pipeline screen shape', () => {
    const pipeline = readPipelineDetail(
      {
        id: 'pipeline-1',
        data_space_id: 'space-1',
        name: 'Chuẩn hoá dư nợ',
        owner_id: 'user-1',
        input_dataset_ids: ['dataset-1'],
        target_dataset_id: 'dataset-2',
        steps: [
          {
            kind: 'filter',
            description: 'Bỏ khoản vay đã tất toán',
            language: 'sql',
            code: "WHERE status <> 'closed'",
          },
        ],
        scope: 'data_space',
        status: 'draft',
        cron: null,
        work_calendar_id: null,
        trigger_on_job_id: null,
        depends_on_pipeline_ids: [],
        no_overlap: true,
        on_failure: 'notify',
        created_at: '2026-10-13T00:00:00Z',
        updated_at: '2026-10-13T00:00:00Z',
        last_run: {
          id: 'run-1',
          pipeline_id: 'pipeline-1',
          mode: 'preview',
          sample_rows: 1000,
          rows_in: 1200,
          rows_out: 1150,
          rows_dropped: 50,
          totals_check: { matched: true, columns: [], step_rows: [1150] },
          status: 'succeeded',
          workflow_id: null,
          started_at: '2026-10-13T00:01:00Z',
          finished_at: '2026-10-13T00:02:00Z',
          created_at: '2026-10-13T00:01:00Z',
          error: null,
        },
      } as PipelineDetailWire,
      new Map([
        ['dataset-1', 'raw.loans'],
        ['dataset-2', 'prep.loans_clean'],
      ]),
    );

    expect(pipeline.input_table).toBe('raw.loans');
    // A sampled preview's rows_in is the sample: it does not say how many rows the input has.
    expect(pipeline.input_rows).toBeNull();
    expect(pipeline.preview_sample).toBe(1000);
    expect(pipeline.output_table).toBe('prep.loans_clean');
    expect(pipeline.steps).toEqual([
      {
        seq: 1,
        title: 'Bỏ khoản vay đã tất toán',
        sql: "WHERE status <> 'closed'",
        // From the last run: what it read, and the rows after the step.
        rows_in: 1200,
        rows_out: 1150,
        is_new: false,
        kind_label: 'filter',
      },
    ]);
    expect(pipeline.messages).toEqual([]);
  });

  it('keeps canonical pipeline display details when the API includes step and conversation metadata', () => {
    const pipeline = readPipelineDetail(
      {
        id: 'pipeline-2',
        data_space_id: 'space-1',
        name: 'Lọc giao dịch lỗi',
        owner_id: 'user-1',
        input_dataset_ids: ['dataset-1'],
        target_dataset_id: null,
        steps: [
          {
            kind: 'filter',
            description: 'Bỏ giao dịch thử',
            language: 'sql',
            code: 'WHERE is_test = false',
            rows_in: 200,
            rows_out: 180,
            is_new: true,
            kind_label: 'lọc',
          },
        ],
        messages: [{ id: 'm1', author: 'agent', text: 'Đã thêm bước lọc.' }],
        scope: 'data_space',
        status: 'draft',
        cron: null,
        work_calendar_id: null,
        trigger_on_job_id: null,
        depends_on_pipeline_ids: [],
        no_overlap: true,
        on_failure: 'notify',
        created_at: '2026-10-13T00:00:00Z',
        updated_at: '2026-10-13T00:00:00Z',
        last_run: null,
      } as unknown as PipelineDetailWire & { messages: unknown[] },
      new Map([['dataset-1', 'raw.transactions']]),
    );

    expect(pipeline.steps[0]).toMatchObject({
      rows_in: 200,
      rows_out: 180,
      is_new: true,
      kind_label: 'lọc',
    });
    expect(pipeline.messages).toEqual([{ id: 'm1', author: 'agent', text: 'Đã thêm bước lọc.' }]);
  });
});

describe('readWriteback', () => {
  it('adapts the API writeback view and diff summary into the review screen shape', () => {
    const writeback = readWriteback({
      id: 'writeback-1',
      data_space_id: 'space-1',
      source_id: 'source-1',
      target_table: 'crm_staging.credit_tier',
      from_dataset_id: 'dataset-1',
      requested_by: 'user-1',
      diff_summary: {
        row_count: 1240,
        changes: [{ subject: 'KH ••••4821', before: 'B', after: 'C', reason: 'Quá hạn' }],
        checks: [{ ok: true, text: '1.240 dòng hợp lệ' }],
      },
      diff_ref: null,
      deletes: 0,
      write_window: null,
      snapshot_ref: null,
      approved_by: null,
      proposal_id: null,
      status: 'pending_approval',
      executed_at: null,
      totals_check: { ok: false, text: 'Cần rà soát tổng' },
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
    } as WritebackWire);

    expect(writeback).toMatchObject({
      id: 'writeback-1',
      code: 'writeback-1',
      title: 'Ghi ngược vào crm_staging.credit_tier',
      target_table: 'crm_staging.credit_tier',
      row_count: 1240,
      changes: [{ subject: 'KH ••••4821', before: 'B', after: 'C', reason: 'Quá hạn' }],
      checks: [
        { ok: true, text: '1.240 dòng hợp lệ' },
        { ok: false, text: 'Cần rà soát tổng' },
      ],
    });
  });
});

describe('sendPrepMessage', () => {
  const server = setupServer();
  beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());

  it('reads the pipeline the api answers like getPipeline, inputs and steps named', async () => {
    server.use(
      http.post('*/v1/pipelines/pipeline-1/messages', () =>
        HttpResponse.json({
          id: 'pipeline-1',
          data_space_id: 'space-1',
          name: 'Chuẩn hoá dư nợ',
          owner_id: 'user-1',
          input_dataset_ids: ['dataset-1'],
          target_dataset_id: null,
          steps: [{ kind: 'filter', description: 'Bỏ khoản vay đã tất toán', code: 'WHERE 1=1' }],
          scope: 'data_space',
          status: 'draft',
          cron: null,
          work_calendar_id: null,
          trigger_on_job_id: null,
          depends_on_pipeline_ids: [],
          no_overlap: true,
          on_failure: 'notify',
          created_at: '2026-10-13T00:00:00Z',
          updated_at: '2026-10-13T00:00:00Z',
          last_run: null,
        }),
      ),
      http.get('*/v1/datasets', () =>
        HttpResponse.json({
          items: [{ id: 'dataset-1', schema_name: 'raw', table_name: 'loans' }],
          next_page_token: '',
        }),
      ),
    );
    const pipeline = await sendPrepMessage('pipeline-1', 'Bỏ khoản vay đã tất toán');
    expect(pipeline.input_table).toBe('raw.loans');
    expect(pipeline.steps[0]).toMatchObject({
      title: 'Bỏ khoản vay đã tất toán',
      sql: 'WHERE 1=1',
    });
  });
});

describe('formatSql', () => {
  it('lays a statement out clause by clause without touching a string', async () => {
    const { formatSql } = await import('./dataPrep');
    expect((await formatSql("select a from t where name = 'from here'")).sql).toBe(
      "SELECT\n  a\nFROM\n  t\nWHERE\n  name = 'from here'",
    );
  });

  it('gives back what it cannot read as written', async () => {
    const { formatSql } = await import('./dataPrep');
    expect((await formatSql('  select ((( from  ')).sql).toBe('select ((( from');
  });
});
