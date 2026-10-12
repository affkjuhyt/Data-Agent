/**
 * Pipelines as the api sends them (PipelineView): inputs named from the datasets, the schedule
 * said from cron or what it follows, and a new pipeline created on the dataset its table names.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { createPipeline, listPipelines } from './dataPrep';

const at = '2026-10-12T08:00:00Z';
const pipeline = (extra: object = {}) => ({
  id: 'pl-1',
  name: 'Làm sạch khoản vay',
  steps: [{}, {}],
  input_dataset_ids: ['d-1'],
  target_dataset_id: null,
  status: 'active',
  cron: '0 4 * * *',
  depends_on_pipeline_ids: [],
  trigger_on_job_id: null,
  created_at: at,
  updated_at: at,
  ...extra,
});
let created: unknown;
const server = setupServer(
  http.get('*/v1/pipelines', () => HttpResponse.json({ items: [pipeline()], next_page_token: '' })),
  http.get('*/v1/datasets', ({ request }) => {
    const q = new URL(request.url).searchParams.get('q');
    return HttpResponse.json({
      items: q === 'missing' ? [] : [{ id: 'd-1', schema_name: 'raw', table_name: 'ld_loans' }],
      next_page_token: '',
    });
  }),
  http.post('*/v1/pipelines', async ({ request }) => {
    created = await request.json();
    return HttpResponse.json(pipeline({ id: 'pl-2', status: 'draft', cron: null, steps: [] }), {
      status: 201,
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('pipelines from the api', () => {
  it('names the inputs and says the schedule', async () => {
    const [p] = (await listPipelines()).items;
    expect(p).toMatchObject({
      inputs: ['raw.ld_loans'],
      step_count: 2,
      schedule_text: 'Theo lịch 0 4 * * *',
      status: 'active',
      last_run: null,
    });
  });

  it('creates a pipeline on the dataset its table names, and refuses an unknown table', async () => {
    const p = await createPipeline({ name: 'Khoản vay', input_table: 'raw.ld_loans' });
    expect(created).toEqual({ name: 'Khoản vay', input_dataset_ids: ['d-1'] });
    expect(p).toMatchObject({
      status: 'draft',
      inputs: ['raw.ld_loans'],
      schedule_text: 'Chạy tay',
    });
    await expect(createPipeline({ name: 'x', input_table: 'raw.missing' })).rejects.toMatchObject({
      status: 422,
    });
  });
});
