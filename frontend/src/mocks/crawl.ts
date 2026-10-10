import { http, HttpResponse } from 'msw';
import type {
  AcquireSummary,
  CrawlRun,
  CrawlSchemaVersion,
  ConsolidationRun,
  ExtractedRecord,
  SchemaDrift,
  CrawlSource,
  SourceSuggestion,
} from '../lib/v1/crawl';

/* The crawl sources of "P14 · Nguồn thu thập · Danh sách" (2389:14334). */
function sourcesFixture(): CrawlSource[] {
  const s = (
    id: string,
    name: string,
    kind: CrawlSource['kind'],
    scope_text: string,
    format_label: string,
    schedule_text: string,
    health: CrawlSource['health'],
    health_text: string,
    last_run_text: string | null,
    sample_accuracy_pct: number | null,
    pending_review: number,
    review_gate: CrawlSource['review_gate'],
  ): CrawlSource => ({
    id,
    name,
    kind,
    scope_text,
    format_label,
    schedule_text,
    health,
    health_text,
    last_run_text,
    sample_accuracy_pct,
    pending_review,
    review_gate,
    drift_id: health === 'drift_quarantined' ? 'drift-fees-0710' : null,
    review_run_id: id === 'cs-deposit' ? 'crun-deposit-1010' : null,
  });
  return [
    s(
      'cs-deposit',
      'Lãi suất huy động · 30 ngân hàng',
      'web_listing',
      '30 miền',
      'Bảng HTML',
      'Hằng ngày 08:00',
      'on_schedule',
      'Đúng lịch',
      '08:05 · 412 bản ghi',
      99.1,
      3,
      'exceptions',
    ),
    s(
      'cs-sbv-rate',
      'Lãi suất điều hành NHNN',
      'web_page',
      'sbv.gov.vn',
      'Bảng HTML',
      'Hằng ngày 07:00',
      'on_schedule',
      'Đúng lịch',
      '07:02 · 6 bản ghi',
      100,
      0,
      'auto',
    ),
    s(
      'cs-fx',
      'Tỷ giá trung tâm NHNN',
      'public_api',
      'JSON',
      'API JSON',
      'Hằng ngày 08:30',
      'on_schedule',
      'Đúng lịch',
      '08:31 · 24 bản ghi',
      100,
      0,
      'auto',
    ),
    s(
      'cs-cpi',
      'CPI Tổng cục Thống kê',
      'web_listing',
      'gso.gov.vn',
      'Excel',
      'Ngày 6 hằng tháng',
      'on_schedule',
      'Đúng lịch',
      '06/10 · 312 bản ghi',
      98.0,
      0,
      'exceptions',
    ),
    s(
      'cs-fees',
      'Biểu phí dịch vụ · 12 ngân hàng',
      'web_listing',
      'PDF',
      'PDF có bảng',
      'Thứ Hai',
      'drift_quarantined',
      'Đổi cấu trúc · cách ly',
      '07/10 · 186 bản ghi',
      null,
      12,
      'all',
    ),
    s(
      'cs-mortgage',
      'Lãi suất cho vay mua nhà',
      'web_page',
      '8 miền',
      'Bảng HTML',
      'Hằng ngày 09:00',
      'blocked',
      'Bị chặn 403 · 3 lượt',
      '07/10',
      97.2,
      0,
      'exceptions',
    ),
    s(
      'cs-gold',
      'Giá vàng SJC',
      'web_page',
      'sjc.com.vn',
      'Bảng HTML',
      'Mỗi giờ',
      'on_schedule',
      'Đúng lịch',
      '10:00 · 8 bản ghi',
      100,
      0,
      'auto',
    ),
    s(
      'cs-bonds',
      'Lợi suất trái phiếu Chính phủ',
      'public_api',
      'XML',
      'XML',
      'Hằng ngày 17:00',
      'on_schedule',
      'Đúng lịch',
      '09/10 · 10 bản ghi',
      100,
      0,
      'auto',
    ),
    s(
      'cs-online',
      'Lãi suất tiết kiệm online',
      'web_page',
      '15 miền',
      'Bảng HTML',
      'Hằng ngày 08:00',
      'pending_schema',
      'Chờ duyệt schema',
      null,
      null,
      8,
      'all',
    ),
  ];
}

