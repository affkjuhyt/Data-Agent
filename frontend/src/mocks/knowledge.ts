import { http, HttpResponse } from 'msw';
import type {
  KnowledgeFinding,
  KnowledgeLintRun,
  KnowledgePage,
  KnowledgePageSummary,
  KnowledgePageVersion,
} from '../lib/v1/knowledge';

/* The knowledge pages of "P07 · Trang tri thức" (2405:14761). */
function pagesFixture(): KnowledgePageSummary[] {
  return [
    { id: 'kp-bank-b', title: 'Ngân hàng B', kind: 'entity', status: 'certified' },
    {
      id: 'kp-deposit-12m',
      title: 'Lãi suất huy động 12 tháng bình quân',
      kind: 'metric',
      status: 'approved',
    },
    { id: 'kp-bank-k', title: 'Ngân hàng K', kind: 'entity', status: 'decertified' },
    { id: 'kp-sbv-rate', title: 'Lãi suất điều hành NHNN', kind: 'source', status: 'certified' },
    { id: 'kp-fees', title: 'Biểu phí dịch vụ ngân hàng', kind: 'topic', status: 'needs_review' },
    { id: 'kp-casa', title: 'Tỷ lệ CASA', kind: 'metric', status: 'draft' },
  ];
}

function bankB(): KnowledgePage {
  return {
    ...pagesFixture()[0],
    version: 5,
    certified_at: '2026-10-08T10:00:00+07:00',
    certified_by_name: 'Lê Thu Hà',
    external_source: true,
    summary: [
      {
        text: 'Ngân hàng B là ngân hàng TMCP quy mô vừa, công bố biểu lãi suất huy động trên website và tệp PDF hằng tuần. Lãi suất 12 tháng tại quầy tuần 41 là ',
      },
      { figure: '4,90%/năm', query_run_id: 'qr-8841', source_title: 'Dataset hợp nhất v42' },
      { text: ', cao hơn bình quân 29 ngân hàng ' },
      { figure: '0,05 điểm', query_run_id: 'qr-8842', source_title: 'Dataset hợp nhất v42' },
      { text: '; kỳ hạn 6 tháng là ' },
      { figure: '4,70%/năm', query_run_id: 'qr-8843', source_title: 'Dataset hợp nhất v42' },
      { text: '. Chuyển khoản liên ngân hàng trên kênh online miễn phí từ 01/09/2026.' },
    ],
    claims: [
      {
        text: 'Lãi suất 12 tháng tại quầy {lai_suat_12t}',
        source_title: 'Dataset hợp nhất v42',
        source_detail: 'Lãi suất huy động · tuần 41',
        valid_text: '07/10 – 13/10/2026',
        status: 'valid',
      },
      {
        text: 'Chuyển khoản liên ngân hàng online miễn phí',
        source_title: 'Biểu phí 07/10/2026',
        source_detail: 'PDF · trang 3',
        valid_text: 'Từ 01/09/2026',
        status: 'valid',
      },
      {
        text: 'Mã ngân hàng và tên miền chính thức',
        source_title: 'Danh mục thực thể chuẩn',
        source_detail: 'reference_entity',
        valid_text: 'Không thời hạn',
        status: 'valid',
      },
      {
        text: 'Sáp nhập công ty tài chính con',
        source_title: 'Tin công bố 12/03/2025',
        source_detail: 'nganhangb.vn',
        valid_text: '12/03 – 31/12/2025',
        status: 'expired',
      },
    ],
    policy: {
      data_spaces_text: 'Thị trường (nguồn ngoài), Sản phẩm',
      classification: 'Nội bộ',
      viewer_rule: 'Cần quyền trên mọi nguồn của trang',
    },
    links: [
      {
        direction: 'out',
        title: 'Lãi suất huy động 12 tháng bình quân',
        page_id: 'kp-deposit-12m',
      },
      { direction: 'out', title: 'Biểu phí dịch vụ ngân hàng', page_id: 'kp-fees' },
      { direction: 'in', title: 'Ngân hàng K (so sánh)', page_id: 'kp-bank-k' },
      { direction: 'in', title: 'Câu trả lời đã lưu: “So sánh lãi suất B với K”', page_id: null },
    ],
    version_count: 5,
  };
}

