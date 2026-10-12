import { describe, expect, it } from 'vitest';
import { readPipelineDetail, type PipelineDetailWire } from './dataPrep';

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
            code: 'WHERE status <> \'closed\'',
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
          totals_check: null,
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
    expect(pipeline.input_rows).toBe(1200);
    expect(pipeline.preview_sample).toBe(1000);
    expect(pipeline.output_table).toBe('prep.loans_clean');
    expect(pipeline.steps).toEqual([
      {
        seq: 1,
        title: 'Bỏ khoản vay đã tất toán',
        sql: "WHERE status <> 'closed'",
        rows_in: 0,
        rows_out: 0,
        is_new: false,
        kind_label: 'filter',
      },
    ]);
    expect(pipeline.messages).toEqual([]);
  });
});
