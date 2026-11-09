import { http, HttpResponse } from 'msw';
import type { ErrorBookDetailWire, ErrorBookViewWire } from '../lib/v1/errorBook';

/*
 * Error Book (P07, "P07 · Error Book" 2419:15027) for the dev mock, in the api's own shapes
 * (ErrorBookView, ErrorBookDetail) so the screen goes through the same reader as against the api:
 * EB-0042 fixed with its regression in CI, two open entries, one fixed without regression yet,
 * one dismissed.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const daysAgo = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();
const SPACE = 'ds-fuel';
const HANH = 'u-hanh';

type Entry = ErrorBookDetailWire;

function entry(
  e: Partial<Entry> & Pick<Entry, 'id' | 'entry_no' | 'title' | 'status' | 'source_kind'>,
): Entry {
  const created = e.created_at ?? daysAgo(1);
  return {
    data_space_id: SPACE,
    data_product_id: 'dp-fuel',
    feedback_id: null,
    answer_id: null,
    receipt_id: null,
    hitl_request_id: null,
    replay_id: null,
    accuracy_log_id: null,
    forecast_detail: null,
    lint_finding_id: null,
    detail: {},
    severity: 'medium',
    error_kind: null,
    root_cause: null,
    assignee_id: HANH,
    fix_kind: null,
    fix_proposal_id: null,
    fix_ref: null,
    fixed_by: null,
    fixed_at: null,
    eval_item_id: null,
    regression_run_id: null,
    dismiss_reason: null,
    duplicate_of_id: null,
    reopened_count: 0,
    eval_item: null,
    latest_result: null,
    created_at: created,
    updated_at: created,
    ...e,
  };
}

const evalItem = (id: string, entryId: string, asOf: string, at: string) => ({
  id,
  eval_set_id: 'es-regression',
  category: 'regression',
  origin: 'error_book',
  origin_ref: entryId,
  as_of: asOf,
  input: {},
  expected: {},
  tolerance: null,
  status: 'active',
  created_at: at,
  updated_at: at,
});

function entriesFixture(): Entry[] {
  return [
    entry({
      id: 'eb-0042',
      entry_no: 'EB-0042',
      title: 'Mức giảm E5RON92 kỳ 02/07 ghi 1.155, công bố 1.055',
      status: 'regression_added',
      source_kind: 'user_report',
      feedback_id: 'fb-0702',
      answer_id: 'ans-fuel-0702',
      receipt_id: 'bn-20260702-000190',
      replay_id: 'rp-0703',
      error_kind: 'formula',
      created_at: daysAgo(9),
      detail: { actual: '1.155 đ/lít', expected: '1.055 đ/lít', as_of: '2026-07-02' },
      root_cause:
        'Phép tính F2 lấy kỳ liền trước theo tuần lịch (18/06) thay vì kỳ điều hành gần nhất (25/06).',
      fix_kind: 'metric_version',
      fix_proposal_id: 'pr-0318',
      fixed_by: HANH,
      fixed_at: daysAgo(2),
      eval_item_id: 'ev-eb-0042',
      regression_run_id: 'run-nightly-1107',
      eval_item: evalItem('ev-eb-0042', 'eb-0042', '2026-07-02', daysAgo(1)),
      latest_result: {
        id: 'er-eb-0042',
        eval_item_id: 'ev-eb-0042',
        actual: { value: '1.055 đ/lít' },
        passed: true,
        value_ok: true,
        behavior_ok: null,
        understanding_ok: null,
        cause: null,
        changed_from_baseline: false,
        reviewed_by: null,
        created_at: daysAgo(0),
      },
    }),
    // The two missed fuel-forecast periods the forecast ledger (P15 Sổ dự báo) links to.
    entry({
      id: 'eb-0031',
      entry_no: 'EB-0031',
      title: 'Giá trần E5RON92 kỳ 11/06 ngoài khoảng 95% (minh hoạ)',
      status: 'fixed',
      source_kind: 'forecast_miss',
      accuracy_log_id: 'acc-0611',
      forecast_detail: { period: '2026-06-11', point: 20480, lo95: 20040, hi95: 20900, actual: 20020 },
      error_kind: 'model',
      created_at: daysAgo(30),
      root_cause: 'Quỹ bình ổn chi 300 đ/lít ngoài dự kiến; mô hình chưa có biến cho quyết định chi Quỹ.',
      fix_kind: 'model_config',
      fix_ref: 'thêm xác suất Quỹ vào mô hình',
      fixed_by: HANH,
      fixed_at: daysAgo(22),
    }),
    entry({
      id: 'eb-0027',
      entry_no: 'EB-0027',
      title: 'Giá trần E5RON92 kỳ 09/04 lệch hơn 300 đ/lít (minh hoạ)',
      status: 'fixed',
      source_kind: 'forecast_miss',
      accuracy_log_id: 'acc-0409',
      forecast_detail: { period: '2026-04-09' },
      error_kind: 'model',
      created_at: daysAgo(90),
      root_cause: 'Giá thế giới tăng mạnh trong hai ngày cuối kỳ, sau lần chạy dự báo.',
      fix_kind: 'model_config',
      fix_ref: 'thêm biến giá thế giới ngày cuối kỳ',
      fixed_by: HANH,
      fixed_at: daysAgo(76),
    }),
    entry({
      id: 'eb-0043',
      entry_no: 'EB-0043',
      title: 'Giá trần RON95-III kỳ 09/07 ngoài khoảng 95% (minh hoạ)',
      status: 'open',
      source_kind: 'forecast_miss',
      accuracy_log_id: 'acc-0709',
      forecast_detail: { period: '2026-07-09', point: 21480, lo95: 20900, hi95: 21900, actual: 22310 },
      error_kind: 'model',
      created_at: daysAgo(1),
      assignee_id: 'u-ha',
    }),
    entry({
      id: 'eb-0045',
      entry_no: 'EB-0045',
      title: 'Bản tin 25/06 ghi “đã cập nhật” khi dataset dừng ở kỳ 18/06',
      status: 'open',
      source_kind: 'number_check',
      error_kind: 'freshness',
      created_at: daysAgo(15),
      reopened_count: 1,
    }),
    entry({
      id: 'eb-0040',
      entry_no: 'EB-0040',
      title: '“Lãi suất huy động” ánh xạ sai sang kỳ hạn 12 tháng',
      status: 'fixed',
      source_kind: 'reviewer_reject',
      hitl_request_id: 'hitl-0620',
      error_kind: 'synonym',
      data_product_id: 'dp-deposit-rate',
      assignee_id: 'u-quan',
      created_at: daysAgo(21),
      root_cause: 'Đồng nghĩa mặc định trỏ kỳ hạn 12 tháng thay vì hỏi lại kỳ hạn.',
      fix_kind: 'synonym',
      fix_proposal_id: 'pr-0301',
      fixed_by: 'u-quan',
      fixed_at: daysAgo(13),
      eval_item_id: 'ev-eb-0040',
      eval_item: evalItem('ev-eb-0040', 'eb-0040', '2026-06-20', daysAgo(13)),
    }),
    entry({
      id: 'eb-0038',
      entry_no: 'EB-0038',
      title: 'Câu hỏi giá tại cửa hàng Petrolimex Láng Hạ',
      status: 'dismissed',
      source_kind: 'user_report',
      feedback_id: 'fb-0611',
      error_kind: 'retrieval',
      data_product_id: null,
      created_at: daysAgo(30),
      updated_at: daysAgo(29),
      dismiss_reason: 'Ngoài phạm vi data product; câu trả lời đã mang nhãn không có biên nhận.',
    }),
  ];
}

/** What the list and the writes answer: the entry without the eval item and its result. */
const view = ({ eval_item: _item, latest_result: _result, ...e }: Entry): ErrorBookViewWire => e;