/** The other pages read as short pages with no claims yet. */
function plain(summary: KnowledgePageSummary): KnowledgePage {
  return {
    ...summary,
    version: 1,
    certified_at: null,
    certified_by_name: null,
    external_source: false,
    summary: [{ text: `Trang ${summary.title} đang được biên soạn.` }],
    claims: [],
    policy: { data_spaces_text: 'Rủi ro tín dụng', classification: 'Nội bộ', viewer_rule: '—' },
    links: [],
    version_count: 1,
  };
}

const VERSIONS: KnowledgePageVersion[] = [5, 4, 3, 2, 1].map((v) => ({
  id: `kpv-bank-b-${v}`,
  version: v,
  status: v === 5 ? 'certified' : 'superseded',
  change_note: v === 5 ? 'Biểu phí tháng 10' : v === 4 ? 'Lãi suất tuần 40' : null,
  created_at: new Date(2026, 9, 8 - (5 - v) * 7).toISOString(),
}));

// "P07 · Rà soát tri thức" (2405:15078): the latest lint run and its open findings.
const LINT_RUN: KnowledgeLintRun = {
  id: 'kr-lint-1010',
  finished_at: '2026-10-10T01:30:00+07:00',
  page_count: 412,
  open_findings: 38,
  high_findings: 6,
  stale_pages: 17,
  skipped_source_pct: 61,
  duration_minutes: 11,
  limits_text: 'tối đa 30 phút, 50 thẻ Cần bạn',
  schedule_text: 'lượt dùng mô hình chạy chủ nhật 02:00',
};

