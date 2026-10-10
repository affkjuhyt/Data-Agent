import { http, HttpResponse } from 'msw';
import type {
  ContextEval,
  EntityDetail,
  EntityModel,
  GlossaryEntry,
  RelationshipSuggestion,
  UnknownPhrase,
} from '../lib/v1/semanticLayer';

/*
 * The semantic layer of P07 for the dev mock: the retail-bank entity model of "Semantic · Mô hình
 * thực thể" (2087:8090) and its relationship suggestions ("Hộp thoại · suggestRel", 2095:9259),
 * the glossary of "Từ điển nghiệp vụ" (2088:7308), and "Đánh giá lớp ngữ cảnh" (2232:15323).
 * GET /v1/terms answers here only with ?view=glossary; the plain list stays with handlers.ts.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
const page = <T>(items: T[]) =>
  HttpResponse.json({ items, next_cursor: null, total: items.length });

function modelFixture(): EntityModel {
  return {
    title: 'Ngân hàng bán lẻ',
    focus: 'loan',
    entities: [
      {
        key: 'customer',
        name: 'Khách hàng',
        source_text: 'Core · customers',
        columns: ['customer_id', 'segment', 'region'],
        x: 5,
        y: 5,
      },
      {
        key: 'product',
        name: 'Sản phẩm',
        source_text: 'Core · products',
        columns: ['product_id', 'product_line', 'secured'],
        x: 67,
        y: 5,
      },
      {
        key: 'loan',
        name: 'Khoản vay',
        source_text: 'Core · ld_loans + Hồ sơ tín dụng',
        columns: ['loan_id', 'balance', 'debt_group', 'overdue_days'],
        x: 36,
        y: 34,
      },
      {
        key: 'branch',
        name: 'Chi nhánh',
        source_text: 'DWH · dim_branch',
        columns: ['branch_id', 'region'],
        x: 5,
        y: 66,
      },
      {
        key: 'collateral',
        name: 'Tài sản bảo đảm',
        source_text: 'Core + Ảnh TSBĐ',
        columns: ['collateral_id', 'type', 'value'],
        x: 67,
        y: 66,
      },
      {
        key: 'contract',
        name: 'Hợp đồng',
        source_text: 'Phi cấu trúc · Hợp đồng',
        columns: ['contract_no', 'signed_date', 'terms'],
        x: 36,
        y: 80,
      },
    ],
    relationships: [
      { from: 'customer', to: 'loan', cardinality: '1 — n' },
      { from: 'product', to: 'loan', cardinality: 'n — 1' },
      { from: 'branch', to: 'loan', cardinality: 'n — 1' },
      { from: 'collateral', to: 'loan', cardinality: 'n — n' },
      { from: 'contract', to: 'loan', cardinality: '1 — 1' },
    ],
  };
}

function entityFixture(key: string): EntityDetail | null {
  const node = modelFixture().entities.find((e) => e.key === key);
  if (!node) return null;
  if (key !== 'loan')
    return {
      key,
      name: node.name,
      description: `${node.name} trong mô hình Ngân hàng bán lẻ.`,
      facts: [
        { label: 'Khoá', value: node.columns[0] },
        { label: 'Thuộc tính', value: String(node.columns.length) },
      ],
      sources: [{ label: node.source_text, role: 'Chính', tone: 'success' }],
      synonyms: [],
      metrics: [],
      more_metrics: 0,
    };
  return {
    key,
    name: 'Khoản vay',
    description: 'Một khoản cấp tín dụng cho một khách hàng theo một hợp đồng; theo dõi theo ngày.',
    facts: [
      { label: 'Khoá', value: 'loan_id (Core) ≡ contract_no (Hợp đồng) qua bảng liên kết' },
      { label: 'Hạt', value: '1 khoản vay × 1 ngày' },
      { label: 'Thuộc tính', value: '24 · 3 che theo chính sách' },
    ],
    sources: [
      { label: 'Core banking · ld_loans', role: 'Chính', tone: 'success' },
      { label: 'DWH · fact_loan_balance_daily', role: 'Báo cáo', tone: 'neutral' },
      { label: 'Hồ sơ tín dụng → credit_file_extracted', role: 'Trích xuất', tone: 'info' },
    ],
    synonyms: ['khoản vay', 'khoản tín dụng', 'món vay', 'hợp đồng tín dụng', 'loan'],
    metrics: ['NPL_RATIO v4', 'LOAN_BALANCE v2', 'COST_OF_RISK v1'],
    more_metrics: 11,
  };
}

function suggestionsFixture(): RelationshipSuggestion[] {
  return [
    {
      id: 'rs-collateral',
      from_label: 'Khoản vay.collateral_id',
      to_label: 'Tài sản bảo đảm',
      arrow: '→',
      match_pct: 99.1,
      needs_owner: null,
      body: {
        from_dataset_id: 'ds-ld-loans',
        from_columns: ['collateral_id'],
        to_dataset_id: 'ds-collateral',
        to_columns: ['collateral_id'],
        cardinality: 'n:1',
      },
    },
    {
      id: 'rs-crm',
      from_label: 'Khách hàng.customer_id',
      to_label: 'CRM.cust_no',
      arrow: '↔',
      match_pct: 96.4,
      needs_owner: 'chủ sở hữu CRM',
      body: {
        from_dataset_id: 'ds-customers',
        from_columns: ['customer_id'],
        to_dataset_id: 'ds-crm',
        to_columns: ['cust_no'],
        cardinality: '1:1',
      },
    },
  ];
}

function glossaryFixture(): GlossaryEntry[] {
  const term = (
    t: Partial<GlossaryEntry> &
      Pick<GlossaryEntry, 'id' | 'term' | 'maps_to' | 'synonyms' | 'data_space_text'>,
  ): GlossaryEntry => ({
    status: 'certified',
    meanings: [],
    ambiguity_rule: null,
    owner_name: 'Trần Quốc An',
    usage_text: '',
    ...t,
  });
  return [
    term({
      id: 't-npl',
      term: 'Nợ xấu',
      maps_to: 'NPL_RATIO v4',
      synonyms: 'NPL, nợ nhóm 3–5, tỷ lệ nợ xấu',
      data_space_text: 'Rủi ro tín dụng',
      usage_text: '1.204 câu hỏi · 0 lần người sửa',
    }),
    term({
      id: 't-npl-early',
      term: 'Nợ xấu (cảnh báo sớm)',
      maps_to: 'NPL_INTERNAL',
      synonyms: 'nợ nhóm 2–5, nợ có vấn đề',
      data_space_text: 'Rủi ro · Giám sát',
    }),
    term({
      id: 't-retail',
      term: 'Bán lẻ',
      maps_to: 'segment = retail',
      synonyms: 'khách hàng cá nhân, KHCN',
      data_space_text: 'Mọi Data Space',
    }),
    term({
      id: 't-north',
      term: 'Miền Bắc',
      maps_to: 'region ∈ 4 vùng',
      synonyms: 'phía Bắc, ngoài Bắc',
      data_space_text: 'Mọi Data Space',
      status: 'ambiguous',
      meanings: [
        {
          label: 'Theo địa lý',
          expression: 'region ∈ {ĐB sông Hồng, DH Bắc Bộ, Tây Bắc, Đông Bắc}',
          default: true,
        },
        {
          label: 'Theo tổ chức',
          expression: 'Khối miền Bắc (bao gồm Thanh Hoá, Nghệ An)',
          default: false,
        },
      ],
      ambiguity_rule:
        'Dùng mặc định và ghi rõ trong câu trả lời; nếu Data Space là Mạng lưới → hỏi người',
      usage_text: '212 câu hỏi · 3 lần người sửa',
    }),
    term({
      id: 't-period',
      term: 'Kỳ chốt',
      maps_to: 'dim_date.is_period_close',
      synonyms: 'cuối kỳ, ngày khoá sổ',
      data_space_text: 'Mọi Data Space',
    }),
    term({
      id: 't-balance',
      term: 'Dư nợ',
      maps_to: 'LOAN_BALANCE v2',
      synonyms: 'số dư cho vay, outstanding',
      data_space_text: 'Rủi ro, Tài chính',
    }),
    term({
      id: 't-revenue',
      term: 'Doanh thu',
      maps_to: 'NET_REVENUE v4',
      synonyms: 'doanh số, revenue',
      data_space_text: 'Kinh doanh bán lẻ',
    }),
  ];
}

function unknownFixture(): UnknownPhrase[] {
  return [
    {
      id: 'u-overdue',
      phrase: 'tỷ lệ nợ quá hạn 30+',
      count: 14,
      suggestion: 'Đề xuất: OVERDUE_30_RATIO (chưa có) → tạo chỉ số',
      draft: { term: 'Tỷ lệ nợ quá hạn 30+', definition: 'OVERDUE_30_RATIO' },
    },
    {
      id: 'u-vip',
      phrase: 'khách VIP',
      count: 9,
      suggestion: 'Đề xuất: segment = priority · cần chủ sở hữu xác nhận',
      draft: { term: 'Khách VIP', definition: 'segment = priority' },
    },
    {
      id: 'u-net',
      phrase: 'dư nợ thuần',
      count: 5,
      suggestion: 'Đề xuất: đồng nghĩa với LOAN_BALANCE trừ dự phòng',
      draft: { term: 'Dư nợ thuần', definition: 'LOAN_BALANCE − dự phòng' },
    },
  ];
}

function evalFixture(): ContextEval {
  return {
    id: 'eval-credit-risk',
    data_space_name: 'Rủi ro tín dụng',
    kpis: [
      {
        label: 'Bộ câu hỏi chuẩn',
        value: '120 câu',
        note: '34 câu từ hội thoại thật',
        tone: 'neutral',
      },
      {
        label: 'Trả lời đúng',
        value: '108 / 120',
        note: '▲ 3 câu so với lần trước',
        tone: 'success',
      },
      { label: 'Trả lời sai', value: '9', note: '6 do thuật ngữ mơ hồ', tone: 'error' },
      {
        label: 'Từ chối đúng',
        value: '3',
        note: 'thiếu quyền hoặc thiếu dữ liệu',
        tone: 'neutral',
      },
    ],
    runs: [
      {
        id: 'er-58',
        label: '#58 · hôm nay',
        change: 'NPL_RATIO v5 (bản nháp)',
        correct: 108,
        delta: 3,
        result: 'published',
      },
      {
        id: 'er-57',
        label: '#57 · 05/10',
        change: 'Thuật ngữ “khách hàng mới” = mở tài khoản 90 ngày',
        correct: 105,
        delta: 2,
        result: 'released',
      },
      {
        id: 'er-56',
        label: '#56 · 02/10',
        change: 'Ánh xạ dim_branch → vùng mới',
        correct: 101,
        delta: -2,
        result: 'blocked',
      },
    ],
    latest_run_label: '#58',
    misses: [
      {
        question: 'Nợ xấu miền Bắc quý 3?',
        expected: 'Theo khối Miền Bắc',
        understood: 'Theo địa lý 25 tỉnh',
        cause: 'Thuật ngữ mơ hồ',
      },
      {
        question: 'Khách hàng mới tháng 9 vay bao nhiêu?',
        expected: 'Mở TK trong 90 ngày',
        understood: 'Mở TK trong tháng 9',
        cause: 'Thiếu ví dụ',
      },
      {
        question: 'Dư nợ thẻ tín dụng hôm qua?',
        expected: 'Từ chối: nguồn thiếu 6 ngày',
        understood: 'Trả số của 6 ngày trước',
        cause: 'Thiếu kiểm tra độ mới',
      },
    ],
  };
}

export function createSemanticLayerHandlers() {
  let glossary = glossaryFixture();
  let unknown = unknownFixture();
  return [
    http.get('*/v1/entities', () => HttpResponse.json(modelFixture())),
    http.get('*/v1/entities/:key', ({ params }) => {
      const detail = entityFixture(String(params.key));
      return detail
        ? HttpResponse.json(detail)
        : problem(404, 'not_found', 'Không có thực thể này.');
    }),
    http.post('*/v1/entities\\:suggest-relationships', () =>
      HttpResponse.json({
        items: suggestionsFixture(),
        note: 'Quan hệ thứ hai cần chủ sở hữu CRM xác nhận vì khác mã.',
      }),
    ),
    http.post('*/v1/relationships', () =>
      HttpResponse.json({ id: `rel-${Date.now()}`, status: 'pending_review' }, { status: 201 }),
    ),
    http.get('*/v1/terms/unknown', () => page(unknown)),
    http.get('*/v1/terms', ({ request }) =>
      new URL(request.url).searchParams.get('view') === 'glossary' ? page(glossary) : undefined,
    ),
    http.post('*/v1/terms', async ({ request }) => {
      const body = (await request.json()) as { term: string; definition: string };
      if (!body.term?.trim() || !body.definition?.trim())
        return problem(422, 'validation_failed', 'Nhập thuật ngữ và nghĩa.');
      const created: GlossaryEntry = {
        id: `t-new-${glossary.length}`,
        term: body.term,
        maps_to: body.definition,
        synonyms: '',
        data_space_text: 'Rủi ro tín dụng',
        status: 'draft',
        meanings: [],
        ambiguity_rule: null,
        owner_name: 'Đỗ Hải Yến',
        usage_text: 'Chưa dùng',
      };
      glossary = [...glossary, created];
      unknown = unknown.filter((u) => u.draft.term !== body.term);
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get('*/v1/eval-sets', () =>
      page([{ id: 'eval-credit-risk', data_space_name: 'Rủi ro tín dụng' }]),
    ),
    http.get('*/v1/eval-sets/:id', ({ params }) =>
      params.id === 'eval-credit-risk'
        ? HttpResponse.json(evalFixture())
        : problem(404, 'not_found', 'Không có bộ đánh giá này.'),
    ),
    http.post('*/v1/eval-sets/:id/runs', () => HttpResponse.json({ id: 'er-59' }, { status: 202 })),
  ];
}