const SUMMARY: AcquireSummary = {
  active: 7,
  total: 9,
  paused: 1,
  blocked: 1,
  last_agent_run_text: 'Agent Thu thập · 10/10 08:05',
  sample_accuracy_pct: 98.6,
  accuracy_delta_text: '+0,4 điểm so với tuần trước',
  sample_text: '2% bản ghi mỗi lượt, 4 lượt gần nhất',
  pending_records: 23,
  blocking_records: 4,
  latest_dataset: {
    published_at: '2026-10-10T08:20:00+07:00',
    title: 'Lãi suất huy động tuần 41',
    detail: 'Dataset v41 · 30 ngân hàng',
    consolidation_run_id: 'cons-deposit-w41',
  },
};

const SUGGESTION: SourceSuggestion = {
  suggested_name: 'Lãi suất huy động · 30 ngân hàng',
  sources: [
    {
      key: 'a',
      label: 'Ngân hàng A',
      seed_url: 'nganhanga.vn/lai-suat',
      domain: 'nganhanga.vn',
      format_label: 'Bảng HTML',
      robots: 'allowed',
    },
    {
      key: 'b',
      label: 'Ngân hàng B',
      seed_url: 'nganhangb.com.vn/bieu-lai-suat',
      domain: 'nganhangb.com.vn',
      format_label: 'PDF có bảng',
      robots: 'allowed',
    },
    {
      key: 'c',
      label: 'Ngân hàng C',
      seed_url: 'api.nganhangc.vn/rates',
      domain: 'nganhangc.vn',
      format_label: 'API JSON',
      robots: 'allowed',
    },
    {
      key: 'd',
      label: 'Ngân hàng D',
      seed_url: 'nganhangd.vn/lai-suat',
      domain: 'nganhangd.vn',
      format_label: 'Bảng HTML',
      robots: 'forbidden',
    },
  ],
  more: {
    count: 26,
    allowed: 26,
    seed_urls: Array.from({ length: 26 }, (_, i) => `bank${i + 5}.vn/lai-suat`),
    domains: Array.from({ length: 26 }, (_, i) => `bank${i + 5}.vn`),
  },
  render_js_domains: 2,
};

// "P14 · Suy ngược schema · Chạy thử" (2389:15042): the draft every source starts from.
function draftSchema(sourceId: string): CrawlSchemaVersion {
  const f = (
    name: string,
    type: CrawlSchemaVersion['fields'][number]['type'],
    unit: string | null,
    required: boolean,
    key: boolean,
    locator: string,
    canonical: string | null,
    confidence: number,
    canonical_options: string[] = [],
  ) => ({ name, type, unit, required, key, locator, canonical, canonical_options, confidence });
  return {
    id: `schema-${sourceId}-v1`,
    version: 1,
    status: 'draft',
    summary_text:
      'Agent Thu thập đọc 6 mẫu từ 5 ngân hàng, đề xuất 7 trường và chạy thử trên 24 trang giữ lại',
    sample: {
      url: 'nganhanga.vn/lai-suat',
      fetched_text: 'Tải 10/10 08:05 · SHA-256 9f2c…a41 · HTML',
      columns: ['Kỳ hạn', 'Lãi suất (%/năm)', 'Lĩnh lãi'],
      rows: [
        ['1 tháng', '3,10', 'Cuối kỳ'],
        ['3 tháng', '3,40', 'Cuối kỳ'],
        ['6 tháng', '4,60', 'Cuối kỳ'],
        ['12 tháng', '4,85', 'Cuối kỳ'],
        ['24 tháng', '5,10', 'Hằng tháng'],
      ],
      highlighted_row: 2,
      footer_text: 'Áp dụng từ 01/10/2026 · Tại quầy',
      repeat_text: 'Vùng lặp: table#bieu-lai-suat > tbody > tr · có trên 6/6 mẫu',
    },
    samples_read: [
      'Ngân hàng A · HTML · 2 trang (tại quầy, online)',
      'Ngân hàng B · PDF có bảng · đọc qua Document service',
      'Ngân hàng C · API JSON · $.rates[*]',
      'Ngân hàng E, G · HTML',
    ],
    fields: [
      f('bank_code', 'text', null, true, true, 'miền nguồn → mã chuẩn', 'Ngân hàng', 1),
      f('term_months', 'integer', 'tháng', true, true, 'td:nth-child(1)', 'Kỳ hạn', 0.97),
      f('rate', 'numeric', '%/năm', true, false, 'td:nth-child(2)', 'Lãi suất huy động', 0.95),
      f('payment', 'text', null, false, false, 'td:nth-child(3)', 'Hình thức lĩnh lãi', 0.84),
      f('channel', 'text', null, false, true, 'caption: “Tại quầy”', null, 0.78, [
        'Kênh giao dịch',
        'Loại sản phẩm',
      ]),
      f(
        'effective_date',
        'date',
        null,
        true,
        true,
        '“Áp dụng từ dd/mm/yyyy”',
        'Ngày hiệu lực',
        0.92,
      ),
      f('currency', 'text', null, true, false, 'mặc định VND', 'Tiền tệ', 1),
    ],
    test: { pages: 24, records: 117, required_correct_pct: 97.9 },
  };
}

