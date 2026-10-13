import { http, HttpResponse } from 'msw';
import type {
  AnswerProduct,
  ProductDetailWire,
  ProductFreshness,
  ProductMonitoring,
  ProductSummaryWire,
} from '../lib/v1/answerProducts';

/*
 * Data product of the context layer (P07, "P07 · Data product" 2418:14958) for the dev mock: the
 * active "Giá xăng dầu trong nước" v3 and a draft short of its sample questions. The P06 shared
 * data product mock answers the same paths, so this one sits before it and answers only
 * `?layer=context` lists and `adp-` ids (P06 uses `dp-`), falling through otherwise.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

/** The screen's product without the Theo dõi figures, which the detail sends as `stats`. */
type Product = Omit<AnswerProduct, 'monitoring'>;

const ours = (id: unknown) => typeof id === 'string' && id.startsWith('adp-');

function productsFixture(): Product[] {
  return [
    {
      id: 'adp-fuel-prices',
      code: 'gia_xang_dau_trong_nuoc',
      name: 'Giá xăng dầu trong nước',
      status: 'active',
      version: 3,
      owner_name: 'Phạm Minh Đức',
      steward_name: 'Nguyễn Thị Hạnh',
      freshness_statement: 'Cập nhật trước 16:30 ngày điều hành',
      freshness_state: 'fresh',
      next_due_at: '2026-07-09T16:30:00+07:00',
      readiness_text: null,
      data_space_name: 'Thị trường năng lượng',
      description:
        'Giá cơ sở và giá trần bán lẻ theo từng kỳ điều hành của Bộ Công Thương, thành phần giá (giá thế giới bình quân giữa hai kỳ, thuế, trích lập và chi Quỹ bình ổn).',
      scope_chips: ['Kỳ từ 01/01/2024', 'E5RON92', 'RON95-III', 'DO 0,05S-II', 'Dầu hoả', 'Mazut'],
      out_of_scope_note:
        'Giá tại một cửa hàng cụ thể; giá bán lẻ thấp hơn giá trần của từng doanh nghiệp. Trả lời được nhưng mang nhãn “Ngoài phạm vi data product, không có biên nhận”.',
      sample_questions: [
        { question: 'Giá trần E5RON92 kỳ 02/07/2026?', answer: '19.730 đ/lít', result: 'pass' },
        {
          question: 'Kỳ 02/07/2026 E5RON92 giảm bao nhiêu?',
          answer: '1.055 đ/lít',
          result: 'pass',
        },
        {
          question: 'Quỹ BOG trích lập với dầu diesel kỳ 02/07?',
          answer: '200 đ/lít',
          result: 'pass',
        },
        {
          question: 'Giá thế giới RON92 bình quân kỳ 02/07?',
          answer: '98,166 USD/thùng',
          result: 'pass',
        },
        {
          question: 'Thuế BVMT với xăng áp dụng kỳ 02/07?',
          answer: 'theo NQ 34/2026',
          result: 'fail',
        },
      ],
      sample_total: 12,
      members: [
        {
          id: 'm-ds',
          member_kind: 'dataset',
          ref: 'gia_xang_dau_ky_dieu_hanh',
          detail: 'Dataset · chính · v41',
          role: 'primary',
          certified: true,
        },
        {
          id: 'm-metric',
          member_kind: 'metric',
          ref: 'gia_tran_ban_le v2',
          detail: 'Chỉ số · ký Nguyễn Thị Hạnh 15/06/2026',
          role: 'reference',
          certified: true,
        },
        {
          id: 'm-page',
          member_kind: 'knowledge_page',
          ref: 'Nghị định 80/2023/NĐ-CP',
          detail: 'Trang tri thức · tham chiếu',
          role: 'reference',
          certified: false,
        },
      ],
      stats: {
        in_scope_pct: 82,
        masked_pct: 1.4,
        error_book_count: 2,
        masked_note: '8 kỳ trễ từ 01/2024',
        error_book_open: [
          { id: 'eb-0043', code: 'EB-0043' },
          { id: 'eb-0045', code: 'EB-0045' },
        ],
        eval: { passed: 11, total: 12, threshold_pct: 95 },
      },
      reviewer_text:
        'Người duyệt: Phạm Minh Đức (owner), khác steward và người tạo. Đổi phạm vi tạo v4; v3 vẫn phục vụ tới khi duyệt.',
      draft_version: null,
    },
    {
      id: 'adp-deposit-rates',
      code: 'lai_suat_huy_dong_niem_yet',
      name: 'Lãi suất huy động niêm yết',
      status: 'draft',
      version: 1,
      owner_name: 'Đỗ Hải Yến',
      steward_name: 'Trần Minh Quân',
      freshness_statement: 'Cập nhật trước 10:00 thứ Hai hằng tuần',
      freshness_state: 'missing',
      next_due_at: null,
      readiness_text: 'Chưa kích hoạt · 7/10 câu hỏi mẫu',
      data_space_name: 'Nguồn vốn',
      description:
        'Lãi suất huy động niêm yết theo kỳ hạn của các ngân hàng, hợp nhất hằng tuần (P14).',
      scope_chips: ['Tuần từ 01/2026', '1–24 tháng'],
      out_of_scope_note: 'Lãi suất thoả thuận riêng với khách hàng lớn.',
      sample_questions: [
        {
          question: 'Lãi suất 12 tháng bình quân tuần 41?',
          answer: '4,85%/năm',
          result: 'pending',
        },
      ],
      sample_total: 7,
      members: [
        {
          id: 'm-dep-ds',
          member_kind: 'dataset',
          ref: 'lai_suat_huy_dong_tuan',
          detail: 'Dataset · chính · v42',
          role: 'primary',
          certified: true,
        },
      ],
      stats: {
        in_scope_pct: 0,
        masked_pct: 0,
        error_book_count: 0,
        masked_note: 'chưa kích hoạt',
        error_book_open: [],
        eval: null,
      },
      reviewer_text: 'Người duyệt: Đỗ Hải Yến (owner).',
      draft_version: null,
    },
  ];
}

