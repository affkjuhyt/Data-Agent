import { http, HttpResponse } from 'msw';
import type {
  AcquireSummary,
  CrawlSchemaVersion,
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

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createCrawlHandlers() {
  const sources = sourcesFixture();
  const schemas = new Map<string, CrawlSchemaVersion>();
  let drift = driftFixture();
  const schemaOf = (sourceId: string) => {
    if (!schemas.has(sourceId)) schemas.set(sourceId, draftSchema(sourceId));
    return schemas.get(sourceId) as CrawlSchemaVersion;
  };
  return [
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
