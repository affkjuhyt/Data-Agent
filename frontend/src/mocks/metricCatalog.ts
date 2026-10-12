import { http, HttpResponse } from 'msw';
import type { CatalogMetric, CatalogMetricWire, MetricProfile } from '../lib/v1/metricCatalog';

/*
 * "Dữ liệu & tri thức" (2056:1150, tabs 2099:11054, 2099:11344, 2099:11632) for the dev mock: six
 * metrics of the catalog and the full profile of NPL_RATIO; the others get a short one. GET
 * /v1/metrics answers here only with ?view=catalog; the plain list stays with handlers.ts.
 */
function catalogFixture(): CatalogMetric[] {
  return [
    {
      id: 'metric-npl-ratio',
      code: 'NPL_RATIO',
      version: 4,
      name: 'Tỷ lệ nợ xấu',
      status: 'certified',
      domain: 'Ngân hàng',
      usage_text: '5 agent · 23 artifact',
    },
    {
      id: 'metric-casa',
      code: 'CASA_RATIO',
      version: 3,
      name: 'Tỷ lệ tiền gửi không kỳ hạn',
      status: 'certified',
      domain: 'Ngân hàng',
      usage_text: '3 agent · 11 artifact',
    },
    {
      id: 'metric-ldr',
      code: 'LDR',
      version: 2,
      name: 'Tỷ lệ cho vay trên huy động',
      status: 'certified',
      domain: 'Ngân hàng',
      usage_text: '2 agent · 9 artifact',
    },
    {
      id: 'metric-cost-of-risk',
      code: 'COST_OF_RISK',
      version: 1,
      name: 'Chi phí rủi ro tín dụng',
      status: 'draft',
      domain: 'Ngân hàng',
      usage_text: 'do Agent Phân tích đề xuất',
    },
    {
      id: 'metric-gross-margin',
      code: 'GROSS_MARGIN',
      version: 4,
      name: 'Biên lợi nhuận gộp',
      status: 'certified',
      domain: 'Bán lẻ',
      usage_text: 'domain Bán lẻ · 2 agent',
    },
    {
      id: 'metric-ar-aging',
      code: 'AR_AGING',
      version: 2,
      name: 'Tuổi nợ phải thu',
      status: 'certified',
      domain: 'Tài chính',
      usage_text: 'domain Tài chính · 1 agent',
    },
  ];
}

