import { http, HttpResponse } from 'msw';
import type {
  FindingWire,
  KnowledgeFinding,
  KnowledgeLintRun,
  KnowledgePage,
  KnowledgePageSummary,
  KnowledgePageVersion,
  LintRunWire,
  RenderedWire,
} from '../lib/v1/knowledge';
import type { components } from '../lib/v1/schema';

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

/*
 * The pages, runs and findings are kept in the screens' shapes and answered in the api's
 * (KnowledgePageView, RenderedKnowledgePage, KnowledgeRunView, FindingView), with the PROPOSAL
 * display fields alongside.
 */
const DS = 'ds-thi-truong';
const AT = '2026-10-08T10:00:00+07:00';

const pageView = (
  p: KnowledgePageSummary,
  versions = 1,
): components['schemas']['KnowledgePageView'] => ({
  id: p.id,
  title: p.title,
  kind: p.kind,
  status: p.status,
  slug: p.id,
  subject_kind: p.kind === 'source' ? 'dataset' : p.kind === 'entity' ? 'organization' : p.kind,
  subject_ref: null,
  index_summary: '',
  tags: [],
  classification: 'internal',
  current_version_id: `kpv-${p.id}-${versions}`,
  has_external_source: false,
  certification_suspended: p.status === 'decertified',
  data_space_id: DS,
  owner_id: 'u-ha',
  latest_version_no: versions,
  last_read_at: null,
  read_count_30d: 0,
  pii_column_refs: [],
  policy_state: 'current',
  policy_computed_at: AT,
  required_data_space_ids: [DS],
  stale_reason: null,
  created_at: AT,
  updated_at: AT,
});

const renderedWire = (page: KnowledgePage): RenderedWire => ({
  page: { ...pageView(page, page.version_count), has_external_source: page.external_source },
  version: {
    id: `kpv-${page.id}-${page.version}`,
    page_id: page.id,
    version: page.version,
    status: page.certified_at ? 'certified' : 'draft',
    summary: '',
    body: '',
    body_sha256: '0'.repeat(64),
    change_note: null,
    change_size: 'minor',
    origin: 'ingest',
    certified_at: page.certified_at,
    certified_by: null,
    reviewed_at: null,
    reviewed_by: null,
    parent_version_id: null,
    proposal_id: null,
    agent_run_id: null,
    knowledge_run_id: null,
    source_answer_id: null,
    created_at: AT,
  },
  claims: [],
  figures: [],
  links: [],
  sources: [],
  labels: [],
  render: 'template',
  as_of: AT,
  diff: null,
  parent_body: null,
  display: {
    summary: page.summary,
    claims: page.claims,
    policy: page.policy,
    links: page.links,
    certified_by_name: page.certified_by_name,
  },
});

const versionWire = (v: KnowledgePageVersion) => ({
  ...renderedWire(plain(pagesFixture()[0])).version!,
  id: v.id,
  version: v.version,
  status: v.status,
  change_note: v.change_note,
  created_at: v.created_at,
});

const runWire = (r: KnowledgeLintRun): LintRunWire => ({
  ...r,
  kind: 'lint',
  status: 'succeeded',
  trigger: 'schedule',
  trigger_event: null,
  data_space_id: DS,
  agent_run_id: null,
  cost: null,
  findings_count: r.open_findings,
  hitl_count: 0,
  page_limit: 50,
  pages_analyzed: r.page_count,
  pages_changed: 0,
  pages_deferred: 0,
  source_kind: null,
  source_ref: null,
  sources_seen: 100,
  sources_skipped_unchanged: r.skipped_source_pct,
  started_at: AT,
  created_at: AT,
});

/** The api's kind names for the two the screen calls differently. */
const API_KIND: Partial<Record<KnowledgeFinding['kind'], string>> = {
  orphan: 'orphan_page',
  decertified_source: 'source_decertified',
};

const findingWire = (f: KnowledgeFinding): FindingWire => ({
  ...f,
  kind: API_KIND[f.kind] ?? f.kind,
  detail: f.detail,
  data_space_id: DS,
  run_id: LINT_RUN.id,
  fingerprint: f.id,
  page_id: null,
  page_version_id: null,
  related_page_id: null,
  proposal_id: null,
  hitl_request_id: null,
  action: null,
  action_reason: null,
  resolved_at: null,
  resolved_by: null,
  suppressed_until: null,
  created_at: AT,
});