function freshnessFixture(): ProductFreshness {
  const periods = ['02/07', '25/06', '18/06', '11/06', '04/06', '28/05', '21/05', '14/05'];
  return {
    datasets: [
      {
        ref: 'gia_xang_dau_ky_dieu_hanh',
        commitment_text:
          'Hạn 16:30 ngày điều hành theo lịch điều hành giá (thứ Năm, dời theo lịch làm việc)',
        grace_text: 'ân hạn 30 phút',
        last_as_of: '2026-07-02',
        last_loaded_at: '2026-07-02T16:12:00+07:00',
        next_due_at: '2026-07-09T16:30:00+07:00',
        state: 'fresh',
      },
    ],
    periods: periods.map((p, i) => ({
      period: p,
      state: i === 2 ? 'masked_stale' : 'shown',
      note: i === 2 ? 'nạp 17:20, trễ 50 phút' : 'đúng hạn',
    })),
  };
}

function monitoringFixture(): ProductMonitoring {
  return {
    questions_30d: 412,
    weeks: [
      { label: 'Tuần 24', in_scope_pct: 78, masked_pct: 0 },
      { label: 'Tuần 25', in_scope_pct: 80, masked_pct: 4.2 },
      { label: 'Tuần 26', in_scope_pct: 83, masked_pct: 0 },
      { label: 'Tuần 27', in_scope_pct: 82, masked_pct: 1.1 },
    ],
  };
}

/*
 * Products are kept in the screen's shape and answered in the api's (DataProductView,
 * DataProductDetail, FreshnessReport); what only the screen draws goes under `display`.
 */
const AT = '2026-07-01T09:00:00+07:00';