// "P14 · Thay đổi cấu trúc · Hoàn tác" (2389:15400): the fee-schedule PDFs changed layout.
function driftFixture(): SchemaDrift {
  return {
    id: 'drift-fees-0710',
    source_id: 'cs-fees',
    source_name: 'Biểu phí dịch vụ · 12 ngân hàng',
    title: 'Thay đổi cấu trúc nặng · lượt 07/10',
    summary_text:
      'Điểm lệch 0,62 vượt ngưỡng 0,4: nguồn đã tạm ngưng, dữ liệu lượt bị cách ly, chờ người quyết (BR-152) · loại: cấu trúc',
    drift_kind: 'structural',
    drift_score: 0.62,
    thresholds: { light: 0.15, heavy: 0.4 },
    signals: [
      { name: 'Cấu trúc', measure: 'Tiêu đề cột khác 3/7 cột', value: 0.48, weight: 0.3 },
      {
        name: 'Trích được',
        measure: 'Mất trường khoá fee_code; bắt buộc còn 64%',
        value: 0.78,
        weight: 0.5,
      },
      {
        name: 'Phân bố',
        measure: 'PSI phí giao dịch so với 10 lượt trước',
        value: 0.35,
        weight: 0.2,
      },
    ],
    impact: [
      { text: '186 bản ghi của lượt 07/10 đang cách ly, chưa phát hành', tone: 'warning' },
      {
        text: 'Dataset hợp nhất “Biểu phí dịch vụ” giữ phiên bản v12, chưa cập nhật',
        tone: 'info',
      },
      { text: 'Không câu trả lời nào đã dùng số của lượt này', tone: 'success' },
      { text: 'Bản gốc 48 tệp PDF còn lưu 400 ngày: trích lại được toàn bộ', tone: 'success' },
    ],
    active_version: { id: 'schema-cs-fees-v3', version: 3 },
    proposed_version: { id: 'schema-cs-fees-v4', version: 4 },
    changes: [
      {
        change: 'modify',
        field: 'fee_code',
        before: 'Cột “Mã phí”',
        after: 'Cột “Mã dịch vụ”',
        test_text: '12/12 tệp khớp',
      },
      {
        change: 'add',
        field: 'fee_min',
        before: '—',
        after: 'Cột “Phí tối thiểu (VNĐ)”',
        test_text: '12/12 tệp khớp',
      },
      {
        change: 'modify',
        field: 'fee_rate',
        before: 'Cột 3 · đơn vị %',
        after: 'Cột 4 · % giá trị giao dịch',
        test_text: '11/12 · 1 tệp scan',
      },
      {
        change: 'remove',
        field: 'note',
        before: 'Cột 5 “Ghi chú”',
        after: 'Không còn trong mẫu mới',
        test_text: '—',
      },
    ],
    revert_steps: [
      'Đặt lại phiên bản active về v3; v4 nháp chuyển rejected.',
      'Bản ghi trích bằng phiên bản lỗi chuyển superseded; không sửa dữ liệu đã phát hành.',
      'Trích lại 48 tệp gốc bằng v3; tệp không trích được đưa vào hàng duyệt.',
      'Chạy lại hợp nhất, phát hành dataset phiên bản mới; câu trả lời đã dùng số cũ được gắn nhãn.',
    ],
    resolution: null,
  };
}

// "P14 · Duyệt dữ liệu thu thập" (2390:14372): the deposit-rate run of 10/10 and its queue.
function runFixture(): CrawlRun {
  return {
    id: 'crun-deposit-1010',
    source_id: 'cs-deposit',
    source_name: 'Lãi suất huy động',
    label: 'lượt 10/10',
    status: 'extracted',
    record_count: 412,
    review_count: 23,
    blocking_count: 4,
    sample_count: 5,
    sample_pct: 2,
    accuracy: {
      sample_reviewed: 3,
      sample_accuracy_pct: 100,
      recent_runs_ok: true,
      gate: 'exceptions',
      gate_note: 'đủ điều kiện bật Tự động',
    },
  };
}