/* "Lưu câu trả lời thành tri thức" (2405:15399): the answers the dialog is opened on. */
const SAVED_ANSWERS: Record<string, string> = {
  'answer-npl-t9':
    'Nợ xấu bán lẻ T9 tăng 0,12 điểm so với T8, chủ yếu từ khoản vay tiêu dùng tín chấp ở vùng Đông Nam Bộ.',
  'answer-typed-number': 'Lãi suất huy động 12 tháng của Ngân hàng B là 4,9%.',
};

const answerWire = (id: string, text: string): components['schemas']['AnswerView'] => ({
  id,
  text,
  kind: 'data',
  question_id: `q-${id}`,
  plan_id: `plan-${id}`,
  figures: [],
  citations: [],
  chart_spec: null,
  filed_vs_current: null,
  reconcile_badge: null,
  agent_name: 'Agent Hỏi đáp',
  latency_ms: 1200,
  lineage_hash: null,
  data_product_id: null,
  product_scope: 'not_applicable',
  forecast_job_id: null,
  number_check: {},
  created_at: '2026-10-09T09:12:00+07:00',
});

const NPL_INDEX_ENTRY: components['schemas']['KnowledgeIndexEntry'] = {
  id: 'kp-npl',
  slug: 'no-xau-ban-le',
  title: 'Nợ xấu bán lẻ (chỉ số NPL_RATIO)',
  kind: 'metric',
  status: 'certified',
  subject_kind: 'metric',
  subject_ref: 'NPL_RATIO',
  index_summary: 'Định nghĩa và diễn biến nợ xấu bán lẻ.',
  classification: 'internal',
  has_external_source: false,
  certification_suspended: false,
  current_version_id: null,
  tags: [],
  updated_at: '2026-10-08T10:00:00+07:00',
};

const accepted = (id: string) =>
  HttpResponse.json({ id, status: 'queued', poll_after_ms: 1000 }, { status: 202 });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createKnowledgeHandlers() {
  const pages = pagesFixture();
  let findings = findingsFixture();
  return [
    // AnswerView of the two answers the save dialog is opened on; other ids fall through.
    http.get('*/v1/answers/:id', ({ params }) => {
      const text = SAVED_ANSWERS[String(params.id)];
      return text ? HttpResponse.json(answerWire(String(params.id), text)) : undefined;
    }),
    // The index ranks NPL_RATIO's page first for an answer about bad debt, and finds no other.
    http.get('*/v1/knowledge/index', ({ request }) => {
      const q = (new URL(request.url).searchParams.get('q') ?? '').toLowerCase();
      return HttpResponse.json({
        items: q.includes('nợ xấu') ? [NPL_INDEX_ENTRY] : [],
        next_page_token: '',
      });
    }),
    http.post('*/v1/knowledge-pages\\:from-answer', async ({ request }) => {
      const body = (await request.json()) as { answer_id: string };
      // An answer with a hand-typed figure cannot be saved (BR-167).
      if (body.answer_id === 'answer-typed-number')
        return problem(
          422,
          'business_rule_violated',
          'Câu trả lời có số không gắn với lượt truy vấn nên không lưu được (BR-167).',
        );
      return accepted('kpv-saveback-' + Date.now());
    }),
    http.get('*/v1/knowledge-runs', () =>
      HttpResponse.json({ items: [runWire(LINT_RUN)], next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-findings', () =>
      HttpResponse.json({
        items: findings.filter((x) => x.status === 'open').map(findingWire),
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
      return HttpResponse.json(findingWire(done));
    }),
    http.get('*/v1/knowledge-pages', () =>
      HttpResponse.json({ items: pages.map((p) => pageView(p)), next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id/versions', ({ params }) =>
      params.id === 'kp-bank-b'
        ? HttpResponse.json({ items: VERSIONS.map(versionWire), next_page_token: '' })
        : HttpResponse.json({ items: [], next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id', ({ params }) => {
      const summary = pages.find((p) => p.id === params.id);
      if (!summary) return problem(404, 'not_found', 'Không có trang hoặc bạn thiếu quyền.');
      return HttpResponse.json(renderedWire(summary.id === 'kp-bank-b' ? bankB() : plain(summary)));
    }),
    http.post('*/v1/knowledge-runs', async ({ request }) => {
      const body = (await request.json()) as { data_space_id?: string };
      if (!body.data_space_id) return problem(422, 'validation_failed', 'Thiếu data_space_id.');
      return accepted('kr-' + Date.now());
    }),
  ];
}
