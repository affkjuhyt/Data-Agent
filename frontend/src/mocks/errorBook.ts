import { http, HttpResponse } from 'msw';
import type { ErrorBookEntry } from '../lib/v1/errorBook';

/*
 * Error Book (P07, "P07 · Error Book" 2419:15027) for the dev mock: EB-0042 fixed with its
 * regression in CI, two open entries, one fixed without regression yet, one dismissed.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const daysAgo = (d: number) => new Date(Date.now() - d * 86_400_000).toISOString();

function entry(
  e: Partial<ErrorBookEntry> &
    Pick<ErrorBookEntry, 'id' | 'code' | 'title' | 'status' | 'source_kind'>,
): ErrorBookEntry {
  return {
    error_kind: null,
    data_product_name: 'Giá xăng dầu trong nước',
    assignee_name: 'Nguyễn Thị Hạnh',
    created_at: daysAgo(1),
    wrong: null,
    right: null,
    receipt: null,
    root_cause: null,
    fix: null,
    regression: null,
    timeline: [],
    history_text: '',
    pack_candidate: null,
    dismissed_reason: null,
    ...e,
  };
}

function entriesFixture(): ErrorBookEntry[] {
  return [
    entry({
      id: 'eb-0042',
      code: 'EB-0042',
      title: 'Mức giảm E5RON92 kỳ 02/07 ghi 1.155, công bố 1.055',
      status: 'regression_added',
      source_kind: 'user_report',
      error_kind: 'formula',
      created_at: daysAgo(9),
      wrong: { value: '1.155 đ/lít', note: 'biên nhận BN-20260702-000190' },
      right: { value: '1.055 đ/lít', note: 'Bộ Công Thương công bố 02/07/2026' },
      receipt: {
        id: 'bn-20260702-000190',
        code: 'BN-20260702-000190',
        answer_id: 'ans-fuel-0702',
        replay_text:
          'chạy lại 03/07 ra cùng 1.155 → số tái lập, lỗi không nằm ở câu chữ của mô hình',
      },
      root_cause:
        'Phép tính F2 lấy kỳ liền trước theo tuần lịch (18/06) thay vì kỳ điều hành gần nhất (25/06).',
      fix: {
        kind: 'proposal',
        label: 'Sửa chỉ số muc_thay_doi_gia',
        ref: 'PR-0318',
        proposal_status: 'accepted',
      },
      regression: {
        code: 'EV-EB-0042',
        text: 'as_of 02/07/2026 · đáp án 1.055 đ/lít · câu hỏi đã che dữ liệu cá nhân',
        ci_text: 'Lượt CI nightly 11/07 đạt',
        passed: true,
      },
      timeline: [
        { label: 'Mở', at: '02/07' },
        { label: 'Đã sửa', at: '09/07' },
        { label: 'Đã có hồi quy', at: '11/07' },
      ],
      history_text:
        '02/07 Vũ Lan báo sai · 02/07 Nguyễn Thị Hạnh xác nhận · 09/07 đã sửa · 11/07 đã có hồi quy · mở lại 0 lần',
      pack_candidate: { pack_id: 'pack-energy', pack_name: 'Năng lượng · giá điều hành' },
    }),
    // The two missed fuel-forecast periods the forecast ledger (P15 Sổ dự báo) links to.
    entry({
      id: 'eb-0031',
      code: 'EB-0031',
      title: 'Giá trần E5RON92 kỳ 11/06 ngoài khoảng 95% (minh hoạ)',
      status: 'fixed',
      source_kind: 'forecast_miss',
      error_kind: 'model',
      created_at: daysAgo(30),
      wrong: { value: '20.480 đ/lít', note: 'dự báo trung vị · khoảng 95% 20.040–20.900' },
      right: { value: '20.020 đ/lít', note: 'Bộ Công Thương công bố 11/06/2026' },
      root_cause: 'Quỹ bình ổn chi 300 đ/lít ngoài dự kiến; mô hình chưa có biến cho quyết định chi Quỹ.',
      timeline: [
        { label: 'Mở', at: '12/06' },
        { label: 'Đã sửa', at: '20/06' },
      ],
      history_text: '12/06 Agent Dự báo ghi lệch · 20/06 thêm xác suất Quỹ vào mô hình',
    }),
    entry({
      id: 'eb-0027',
      code: 'EB-0027',
      title: 'Giá trần E5RON92 kỳ 09/04 lệch hơn 300 đ/lít (minh hoạ)',
      status: 'fixed',
      source_kind: 'forecast_miss',
      error_kind: 'model',
      created_at: daysAgo(90),
      right: { value: 'theo công bố kỳ 09/04/2026', note: 'Bộ Công Thương' },
      root_cause: 'Giá thế giới tăng mạnh trong hai ngày cuối kỳ, sau lần chạy dự báo.',
      timeline: [
        { label: 'Mở', at: '10/04' },
        { label: 'Đã sửa', at: '24/04' },
      ],
      history_text: '10/04 Agent Dự báo ghi lệch · 24/04 thêm biến giá thế giới ngày cuối kỳ',
    }),
    entry({
      id: 'eb-0043',
      code: 'EB-0043',
      title: 'Giá trần RON95-III kỳ 09/07 ngoài khoảng 95% (minh hoạ)',
      status: 'open',
      source_kind: 'forecast_miss',
      error_kind: 'model',
      created_at: daysAgo(1),
      assignee_name: 'Lê Thu Hà',
      wrong: { value: '21.480 đ/lít', note: 'dự báo trung vị · khoảng 95% 20.900–21.900' },
      right: { value: '22.310 đ/lít', note: 'Bộ Công Thương công bố 09/07/2026' },
      timeline: [{ label: 'Mở', at: '10/07' }],
      history_text: '10/07 Agent Dự báo ghi lệch · giao Lê Thu Hà',
    }),
    entry({
      id: 'eb-0045',
      code: 'EB-0045',
      title: 'Bản tin 25/06 ghi “đã cập nhật” khi dataset dừng ở kỳ 18/06',
      status: 'open',
      source_kind: 'number_check',
      error_kind: 'freshness',
      created_at: daysAgo(15),
      timeline: [{ label: 'Mở', at: '25/06' }],
      history_text: '25/06 Verifier bỏ câu · gộp 3 lần trong 7 ngày',
    }),
    entry({
      id: 'eb-0040',
      code: 'EB-0040',
      title: '“Lãi suất huy động” ánh xạ sai sang kỳ hạn 12 tháng',
      status: 'fixed',
      source_kind: 'reviewer_reject',
      error_kind: 'synonym',
      data_product_name: 'Lãi suất huy động niêm yết',
      assignee_name: 'Trần Minh Quân',
      created_at: daysAgo(21),
      root_cause: 'Đồng nghĩa mặc định trỏ kỳ hạn 12 tháng thay vì hỏi lại kỳ hạn.',
      fix: {
        kind: 'proposal',
        label: 'Sửa đồng nghĩa “lãi suất huy động”',
        ref: 'PR-0301',
        proposal_status: 'accepted',
      },
      regression: {
        code: 'EV-EB-0040',
        text: 'chờ lượt CI kế tiếp',
        ci_text: 'Chưa chạy',
        passed: null,
      },
      timeline: [
        { label: 'Mở', at: '20/06' },
        { label: 'Đã sửa', at: '28/06' },
      ],
      history_text: '20/06 người duyệt bác · 28/06 đã sửa',
    }),
    entry({
      id: 'eb-0038',
      code: 'EB-0038',
      title: 'Câu hỏi giá tại cửa hàng Petrolimex Láng Hạ',
      status: 'dismissed',
      source_kind: 'user_report',
      error_kind: 'retrieval',
      data_product_name: null,
      created_at: daysAgo(30),
      dismissed_reason: 'Ngoài phạm vi data product; câu trả lời đã mang nhãn không có biên nhận.',
      timeline: [
        { label: 'Mở', at: '11/06' },
        { label: 'Bỏ qua', at: '12/06' },
      ],
      history_text: '11/06 báo sai · 12/06 bỏ qua',
    }),
  ];
}

const summary = (e: ErrorBookEntry) => ({
  id: e.id,
  code: e.code,
  title: e.title,
  status: e.status,
  error_kind: e.error_kind,
  source_kind: e.source_kind,
  data_product_name: e.data_product_name,
  assignee_name: e.assignee_name,
  created_at: e.created_at,
});

export function createErrorBookHandlers() {
  let entries = entriesFixture();
  const update = (id: string, patch: Partial<ErrorBookEntry>) => {
    entries = entries.map((e) => (e.id === id ? { ...e, ...patch } : e));
    return entries.find((e) => e.id === id)!;
  };
  return [
    http.get('*/v1/error-book-entries', ({ request }) => {
      const p = new URL(request.url).searchParams;
      const items = entries
        .filter((e) => !p.get('status') || e.status === p.get('status'))
        .filter((e) => !p.get('error_kind') || e.error_kind === p.get('error_kind'))
        .filter((e) => !p.get('source_kind') || e.source_kind === p.get('source_kind'))
        .map(summary);
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
      return HttpResponse.json(update(e.id, (await request.json()) as Partial<ErrorBookEntry>));
    }),
    http.post('*/v1/error-book-entries', async ({ request }) => {
      const body = (await request.json()) as {
        source_kind: ErrorBookEntry['source_kind'];
        title: string;
      };
      if (!body.title?.trim()) return problem(422, 'validation_failed', 'Nhập tiêu đề mục.');
      const n = 46 + entries.length;
      const created = entry({
        id: `eb-00${n}`,
        code: `EB-00${n}`,
        title: body.title,
        status: 'open',
        source_kind: body.source_kind,
        created_at: new Date().toISOString(),
        timeline: [{ label: 'Mở', at: 'hôm nay' }],
      });
      entries = [created, ...entries];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.post('*/v1/error-book-entries/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const e = entries.find((x) => x.id === id);
      if (!e) return problem(404, 'not_found', 'Không tìm thấy mục.');
      const body = (await request.json().catch(() => ({}))) as Record<string, string>;
      if (verb === 'mark-fixed') {
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
          update(id, {
            status: 'fixed',
            fix: {
              kind: body.fix_proposal_id ? 'proposal' : 'commit',
              label: body.fix_proposal_id ?? body.fix_ref,
              ref: body.fix_proposal_id ?? body.fix_ref,
              proposal_status: body.fix_proposal_id ? 'accepted' : null,
            },
            regression: {
              code: `EV-${e.code}`,
              text: 'chờ lượt CI kế tiếp',
              ci_text: 'Chưa chạy',
              passed: null,
            },
            timeline: [...e.timeline, { label: 'Đã sửa', at: 'hôm nay' }],
          }),
        );
      }
      if (verb === 'dismiss') {
        if (e.status !== 'open') return problem(409, 'conflict', 'Chỉ bỏ qua được mục đang mở.');
        if (!body.reason?.trim() && !body.duplicate_of_id)
          return problem(422, 'validation_failed', 'Ghi lý do hoặc chọn mục gốc.');
        return HttpResponse.json(
          update(id, {
            status: 'dismissed',
            dismissed_reason: body.reason ?? `Trùng ${body.duplicate_of_id}`,
            timeline: [...e.timeline, { label: 'Bỏ qua', at: 'hôm nay' }],
          }),
        );
      }
      if (verb === 'propose-to-pack') {
        if (e.status === 'open') return problem(409, 'conflict', 'Mục chưa sửa.');
        return HttpResponse.json({ pack_proposal_ref: `pc-${id}` });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
