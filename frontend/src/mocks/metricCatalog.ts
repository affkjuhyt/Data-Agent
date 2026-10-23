import { http, HttpResponse } from 'msw';
import type { CatalogMetric, CatalogMetricWire } from '../lib/v1/metricCatalog';
import type { components } from '../lib/v1/schema';

type Schemas = components['schemas'];

/*
 * "Dữ liệu & tri thức" (2056:1150, tabs 2099:11054, 2099:11344, 2099:11632) for the dev mock: six
 * metrics of the catalog, one metric (GET /v1/metrics/{id}) for those mocks/metrics.ts does not
 * answer, and the lineage of a metric. GET /v1/metrics answers here only with ?view=catalog; the
 * plain list stays with handlers.ts.
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

/*
 * One metric as A2 sends it. NPL_RATIO (its versions) and COST_OF_RISK (the 403 of "Thiếu
 * quyền") are answered by mocks/metrics.ts; the other catalog metrics get a MetricDetail here.
 */
const ANSWERED_BY_METRICS_MOCK = ['metric-npl-ratio', 'metric-cost-of-risk'];

function detailFixture(m: CatalogMetric): Schemas['MetricDetail'] {
  const at = '2026-01-05T09:00:00+07:00';
  return {
    id: m.id,
    code: m.code,
    display_name: m.name,
    status: 'active',
    certified: m.status === 'certified',
    current_version: m.version,
    data_space_id: 'ds-rui-ro-tin-dung',
    owner_id: 'u-long',
    canonical_dataset_id: null,
    consumer_group: null,
    in_filed_report: false,
    pack_metric_id: null,
    created_at: at,
    updated_at: at,
    usage: { data_product_ids: [], knowledge_page_ids: [] },
    versions: [
      {
        id: `mv-${m.id}-v${m.version}`,
        metric_id: m.id,
        version: m.version,
        display_name: m.name,
        expression: `${m.code} = …`,
        description: 'Định nghĩa đầy đủ ở trang Chỉ số · Định nghĩa.',
        change_note: 'Phiên bản hiện hành',
        status: m.status === 'certified' ? 'certified' : 'draft',
        additivity: 'none',
        allowed_dimensions: [],
        filters: [],
        decimals: 2,
        direction: 'neutral',
        period_semantics: 'end_of_period',
        time_grain: 'month',
        unit: null,
        legal_basis: null,
        override_id: null,
        proposal_id: null,
        valid_from: null,
        valid_to: null,
        certified_at: null,
        certified_by: null,
        created_at: at,
      },
    ],
  };
}

type Node = Schemas['LineageNode'];
const node = (key: string, kind: string, name: string): Node => ({
  id: null,
  key,
  kind,
  name,
  hidden: false,
});

/** GET /v1/lineage?object_kind=metric: NPL_RATIO from core banking to the agents using it. */
function lineageFixture(id: string, code: string): Schemas['LineageGraph'] {
  const root = node(id, 'metric', code);
  if (id !== 'metric-npl-ratio')
    return { root, nodes: [root], edges: [], depth: 3, direction: 'both', grouped: false };
  const nodes = [
    node('src-core', 'source', 'Core banking'),
    node('ds-loans', 'dataset', 'fact_loan_balance_daily'),
    root,
    node('report-t9', 'report', 'Báo cáo rủi ro tháng 9'),
    node('agent-risk', 'agent', 'Agent Giám sát rủi ro'),
    node('agent-analysis', 'agent', 'Agent Phân tích'),
    node('skill-decompose', 'skill', 'Kỹ năng Phân rã biến động'),
  ];
  const edge = (source: string, target: string) => ({ source, target, via: null, via_ref: null });
  return {
    root,
    nodes,
    edges: [
      edge('src-core', 'ds-loans'),
      edge('ds-loans', id),
      edge(id, 'report-t9'),
      edge(id, 'agent-risk'),
      edge(id, 'agent-analysis'),
      edge(id, 'skill-decompose'),
    ],
    depth: 3,
    direction: 'both',
    grouped: false,
  };
}

export function createMetricCatalogHandlers() {
  return [
    // POST /v1/metrics (steward): the metric and its first draft version; the code is unique.
    http.post('*/v1/metrics', async ({ request }) => {
      const body = (await request.json()) as { code: string; owner_id: string; definition?: object };
      if (catalogFixture().some((m) => m.code === body.code))
        return HttpResponse.json(
          { status: 409, code: 'conflict', title: 'Mã chỉ số đã có.', detail: 'Mã chỉ số đã có.' },
          { status: 409 },
        );
      return HttpResponse.json(
        { id: `metric-${body.code.toLowerCase()}`, code: body.code, owner_id: body.owner_id },
        { status: 201 },
      );
    }),
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
    http.get('*/v1/metrics/:id', ({ params }) => {
      const m = catalogFixture().find((x) => x.id === params.id);
      return m && !ANSWERED_BY_METRICS_MOCK.includes(m.id)
        ? HttpResponse.json(detailFixture(m))
        : undefined;
    }),
    http.get('*/v1/lineage', ({ request }) => {
      const query = new URL(request.url).searchParams;
      const m = catalogFixture().find((x) => x.id === query.get('object_id'));
      return query.get('object_kind') === 'metric' && m
        ? HttpResponse.json(lineageFixture(m.id, m.code))
        : undefined;
    }),
  ];
}
