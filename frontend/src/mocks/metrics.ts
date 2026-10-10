import { http, HttpResponse } from 'msw';
import type { MetricDetail, MetricVersion } from '../lib/v1/metrics';

/*
 * NPL_RATIO as "P07 · Chỉ số · Định nghĩa" (2301:19011) shows it: v4 certified and in use, v3
 * superseded, v5 a draft. Only this mock's metric ids are answered; the metric list stays with
 * the main handlers.
 */
const EXPRESSION = [
  'SUM(outstanding_amt WHERE debt_group IN (3,4,5))',
  '/ SUM(outstanding_amt)',
  'FROM mart.credit_risk.fact_loan_daily',
  "WHERE segment = 'retail' AND is_internal_loan = false",
].join('\n');

function version(
  v: number,
  status: MetricVersion['status'],
  note: string,
  at: string,
): MetricVersion {
  return {
    id: `mv-npl-v${v}`,
    version: v,
    display_name: 'Nợ xấu (%)',
    expression: EXPRESSION,
    unit: '%',
    decimals: 2,
    direction: 'lower_better',
    period_semantics: 'end_of_period',
    time_grain: 'day',
    dimension_labels: ['vùng', 'chi nhánh', 'sản phẩm', 'phân khúc', 'kỳ'],
    change_note: note,
    status,
    certified_at: status === 'certified' || status === 'superseded' ? at : null,
    certified_by_name: status === 'certified' ? 'Phạm Đức Long' : null,
    created_at: at,
  };
}

function metricsFixture(): MetricDetail[] {
  return [
    {
      id: 'metric-npl-ratio',
      code: 'NPL_RATIO',
      status: 'active',
      current_version: 4,
      versions: [
        version(4, 'certified', 'Loại vay nội bộ', '2026-06-12T09:00:00+07:00'),
        version(3, 'superseded', 'Thêm phân khúc', '2026-03-02T09:00:00+07:00'),
        version(5, 'draft', 'Khoản cơ cấu lần 2 vào nhóm 3', '2026-10-08T09:00:00+07:00'),
      ],
      usage: { artifacts: 23, agents: 5, reports: 2 },
      stewardship: [
        { role: 'Chủ sở hữu', who: 'Phạm Đức Long', note: 'Duyệt mọi thay đổi định nghĩa' },
        {
          role: 'Người dùng chính',
          who: 'Ban Rủi ro tín dụng',
          note: 'Nhận thông báo khi đổi phiên bản',
        },
        { role: 'Nguồn chuẩn', who: 'mart.credit_risk', note: 'Khớp báo cáo NHNN hằng tháng' },
      ],
    },
  ];
}

export function createMetricHandlers() {
  let metrics = metricsFixture();
  return [
    http.get('*/v1/metrics/:id', ({ params }) => {
      // "v3.1 · Trạng thái · Thiếu quyền" (2076:5267): a metric over a Data Space the user is not in.
      if (params.id === 'metric-cost-of-risk')
        return HttpResponse.json(
          {
            type: 'about:blank',
            title: 'Forbidden',
            status: 403,
            code: 'forbidden',
            detail: 'Bạn chưa có quyền với Data Space Kế toán.',
            access: {
              title: 'Bạn chưa có quyền với dữ liệu trích lập dự phòng',
              detail:
                'Chỉ số COST_OF_RISK dùng bảng provision_detail thuộc Data Space Kế toán. Chủ sở hữu: Trần Quốc An.',
              data_space_id: 'ds-accounting',
              owner_name: 'Trần Quốc An',
              options: [
                {
                  capability: 'view_aggregate',
                  label: 'Xem tổng hợp theo vùng (không chi tiết khoản vay)',
                },
                { capability: 'view_detail', label: 'Xem chi tiết khoản vay' },
              ],
              reason_hint: 'Điều tra nợ xấu bán lẻ T9 · phiên của Đỗ Hải Yến',
              fallback_text:
                'Trong lúc chờ, agent có thể trả lời ở mức tổng hợp toàn hệ thống mà không lộ dữ liệu bạn chưa được xem.',
            },
          },
          { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
        );
      const metric = metrics.find((m) => m.id === params.id);
      return metric ? HttpResponse.json(metric) : undefined;
    }),
    http.post('*/v1/metrics/:id/versions', async ({ params, request }) => {
      const metric = metrics.find((m) => m.id === params.id);
      if (!metric) return undefined;
      const body = (await request.json()) as Partial<MetricVersion>;
      const next = Math.max(...metric.versions.map((v) => v.version)) + 1;
      const draft: MetricVersion = {
        ...metric.versions.find((v) => v.status === 'certified')!,
        ...body,
        id: `mv-${metric.code.toLowerCase()}-v${next}`,
        version: next,
        status: 'draft',
        certified_at: null,
        certified_by_name: null,
        created_at: new Date().toISOString(),
      };
      metrics = metrics.map((m) =>
        m.id === metric.id ? { ...m, versions: [...m.versions, draft] } : m,
      );
      return HttpResponse.json(draft, { status: 201 });
    }),
    http.post('*/v1/metric-versions/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const owned = metrics.some((m) => m.versions.some((v) => v.id === id));
      if (!owned || verb !== 'preview') return undefined;
      metrics = metrics.map((m) => ({
        ...m,
        versions: m.versions.map((v) => (v.id === id ? { ...v, status: 'testing' } : v)),
      }));
      return HttpResponse.json({ status: 'queued' }, { status: 202 });
    }),
  ];
}