const row = (p: Product): ProductSummaryWire => ({
  id: p.id,
  code: p.code,
  name: p.name,
  status: p.status,
  version: p.version,
  layer: 'context',
  description: p.description,
  freshness_statement: p.freshness_statement,
  freshness_state: p.freshness_state,
  out_of_scope_note: p.out_of_scope_note,
  sample_questions: p.sample_questions.map((q) => ({ text: q.question })),
  scope_entities: [],
  classification: 'internal',
  data_space_id: 'ds-nang-luong',
  owner_id: 'u-owner',
  steward_id: 'u-steward',
  eval_set_id: null,
  pack_version_id: null,
  proposal_id: null,
  period_from: '2024-01-01',
  period_to: null,
  period_grain: 'week',
  suspended_reason: null,
  activated_at: p.status === 'active' ? AT : null,
  activated_by: null,
  created_at: AT,
  updated_at: AT,
  owner_name: p.owner_name,
  steward_name: p.steward_name,
  next_due_at: p.next_due_at,
  readiness_text: p.readiness_text,
});

const detail = (p: Product): ProductDetailWire => ({
  ...row(p),
  members: [],
  freshness: [],
  latest_eval_run: null,
  open_error_book_entries: p.stats.error_book_count,
  display: p,
  stats: monitoringFixture(),
});

export function createAnswerProductHandlers() {
  let products = productsFixture();
  const find = (id: unknown) => products.find((p) => p.id === id);
  return [
    http.get('*/v1/data-products', ({ request }) => {
      const params = new URL(request.url).searchParams;
      if (params.get('layer') !== 'context') return undefined;
      const status = params.get('status');
      const items = products.filter((p) => !status || p.status === status).map(row);
      return HttpResponse.json({ items, next_page_token: '' });
    }),
    http.get('*/v1/data-products/:id/freshness', ({ params }) =>
      ours(params.id)
        ? HttpResponse.json({
            at: AT,
            data_product_id: String(params.id),
            datasets: [],
            display: freshnessFixture(),
          })
        : undefined,
    ),
    http.get('*/v1/data-products/:id', ({ params }) => {
      if (!ours(params.id)) return undefined;
      const p = find(params.id);
      return p
        ? HttpResponse.json(detail(p))
        : problem(404, 'not_found', 'Không tìm thấy data product.');
    }),
    http.patch('*/v1/data-products/:id', ({ params }) => {
      if (!ours(params.id)) return undefined;
      const p = find(params.id);
      if (!p) return problem(404, 'not_found', 'Không tìm thấy data product.');
      const updated = {
        ...p,
        draft_version: p.status === 'active' ? p.version + 1 : p.draft_version,
      };
      products = products.map((x) => (x.id === p.id ? updated : x));
      return HttpResponse.json(row(updated));
    }),
    http.delete('*/v1/data-products/:id/members/:memberId', ({ params }) => {
      if (!ours(params.id)) return undefined;
      const p = find(params.id);
      const m = p?.members.find((x) => x.id === params.memberId);
      if (!p || !m) return problem(404, 'not_found', 'Không tìm thấy thành phần.');
      if (p.status === 'active' && m.role === 'primary')
        return problem(
          422,
          'business_rule_violated',
          'Gỡ thành phần chính của bản đang dùng phải qua phiên bản mới (BR-175).',
        );
      products = products.map((x) =>
        x.id === p.id ? { ...x, members: x.members.filter((y) => y.id !== m.id) } : x,
      );
      return new HttpResponse(null, { status: 204 });
    }),
    http.post('*/v1/data-products/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (!ours(id)) return undefined;
      const p = find(id);
      if (!p) return problem(404, 'not_found', 'Không tìm thấy data product.');
      if (verb === 'submit') {
        if (p.sample_total < 10)
          return problem(
            422,
            'business_rule_violated',
            `Mới có ${p.sample_total}/10 câu hỏi mẫu có đáp án (BR-175).`,
          );
        const evalPct = p.stats.eval ? (p.stats.eval.passed / p.stats.eval.total) * 100 : 0;
        if (evalPct < 95)
          return problem(
            422,
            'business_rule_violated',
            `Bộ đánh giá đạt ${evalPct.toFixed(0)}%, dưới 95% (BR-175).`,
          );
        products = products.map((x) => (x.id === id ? { ...x, status: 'pending_review' } : x));
        return HttpResponse.json(
          { id, eval_run_id: 'er-dp-1', status: 'evaluating', poll_after_ms: 1000 },
          { status: 202 },
        );
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