function recordsFixture(): ExtractedRecord[] {
  const raw = (
    rows: string[][],
    highlighted: number | null,
    footnote: string | null,
    caption: string,
  ) => ({
    caption,
    columns: ['Kỳ hạn', 'Lãi suất (%/năm)'],
    rows,
    highlighted_row: highlighted,
    footnote,
  });
  const r = (
    id: string,
    title: string,
    value_text: string,
    reasons: ExtractedRecord['reasons'],
    reason_text: string,
    blocking: boolean,
    extra: Partial<ExtractedRecord> = {},
  ): ExtractedRecord => ({
    id,
    title,
    value_text,
    reasons,
    reason_text,
    blocking,
    status: 'pending',
    raw: raw([['12 tháng', value_text.replace('%', '')]], 0, null, 'Bản gốc'),
    values: [{ field: 'rate', value: value_text, confidence: 0.9 }],
    evidence: null,
    agent_note: null,
    proposed_values: null,
    ...extra,
  });
  return [
    r(
      'rec-k-12',
      'Ngân hàng K · 12 tháng',
      '6,85%',
      ['cross_source_anomaly'],
      'Bất thường chéo nguồn',
      true,
      {
        raw: raw(
          [
            ['6 tháng', '4,70'],
            ['12 tháng', '6,85 (*)'],
            ['24 tháng', '5,20'],
          ],
          1,
          '(*) Áp dụng cho khoản tiền gửi từ 500 tỷ đồng',
          'Bản gốc · nganhangk.vn/lai-suat · 08:04',
        ),
        values: [
          { field: 'rate', value: '6,85 %/năm', confidence: 0.96 },
          { field: 'term_months', value: '12', confidence: 0.98 },
          { field: 'channel', value: 'Tại quầy', confidence: 0.9 },
        ],
        evidence: 'Trung vị 29 ngân hàng khác cùng kỳ: 4,85% · lệch 4,1 MAD (ngưỡng 3,5)',
        agent_note:
          'Số đúng như trang nguồn nhưng có ghi chú chân bảng; có thể là lãi suất riêng cho tiền gửi lớn. Đề xuất: sửa kênh thành “Tiền gửi từ 500 tỷ”, không tính vào bình quân thị trường.',
        proposed_values: { channel: 'Tiền gửi từ 500 tỷ' },
      },
    ),
    r(
      'rec-b-6',
      'Ngân hàng B · 6 tháng',
      '4,6?%',
      ['low_confidence'],
      'Độ tin thấp 0,71 · PDF',
      true,
    ),
    r(
      'rec-t-24',
      'Ngân hàng T · 24 tháng',
      '—',
      ['missing_required'],
      'Thiếu trường bắt buộc',
      true,
    ),
    r(
      'rec-q-1',
      'Ngân hàng Q · 1 tháng',
      '0,30%',
      ['source_anomaly'],
      'Bất thường theo nguồn',
      true,
    ),
    r(
      'rec-a-online',
      'Ngân hàng A · online 12 tháng',
      '5,05%',
      ['new_value'],
      'Giá trị mới: kênh “Ví điện tử”',
      false,
    ),
    r('rec-g-3', 'Ngân hàng G · 3 tháng', '3,40%', ['random_sample'], 'Mẫu ngẫu nhiên', false),
  ];
}

