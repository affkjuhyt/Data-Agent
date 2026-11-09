import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  getErrorBookEntry,
  listErrorBookEntries,
  markEntryFixed,
  readErrorBookEntry,
  type ErrorBookDetailWire,
} from './errorBook';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const ENTRY = '01930000-0000-7000-8000-00000000eb01';
const PROPOSAL = '01930000-0000-7000-8000-0000000000aa';

// What GET /v1/error-book-entries/{id} answers (ErrorBookDetail): ids, no display fields.
const wire = (over: Partial<ErrorBookDetailWire> = {}): ErrorBookDetailWire => ({
  id: ENTRY,
  entry_no: 'EB-0007',
  data_space_id: '01930000-0000-7000-8000-0000000000d5',
  data_product_id: null,
  source_kind: 'forecast_miss',
  feedback_id: null,
  answer_id: null,
  receipt_id: null,
  hitl_request_id: null,
  replay_id: null,
  accuracy_log_id: '01930000-0000-7000-8000-0000000000a1',
  forecast_detail: { period: '2026-07-09', point: 21480, actual: 22310, lo95: 20900, hi95: 21900 },
  lint_finding_id: null,
  title: 'Giá trần RON95-III kỳ 09/07 ngoài khoảng 95%',
  detail: {},
  severity: 'medium',
  error_kind: 'model',
  root_cause: 'Quỹ bình ổn chi ngoài dự kiến',
  assignee_id: '01930000-0000-7000-8000-0000000000u1',
  fix_kind: 'model_config',
  fix_proposal_id: null,
  fix_ref: 'commit 3c3a1ae3',
  fixed_by: '01930000-0000-7000-8000-0000000000u1',
  fixed_at: '2026-07-12T03:00:00Z',
  eval_item_id: null,
  regression_run_id: null,
  status: 'fixed',
  dismiss_reason: null,
  duplicate_of_id: null,
  reopened_count: 0,
  created_at: '2026-07-10T03:00:00Z',
  updated_at: '2026-07-12T03:00:00Z',
  ...over,
});

describe('readErrorBookEntry', () => {
  it('reads the api entry into the screen shape, with a timeline derived from its dates', () => {
    const entry = readErrorBookEntry(wire());
    expect(entry.code).toBe('EB-0007');
    expect(entry.timeline.map((t) => t.label)).toEqual(['Mở', 'Đã sửa']);
    expect(entry.wrong?.note).toMatch(/khoảng 95%/);
    expect(entry.right).not.toBeNull();
    expect(entry.fix).toMatchObject({ kind: 'model_config', label: 'Cấu hình model' });
    expect(entry.fix?.ref).toBe('commit 3c3a1ae3');
    expect(entry.regression).toBeNull();
    expect(entry.receipt).toBeNull();
    expect(entry.assignee_name).toBeNull();
  });

  it('reads the regression case and its last CI result when the entry has one', () => {
    const entry = readErrorBookEntry(
      wire({
        status: 'regression_added',
        eval_item_id: '01930000-0000-7000-8000-0000000000e1',
        regression_run_id: '01930000-0000-7000-8000-0000000000r1',
        eval_item: {
          id: '01930000-0000-7000-8000-0000000000e1',
          eval_set_id: '01930000-0000-7000-8000-0000000000s1',
          category: 'regression',
          origin: 'error_book',
          origin_ref: ENTRY,
          as_of: '2026-07-09',
          input: {},
          expected: {},
          tolerance: null,
          status: 'active',
          created_at: '2026-07-13T03:00:00Z',
          updated_at: '2026-07-13T03:00:00Z',
        },
        latest_result: {
          id: '01930000-0000-7000-8000-0000000000x1',
          eval_item_id: '01930000-0000-7000-8000-0000000000e1',
          actual: {},
          passed: true,
          value_ok: true,
          behavior_ok: null,
          understanding_ok: null,
          cause: null,
          changed_from_baseline: false,
          reviewed_by: null,
          created_at: '2026-07-14T03:00:00Z',
        },
      }),
    );
    expect(entry.regression?.passed).toBe(true);
    expect(entry.timeline.map((t) => t.label)).toEqual(['Mở', 'Đã sửa', 'Đã có hồi quy']);
  });

  it('reads a write answer (ErrorBookView, no eval item) without failing', () => {
    const {
      eval_item: _item,
      latest_result: _result,
      ...view
    } = wire({
      status: 'open',
      fixed_at: null,
      fix_kind: null,
    });
    const entry = readErrorBookEntry(view);
    expect(entry.timeline).toEqual([{ label: 'Mở', at: expect.any(String) }]);
  });
});

describe('error book api', () => {
  it('asks for the whole list with page_size, which the api reads', async () => {
    let query: URLSearchParams | undefined;
    server.use(
      http.get('*/v1/error-book-entries', ({ request }) => {
        query = new URL(request.url).searchParams;
        return HttpResponse.json({ items: [wire()], next_page_token: '' });
      }),
    );
    const page = await listErrorBookEntries();
    expect(query?.get('page_size')).toBe('500');
    expect(query?.has('limit')).toBe(false);
    expect(page.items[0].code).toBe('EB-0007');
  });

  it('reads one entry through the mapper', async () => {
    server.use(http.get(`*/v1/error-book-entries/${ENTRY}`, () => HttpResponse.json(wire())));
    expect((await getErrorBookEntry(ENTRY)).timeline.length).toBe(2);
  });

  it('turns an unknown proposal id in the fix reference into a validation message', async () => {
    server.use(
      http.get(`*/v1/proposals/${PROPOSAL}`, () =>
        HttpResponse.json(
          { title: 'not_found', status: 404, code: 'not_found', detail: 'Not found' },
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    await expect(markEntryFixed(ENTRY, 'metric_version', PROPOSAL)).rejects.toMatchObject({
      status: 422,
      code: 'validation_failed',
      message: expect.stringMatching(/Không tìm thấy đề xuất/),
    });
  });
});