function profileFixture(id: string): MetricProfile | null {
  const m = catalogFixture().find((x) => x.id === id);
  if (!m) return null;
  const base: MetricProfile = {
    id: m.id,
    code: m.code,
    name: m.name,
    status: m.status,
    meta_line: `${m.code} · v${m.version} · domain ${m.domain}`,
    definition: {
      expression: `${m.code} = …`,
      note: 'Định nghĩa đầy đủ ở trang Chỉ số · Định nghĩa.',
    },
    lineage: [],
    agents: [],
    change_notice: null,
    versions: [
      {
        version: m.version,
        change: 'Phiên bản hiện hành',
        effective: '—',
        status: m.status === 'draft' ? 'draft' : 'certified',
      },
    ],
    usage: [],
    usage_note: m.usage_text,
    quality: [],
  };
  if (id !== 'metric-npl-ratio') return base;
  return {
    ...base,
    meta_line: 'NPL_RATIO · v4 · hiệu lực từ 01/03/2026 · chủ sở hữu Trần Quốc An',
    definition: {
      expression:
        'NPL_RATIO = SUM(balance WHERE debt_group IN (3,4,5))\n          / SUM(balance)\nlọc: segment, region, product · thời điểm: cuối kỳ',
      note: 'Dư nợ nhóm 3–5 chia tổng dư nợ cho vay khách hàng tại ngày cuối kỳ. Agent chỉ dùng phiên bản chứng nhận trừ khi người dùng chọn khác; mọi câu trả lời ghi rõ phiên bản.',
    },
    lineage: [
      { kind: 'Nguồn', title: 'Core banking', detail: 'ld_loans' },
      { kind: 'Kho', title: 'DWH', detail: 'fact_loan_balance_daily' },
      { kind: 'Snapshot', title: 'Phiên bản theo ngày', detail: 'loan_balance_snapshot' },
      { kind: 'Chỉ số', title: 'NPL_RATIO v4', detail: '', current: true },
      { kind: 'Sử dụng', title: '23 artifact · 5 agent', detail: 'báo cáo, notebook, cảnh báo' },
    ],
    agents: [
      { name: 'Agent Giám sát rủi ro', state: 'Đang chạy', tone: 'success' },
      { name: 'Agent Phân tích', state: 'Chờ bạn', tone: 'warning' },
      { name: 'Agent Đối soát', state: 'Đang chạy', tone: 'success' },
      { name: 'Agent Báo cáo', state: 'Đang chạy', tone: 'success' },
      { name: 'Agent Dự báo', state: 'Chờ lịch', tone: 'idle' },
    ],
    change_notice: {
      title: 'Agent Tài liệu: văn bản mới thay đổi tiêu chí nợ nhóm 2',
      text: 'Đã soạn NPL_RATIO v5 (nháp) và chạy thử: kết quả T9 thay đổi +0,03 điểm, ảnh hưởng 4 báo cáo.',
      draft_version: 5,
    },
    versions: [
      {
        version: 5,
        change: 'Tính khoản cơ cấu lần 2 vào nhóm 3',
        effective: '01/01/2027',
        status: 'draft',
      },
      {
        version: 4,
        change: 'Loại trừ cho vay nội bộ',
        effective: '01/03/2026',
        status: 'certified',
      },
      { version: 3, change: 'Thêm phân khúc', effective: '01/09/2025', status: 'archived' },
      {
        version: 2,
        change: 'Chuẩn hoá theo thông tư 2024',
        effective: '01/01/2025',
        status: 'archived',
      },
    ],
    usage: [
      { place: 'Báo cáo rủi ro tháng 9', kind: 'Báo cáo', version: 'v4', last: '08/10' },
      { place: 'Agent Giám sát rủi ro', kind: 'Agent', version: 'v4', last: '12:00' },
      { place: 'Notebook “Điều tra nợ xấu”', kind: 'Phiên', version: 'v4', last: '09:58' },
      { place: 'mart_credit_risk_retail', kind: 'Data mart', version: 'v4', last: '06:10' },
      { place: 'Kỹ năng Phân rã biến động', kind: 'Kỹ năng', version: 'v4', last: 'hôm qua' },
    ],
    usage_note: '23 artifact · 5 agent · 9 phiên trong 30 ngày',
    quality: [
      { ok: true, label: 'Đạt', text: 'Khớp số đã nộp 6/6 kỳ gần nhất' },
      { ok: true, label: 'Đạt', text: 'Dữ liệu nguồn mới tới 30/09 23:59' },
      { ok: true, label: 'Đạt', text: 'Không âm, không vượt 100%' },
      { ok: false, label: 'Cảnh báo', text: 'Tây Nguyên thiếu 2 chi nhánh trong T8 (đã ghi chú)' },
    ],
  };
}

export function createMetricCatalogHandlers() {
  return [
    http.get('*/v1/metrics', ({ request }) => {
      if (new URL(request.url).searchParams.get('view') !== 'catalog') return undefined;
      // MetricView as the api sends it, with the PROPOSAL catalog fields.
      const items: CatalogMetricWire[] = catalogFixture().map((m) => ({
        id: m.id,
        code: m.code,
        display_name: m.name,
        name: m.name,
        version: m.version,
        domain: m.domain,
        usage_text: m.usage_text,
        status: 'active',
        certified: m.status === 'certified',
        current_version: m.version,
        data_space_id: 'ds-rui-ro-tin-dung',
        owner_id: 'u-long',
        canonical_dataset_id: null,
        consumer_group: null,
        in_filed_report: false,
        pack_metric_id: null,
        created_at: '2026-01-05T09:00:00+07:00',
        updated_at: '2026-10-08T09:00:00+07:00',
      }));
      return HttpResponse.json({ items, next_page_token: '' });
    }),
    http.get('*/v1/metrics/:id/profile', ({ params }) => {
      const profile = profileFixture(String(params.id));
      return profile
        ? HttpResponse.json(profile)
        : HttpResponse.json(
            { title: 'not_found', status: 404, code: 'not_found', detail: 'Không có chỉ số này.' },
            { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
          );
    }),
  ];
}