function findingsFixture(): KnowledgeFinding[] {
  const f = (
    id: string,
    severity: KnowledgeFinding['severity'],
    kind: KnowledgeFinding['kind'],
    subject_text: string,
    detail: string,
    extra: Partial<KnowledgeFinding> = {},
  ): KnowledgeFinding => ({
    id,
    severity,
    kind,
    subject_text,
    detail,
    status: 'open',
    evidence: [],
    suggestion: null,
    ...extra,
  });
  return [
    f(
      'kf-bank-k',
      'high',
      'decertified_source',
      'Ngân hàng K',
      'Dataset hợp nhất v41 bị thu hồi 09/10; trang tạm mất chứng nhận',
      { suggestion: 'Gợi ý: Biên soạn lại trang trên dataset v42 rồi gửi chứng nhận.' },
    ),
    f(
      'kf-bank-m',
      'high',
      'contradiction',
      'Lãi suất 12 tháng bình quân ↔ Ngân hàng M',
      'Website 5,10% và API 4,90% cùng tuần 41',
      {
        evidence: [
          {
            label: 'Trang “Lãi suất 12 tháng bình quân”',
            value: '5,10%/năm · website · 08:04 10/10',
          },
          { label: 'Trang “Ngân hàng M”', value: '4,90%/năm · API · 08:31 10/10' },
        ],
        suggestion: 'Gợi ý: Đề xuất sửa, sau khi xử lý xung đột nguồn ở Cần bạn.',
      },
    ),
    f(
      'kf-bank-b',
      'medium',
      'expired_claim',
      'Ngân hàng B',
      '“Sáp nhập công ty tài chính con” hết hạn 31/12/2025',
    ),
    f(
      'kf-casa',
      'medium',
      'metric_without_page',
      'Tỷ lệ CASA',
      '34 câu hỏi trong 30 ngày, chưa có trang',
      {
        suggestion: 'Gợi ý: Tạo trang chỉ số từ định nghĩa đã chứng nhận.',
      },
    ),
    f(
      'kf-floating',
      'low',
      'undefined_term',
      '“lãi suất thả nổi”',
      'Xuất hiện ở 4 trang, chưa có trong từ điển',
    ),
    f(
      'kf-fees-2024',
      'low',
      'orphan',
      'Biểu phí 2024',
      'Không có liên kết đến, 90 ngày không ai đọc',
      {
        suggestion: 'Gợi ý: Gỡ trang; vẫn mở lại được từ trích dẫn cũ.',
      },
    ),
  ];
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createKnowledgeHandlers() {
  const pages = pagesFixture();
  let findings = findingsFixture();
  return [
    http.get('*/v1/answers/:id/knowledge-draft', ({ params }) =>
      params.id === 'answer-npl-t9'
        ? HttpResponse.json({
            target_page: { id: 'kp-npl', title: 'Nợ xấu bán lẻ (chỉ số NPL_RATIO)' },
            paragraph:
              'Nợ xấu bán lẻ T9 tăng {npl_ratio_delta} so với T8, chủ yếu từ khoản vay tiêu dùng tín chấp ở vùng Đông Nam Bộ.',
            source_text: 'Lượt truy vấn QR-2026-1009-118 · chỉ số NPL_RATIO v4 đã chứng nhận',
            validity_text: 'Kỳ T9/2026',
          })
        : problem(404, 'not_found', 'Không có câu trả lời này.'),
    ),
    http.post('*/v1/knowledge-pages\\:from-answer', async ({ request }) => {
      const body = (await request.json()) as { answer_id: string };
      // An answer with a hand-typed figure cannot be saved (BR-167).
      if (body.answer_id === 'answer-typed-number')
        return problem(
          422,
          'business_rule_violated',
          'Câu trả lời có số không gắn với lượt truy vấn nên không lưu được (BR-167).',
        );
      return HttpResponse.json(
        {
          id: 'kpv-saveback-' + Date.now(),
          version: 1,
          status: 'pending_review',
          change_note: 'Lưu từ câu trả lời',
          created_at: new Date().toISOString(),
        },
        { status: 201 },
      );
    }),
    http.get('*/v1/knowledge-runs', () =>
      HttpResponse.json({ items: [LINT_RUN], next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-findings', () =>
      HttpResponse.json({
        items: findings.filter((x) => x.status === 'open'),
        next_page_token: '',
      }),
    ),
    http.post('*/v1/knowledge-findings/:action', async ({ params, request }) => {
      const [id] = String(params.action).split(':');
      const finding = findings.find((x) => x.id === id);
      if (!finding) return problem(404, 'not_found', 'Không tìm thấy phát hiện.');
      if (finding.status !== 'open') return problem(409, 'conflict', 'Phát hiện đã được xử lý.');
      const body = (await request.json()) as {
        action: string;
        reason?: string;
        assignee_id?: string;
      };
      if (body.action === 'dismiss' && !body.reason?.trim())
        return problem(422, 'validation_failed', 'Bỏ qua cần ghi lý do (BR-166).');
      if (body.action === 'reassign' && !body.assignee_id)
        return problem(422, 'validation_failed', 'Chọn người nhận.');
      const done: KnowledgeFinding = {
        ...finding,
        status: body.action === 'dismiss' ? 'dismissed' : 'resolved',
      };
      findings = findings.map((x) => (x.id === id ? done : x));
      return HttpResponse.json(done);
    }),
    http.get('*/v1/knowledge-pages', () =>
      HttpResponse.json({ items: pages, next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id/versions', ({ params }) =>
      params.id === 'kp-bank-b'
        ? HttpResponse.json({ items: VERSIONS, next_page_token: '' })
        : HttpResponse.json({ items: [], next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id', ({ params }) => {
      const summary = pages.find((p) => p.id === params.id);
      if (!summary) return problem(404, 'not_found', 'Không có trang hoặc bạn thiếu quyền.');
      return HttpResponse.json(summary.id === 'kp-bank-b' ? bankB() : plain(summary));
    }),
    http.post('*/v1/knowledge-runs', () =>
      HttpResponse.json({ id: 'kr-' + Date.now() }, { status: 202 }),
    ),
  ];
}