export function createErrorBookHandlers() {
  let entries = entriesFixture();
  const update = (id: string, patch: Partial<Entry>) => {
    const now = new Date().toISOString();
    entries = entries.map((e) => (e.id === id ? { ...e, ...patch, updated_at: now } : e));
    return entries.find((e) => e.id === id)!;
  };
  return [
    http.get('*/v1/error-book-entries', ({ request }) => {
      const p = new URL(request.url).searchParams;
      const items = entries
        .filter((e) => !p.get('status') || e.status === p.get('status'))
        .filter((e) => !p.get('error_kind') || e.error_kind === p.get('error_kind'))
        .filter((e) => !p.get('source_kind') || e.source_kind === p.get('source_kind'))
        .map(view);
      return HttpResponse.json({ items, next_page_token: '' });
    }),
    http.get('*/v1/error-book-entries/:id', ({ params }) => {
      const e = entries.find((x) => x.id === params.id);
      return e ? HttpResponse.json(e) : problem(404, 'not_found', 'Không tìm thấy mục.');
    }),
    http.patch('*/v1/error-book-entries/:id', async ({ params, request }) => {
      const e = entries.find((x) => x.id === params.id);
      if (!e) return problem(404, 'not_found', 'Không tìm thấy mục.');
      if (e.status === 'dismissed') return problem(409, 'conflict', 'Mục đã bỏ qua.');
      return HttpResponse.json(view(update(e.id, (await request.json()) as Partial<Entry>)));
    }),
    http.post('*/v1/error-book-entries', async ({ request }) => {
      const body = (await request.json()) as { source_kind: string; title: string };
      if (!body.title?.trim()) return problem(422, 'validation_failed', 'Nhập tiêu đề mục.');
      const n = 46 + entries.length;
      const created = entry({
        id: `eb-00${n}`,
        entry_no: `EB-00${n}`,
        title: body.title,
        status: 'open',
        source_kind: body.source_kind,
        created_at: new Date().toISOString(),
      });
      entries = [created, ...entries];
      return HttpResponse.json(view(created), { status: 201 });
    }),
    http.post('*/v1/error-book-entries/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const e = entries.find((x) => x.id === id);
      if (!e) return problem(404, 'not_found', 'Không tìm thấy mục.');
      const body = (await request.json().catch(() => ({}))) as Record<string, string>;
      if (verb === 'mark-fixed') {
        if (e.status !== 'open') return problem(409, 'invalid_state', `The entry is ${e.status}.`);
        if (!e.error_kind || !e.root_cause?.trim())
          return problem(
            422,
            'business_rule_violated',
            'Cần loại lỗi và nguyên nhân gốc trước khi đánh dấu đã sửa (BR-179).',
          );
        if (!body.fix_kind)
          return problem(422, 'validation_failed', 'fix_kind: cho biết đã sửa gì.');
        if (!body.fix_proposal_id && !body.fix_ref)
          return problem(
            422,
            'business_rule_violated',
            'Cần cách sửa: đề xuất đã chấp nhận hoặc tham chiếu (BR-179).',
          );
        return HttpResponse.json(
          view(
            update(id, {
              status: 'fixed',
              fix_kind: body.fix_kind,
              fix_proposal_id: body.fix_proposal_id ?? null,
              fix_ref: body.fix_ref ?? null,
              fixed_by: HANH,
              fixed_at: new Date().toISOString(),
            }),
          ),
        );
      }
      if (verb === 'dismiss') {
        if (e.status !== 'open') return problem(409, 'conflict', 'Chỉ bỏ qua được mục đang mở.');
        if (!body.reason?.trim() && !body.duplicate_of_id)
          return problem(422, 'validation_failed', 'Ghi lý do hoặc chọn mục gốc.');
        return HttpResponse.json(
          view(
            update(id, {
              status: 'dismissed',
              dismiss_reason: body.reason ?? null,
              duplicate_of_id: body.duplicate_of_id ?? null,
            }),
          ),
        );
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