// "P14 · Hợp nhất dữ liệu" (2390:14727): week 41 of the market deposit-rate dataset.
function consolidationFixture(): ConsolidationRun {
  return {
    id: 'cons-deposit-w41',
    spec_name: 'Lãi suất huy động thị trường',
    title: 'Hợp nhất dữ liệu · tuần 41',
    summary_text: '3 nguồn thu · 30 ngân hàng · 412 bản ghi vào, 148 dòng hợp nhất',
    status: 'pending_review',
    next_dataset_version: 42,
    steps: [
      { label: 'Chuẩn hoá', state: 'done' },
      { label: 'Thực thể', state: 'waiting' },
      { label: 'Khử trùng lặp', state: 'done' },
      { label: 'Đối chiếu', state: 'waiting' },
      { label: 'Bảng hợp nhất', state: 'done' },
      { label: 'Tổng quát', state: 'done' },
      { label: 'Phát hành', state: 'pending' },
      { label: 'Nhúng', state: 'pending' },
    ],
    table: {
      title: 'Bảng tổng hợp · tuần 41 · tại quầy · %/năm',
      columns: ['Kỳ hạn', 'Trung bình', 'Trung vị', 'Thấp nhất', 'Cao nhất', 'Số nguồn'],
      rows: [
        ['1 tháng', '3,02', '3,00', '2,10', '3,60', '30'],
        ['3 tháng', '3,35', '3,40', '2,40', '3,90', '30'],
        ['6 tháng', '4,52', '4,55', '3,30', '5,30', '30'],
        ['12 tháng', '4,85', '4,80', '3,90', '5,60', '29'],
        ['24 tháng', '5,02', '5,00', '4,10', '5,70', '28'],
      ],
      highlighted_row: 3,
      note: 'Ngân hàng K kỳ hạn 12 tháng (6,85%, tiền gửi từ 500 tỷ) không tính vào bình quân theo quyết định ở hàng duyệt.',
    },
    summary_paragraph: {
      text: 'Lãi suất huy động tại quầy kỳ hạn 12 tháng bình quân của 29 ngân hàng tuần 41 là 4,85%/năm, tăng 0,12 điểm so với tuần 40; cao nhất 5,60% (Ngân hàng P), thấp nhất 3,90%. 7 ngân hàng tăng, 1 ngân hàng giảm.',
      provenance:
        'Mọi số lấy từ bảng tổng hợp · 29 trang gốc · nhãn nguồn ngoài · độ tin cậy nguồn 0,7',
    },
    entity_matches: [
      {
        id: 'em-vietbank',
        alias: '“VietBank”',
        canonical: 'Ngân hàng TMCP Việt Nam Thương Tín',
        score: 0.82,
        reason: 'khớp tên rút gọn và website',
        decision: null,
      },
      {
        id: 'em-bv',
        alias: '“Ngân hàng BV”',
        canonical: 'Ngân hàng TMCP Bản Việt',
        score: 0.76,
        reason: 'cùng website, khác tên viết tắt',
        decision: null,
      },
    ],
    conflicts: [
      {
        text: 'Ngân hàng M · 12 tháng: website 5,10% · API 4,90% · lệch 0,20 điểm, trên ngưỡng trọng yếu 0,05 điểm → đã tạo yêu cầu so sánh hai nguồn trong Cần bạn',
        open: true,
      },
    ],
  };
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createCrawlHandlers() {
  const sources = sourcesFixture();
  const schemas = new Map<string, CrawlSchemaVersion>();
  let drift = driftFixture();
  let run = runFixture();
  let consolidation = consolidationFixture();
  let records = recordsFixture();
  const pending = () => records.filter((x) => x.status === 'pending');
  const recount = () => {
    run = {
      ...run,
      review_count: pending().length,
      blocking_count: pending().filter((x) => x.blocking).length,
    };
  };
  // The header counts what the queue holds, so they stay true as records are decided.
  recount();
  const schemaOf = (sourceId: string) => {
    if (!schemas.has(sourceId)) schemas.set(sourceId, draftSchema(sourceId));
    return schemas.get(sourceId) as CrawlSchemaVersion;
  };
  return [
    http.get('*/v1/consolidation-runs/:id', ({ params }) =>
      params.id === consolidation.id
        ? HttpResponse.json(consolidation)
        : problem(404, 'not_found', 'Không tìm thấy lượt hợp nhất.'),
    ),
    http.post('*/v1/entity-matches/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const match = consolidation.entity_matches.find((m) => m.id === id);
      if (!match || verb !== 'decide') return problem(404, 'not_found', 'Không tìm thấy liên kết.');
      const { decision } = (await request.json()) as { decision: 'merge' | 'split' };
      consolidation = {
        ...consolidation,
        entity_matches: consolidation.entity_matches.map((m) =>
          m.id === id ? { ...m, decision } : m,
        ),
      };
      // The entity step is done once no link waits on a person.
      if (consolidation.entity_matches.every((m) => m.decision))
        consolidation = {
          ...consolidation,
          steps: consolidation.steps.map((st) =>
            st.label === 'Thực thể' ? { ...st, state: 'done' } : st,
          ),
        };
      return HttpResponse.json({ id, decision });
    }),
    http.post('*/v1/consolidation-runs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== consolidation.id || verb !== 'publish')
        return problem(404, 'not_found', 'Không tìm thấy lượt hợp nhất.');
      const waiting =
        consolidation.entity_matches.filter((m) => !m.decision).length +
        consolidation.conflicts.filter((c) => c.open).length;
      if (waiting)
        return problem(409, 'invalid_state', `Còn ${waiting} mục chờ trước khi phát hành.`);
      consolidation = { ...consolidation, status: 'published' };
      return HttpResponse.json(consolidation);
    }),
    http.get('*/v1/crawl-runs/:id/records', ({ params }) =>
      params.id === run.id
        ? HttpResponse.json({ items: pending(), next_page_token: '' })
        : problem(404, 'not_found', 'Không tìm thấy lượt.'),
    ),
    http.get('*/v1/crawl-runs/:id', ({ params }) =>
      params.id === run.id
        ? HttpResponse.json(run)
        : problem(404, 'not_found', 'Không tìm thấy lượt.'),
    ),
    http.post('*/v1/crawl-runs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== run.id || verb !== 'publish')
        return problem(404, 'not_found', 'Không tìm thấy lượt.');
      if (run.blocking_count > 0)
        return problem(
          422,
          'business_rule_violated',
          `Còn ${run.blocking_count} mục chặn trong hàng duyệt (BR-153).`,
        );
      run = { ...run, status: 'published' };
      return HttpResponse.json(run);
    }),
    http.post('*/v1/extracted-records\\:bulk-review', async ({ request }) => {
      const body = (await request.json()) as {
        review_reasons: string[];
        decision: string;
        reason: string;
      };
      if (run.status === 'published') return problem(409, 'invalid_state', 'Lượt đã phát hành.');
      let updated = 0;
      records = records.map((x) => {
        if (x.status !== 'pending' || !x.reasons.some((rr) => body.review_reasons.includes(rr)))
          return x;
        updated += 1;
        return { ...x, status: body.decision === 'reject' ? 'rejected' : 'confirmed' };
      });
      recount();
      return HttpResponse.json({ updated });
    }),
    http.post('*/v1/extracted-records/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const rec = records.find((x) => x.id === id);
      if (!rec || verb !== 'review') return problem(404, 'not_found', 'Không tìm thấy bản ghi.');
      if (run.status === 'published') return problem(409, 'invalid_state', 'Lượt đã phát hành.');
      const body = (await request.json()) as {
        decision: string;
        corrected_values?: Record<string, string>;
      };
      if (body.decision === 'correct' && !body.corrected_values)
        return problem(422, 'validation_failed', 'Sửa cần giá trị mới.');
      const status =
        body.decision === 'reject'
          ? 'rejected'
          : body.decision === 'correct'
            ? 'corrected'
            : 'confirmed';
      const done: ExtractedRecord = { ...rec, status };
      records = records.map((x) => (x.id === id ? done : x));
      recount();
      return HttpResponse.json(done);
    }),
    http.get('*/v1/schema-drifts', () =>
      HttpResponse.json({ items: drift.resolution ? [] : [drift], next_page_token: '' }),
    ),
    http.get('*/v1/schema-drifts/:id', ({ params }) =>
      params.id === drift.id
        ? HttpResponse.json(drift)
        : problem(404, 'not_found', 'Không tìm thấy sự cố.'),
    ),
    http.post('*/v1/schema-drifts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== drift.id || verb !== 'resolve')
        return problem(404, 'not_found', 'Không tìm thấy sự cố.');
      if (drift.resolution) return problem(409, 'conflict', 'Sự cố đã được xử lý.');
      const body = (await request.json()) as {
        resolution: SchemaDrift['resolution'];
        note?: string;
      };
      if (body.resolution === 'reverted' && !body.note?.trim())
        return problem(422, 'validation_failed', 'Ghi lý do hoàn tác.');
      drift = { ...drift, resolution: body.resolution };
      const source = sources.find((x) => x.id === drift.source_id);
      if (source) {
        source.health = 'on_schedule';
        source.health_text = 'Đúng lịch';
        source.drift_id = null;
      }
      return HttpResponse.json(drift);
    }),
    http.get('*/v1/crawl-sources/:id/schemas', ({ params }) => {
      const source = sources.find((x) => x.id === params.id);
      if (!source) return problem(404, 'not_found', 'Không tìm thấy nguồn.');
      return HttpResponse.json({ items: [schemaOf(source.id)], next_page_token: '' });
    }),
    http.get('*/v1/crawl-sources/:id', ({ params }) => {
      const source = sources.find((x) => x.id === params.id);
      return source
        ? HttpResponse.json({ id: source.id, name: source.name })
        : problem(404, 'not_found', 'Không tìm thấy nguồn.');
    }),
    http.post('*/v1/crawl-schemas/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const entry = [...schemas.entries()].find(([, v]) => v.id === id);
      if (!entry) return problem(404, 'not_found', 'Không tìm thấy schema.');
      const [sourceId, schema] = entry;
      if (verb === 'test')
        return HttpResponse.json({ id: 'arun-test-' + Date.now() }, { status: 202 });
      if (verb !== 'approve') return problem(404, 'not_found', 'Không có thao tác này.');
      if (!schema.test) return problem(409, 'invalid_state', 'Chưa chạy thử.');
      if (schema.test.required_correct_pct < 95)
        return problem(422, 'business_rule_violated', 'Dưới 95% trường bắt buộc đúng (BR-151).');
      const { field_mappings } = (await request.json()) as {
        field_mappings: Record<string, string>;
      };
      const unresolved = schema.fields.filter((x) => !x.canonical && !field_mappings?.[x.name]);
      if (unresolved.length)
        return problem(
          422,
          'validation_failed',
          `Chọn thuộc tính chuẩn cho ${unresolved.map((x) => x.name).join(', ')}.`,
        );
      const approved: CrawlSchemaVersion = {
        ...schema,
        status: 'active',
        fields: schema.fields.map((x) => ({
          ...x,
          canonical: x.canonical ?? field_mappings[x.name],
        })),
      };
      schemas.set(sourceId, approved);
      const source = sources.find((x) => x.id === sourceId);
      if (source && source.health === 'pending_schema') {
        source.health = 'on_schedule';
        source.health_text = 'Đúng lịch';
      }
      return HttpResponse.json(approved);
    }),
    http.post('*/v1/crawl-sources\\:suggest', async ({ request }) => {
      const { goal } = (await request.json()) as { goal: string };
      if (!goal?.trim()) return problem(422, 'validation_failed', 'Mô tả dữ liệu cần thu.');
      return HttpResponse.json(SUGGESTION);
    }),
    http.post('*/v1/crawl-sources', async ({ request }) => {
      const body = (await request.json()) as {
        name: string;
        seed_urls: string[];
        rate_limit_rps: number;
      };
      if (!body.seed_urls?.length)
        return problem(422, 'validation_failed', 'Chọn ít nhất một nguồn.');
      // BR-149: at most one request per second per domain.
      if (body.rate_limit_rps > 1)
        return problem(
          422,
          'business_rule_violated',
          'Tối đa 1 yêu cầu mỗi giây mỗi miền (BR-149).',
        );
      const id = 'cs-new-' + sources.length;
      sources.push({
        id,
        name: body.name,
        kind: 'web_listing',
        scope_text: `${body.seed_urls.length} miền`,
        format_label: 'Nhiều định dạng',
        schedule_text: 'Hằng ngày 08:00',
        health: 'pending_schema',
        health_text: 'Chờ duyệt schema',
        last_run_text: null,
        sample_accuracy_pct: null,
        pending_review: 0,
        review_gate: 'exceptions',
        drift_id: null,
        review_run_id: null,
      });
      return HttpResponse.json({ id }, { status: 201 });
    }),
    http.post('*/v1/crawl-sources/:id/schema-inference', () =>
      HttpResponse.json({ id: 'arun-infer-' + Date.now() }, { status: 202 }),
    ),
    http.get('*/v1/crawl-sources', () =>
      HttpResponse.json({ items: sources, next_page_token: '' }),
    ),
    http.get('*/v1/acquire/summary', () => HttpResponse.json(SUMMARY)),
    http.post('*/v1/crawl-sources/:id/runs', ({ params }) => {
      const source = sources.find((x) => x.id === params.id);
      if (!source) return problem(404, 'not_found', 'Không tìm thấy nguồn.');
      if (source.health !== 'on_schedule')
        return problem(
          409,
          'invalid_state',
          `Nguồn chưa chạy được: ${source.health_text.toLowerCase()}.`,
        );
      return HttpResponse.json({ id: `crun-${Date.now()}` }, { status: 202 });
    }),
  ];
}
