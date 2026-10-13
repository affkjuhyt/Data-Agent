import { http, HttpResponse } from 'msw';
import type { Onboarding } from '../lib/v1/explore';
import type { components } from '../lib/v1/schema';

/*
 * Khám phá (2140:15461 and its states), the full-screen artifact (2074:4442) and the onboarding
 * screen (2076:5584), in the A3 shapes the api sends: certified metrics (GET /v1/metrics
 * ?certified=true, GET /v1/metrics/{id}), the entities' dimensions (GET /v1/entities
 * ?status=active, GET /v1/entities/{key}), a query_run (POST /v1/explore/queries, A2 Truy vấn
 * GET /v1/query-runs/{id}, /rows, /sql), the decomposition skill run behind "Agent giải thích",
 * a question asked with purpose explore, and one answer opened as an artifact.
 *
 * Each handler answers only its own ids or its own query (certified=true, status=active,
 * category=decomposition, purpose explore) and lets every other request through to the
 * handlers of the screens that own those resources. Figures are stable made-up values.
 */
type Schemas = components['schemas'];

const DS = 'ds-rui-ro-tin-dung';
const AT = '2026-10-08T09:00:00+07:00';

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

/* ─── Truy vấn: query runs shared with the other mocks (SQL editor) ──────── */

interface StoredRun {
  view: Schemas['QueryRunView'];
  columns: { name: string; type: string }[];
  rows: unknown[][];
  sql: string;
}

let queryRuns = new Map<string, StoredRun>();
let runSeq = 0;

/** Records a finished query_run that GET /v1/query-runs/{id}, /rows and /sql then serve. */
export function recordQueryRun(run: {
  columns: { name: string; type: string }[];
  rows: unknown[][];
  sql?: string;
  status?: string;
  error?: string | null;
  caller_kind?: string;
}): Schemas['QueryRunView'] {
  runSeq += 1;
  const view: Schemas['QueryRunView'] = {
    id: `qr-mock-${runSeq}`,
    data_space_id: DS,
    caller_kind: run.caller_kind ?? 'question',
    caller_id: 'u-yen',
    principal_id: 'u-yen',
    source_id: 'src-core',
    route: 'live',
    period_close_id: null,
    policies_applied: [],
    metric_version_ids: [],
    as_of: null,
    row_count: run.status === 'failed' ? null : run.rows.length,
    result_hash: null,
    duration_ms: 800,
    status: run.status ?? 'succeeded',
    error: run.error ?? null,
    started_at: AT,
  };
  queryRuns.set(view.id, { view, columns: run.columns, rows: run.rows, sql: run.sql ?? '' });
  return view;
}

const accepted = (view: Schemas['QueryRunView']) =>
  HttpResponse.json(view, { status: 202, headers: { Location: `/v1/query-runs/${view.id}` } });

/* ─── Chỉ số và chiều ───────────────────────────────────────────────────── */

interface Dim {
  id: string;
  code: string;
  name: string;
  entity: 'region' | 'period' | 'loan_book';
  parent: string | null;
  is_time: boolean;
  values: string[];
}

const BRANCHES: Record<string, string[]> = {
  'ĐB sông Hồng': ['CN Hà Nội', 'CN Hải Phòng'],
  'DH Bắc Bộ': ['CN Quảng Ninh', 'CN Bắc Giang'],
  'Tây Nguyên': ['CN Đắk Lắk', 'CN Gia Lai'],
  'Đông Nam Bộ': ['CN TP.HCM', 'CN Bình Dương'],
  'ĐB sông Cửu Long': ['CN Cần Thơ', 'CN An Giang'],
  'Bắc Trung Bộ': ['CN Nghệ An', 'CN Thanh Hoá'],
};

const DIMS: Dim[] = [
  {
    id: 'dim-region',
    code: 'vung',
    name: 'Vùng',
    entity: 'region',
    parent: null,
    is_time: false,
    values: Object.keys(BRANCHES),
  },
  {
    id: 'dim-branch',
    code: 'chi_nhanh',
    name: 'Chi nhánh',
    entity: 'region',
    parent: 'dim-region',
    is_time: false,
    values: Object.values(BRANCHES).flat(),
  },
  {
    id: 'dim-quarter',
    code: 'quy',
    name: 'Quý',
    entity: 'period',
    parent: null,
    is_time: true,
    values: ['Q2/2026', 'Q3/2026'],
  },
  {
    id: 'dim-month',
    code: 'thang',
    name: 'Tháng',
    entity: 'period',
    parent: null,
    is_time: true,
    values: ['T7/2026', 'T8/2026', 'T9/2026'],
  },
  {
    id: 'dim-product',
    code: 'san_pham',
    name: 'Sản phẩm',
    entity: 'loan_book',
    parent: null,
    is_time: false,
    values: ['Thẻ tín dụng', 'Vay tiêu dùng', 'Vay mua nhà'],
  },
  {
    id: 'dim-debt-group',
    code: 'nhom_no',
    name: 'Nhóm nợ',
    entity: 'loan_book',
    parent: null,
    is_time: false,
    values: ['Nhóm 1', 'Nhóm 2', 'Nhóm 3', 'Nhóm 4', 'Nhóm 5'],
  },
];

const ENTITIES = [
  { key: 'region', name: 'Chi nhánh' },
  { key: 'period', name: 'Thời gian' },
  { key: 'loan_book', name: 'Khoản vay' },
] as const;

interface Met {
  id: string;
  code: string;
  name: string;
  unit: string | null;
  direction: string;
  version: number;
  allowed: string[];
}

const METS: Met[] = [
  {
    id: 'exp-npl-ratio',
    code: 'NPL_RATIO',
    name: 'Nợ xấu (%)',
    unit: '%',
    direction: 'lower_better',
    version: 4,
    allowed: ['dim-region', 'dim-branch', 'dim-product'],
  },
  {
    id: 'exp-balance',
    code: 'BALANCE',
    name: 'Dư nợ',
    unit: 'tỷ VND',
    direction: 'higher_better',
    version: 2,
    allowed: ['dim-region', 'dim-branch', 'dim-product', 'dim-debt-group'],
  },
  {
    id: 'exp-loan-count',
    code: 'LOAN_COUNT',
    name: 'Số khoản vay',
    unit: null,
    direction: 'neutral',
    version: 1,
    allowed: ['dim-region', 'dim-product', 'dim-debt-group'],
  },
];

const metricRow = (m: Met): Schemas['MetricView'] => ({
  id: m.id,
  data_space_id: DS,
  code: m.code,
  display_name: m.name,
  owner_id: 'u-long',
  consumer_group: null,
  canonical_dataset_id: null,
  pack_metric_id: null,
  current_version: m.version,
  in_filed_report: false,
  certified: true,
  status: 'active',
  created_at: AT,
  updated_at: AT,
});

const metricDetail = (m: Met): Schemas['MetricDetail'] => ({
  ...metricRow(m),
  versions: [
    {
      id: `${m.id}-v${m.version}`,
      metric_id: m.id,
      version: m.version,
      display_name: m.name,
      description: null,
      expression: 'value',
      filters: [],
      unit: m.unit,
      decimals: m.unit === '%' ? 2 : 0,
      direction: m.direction,
      period_semantics: 'end_of_period',
      time_grain: 'month',
      allowed_dimensions: m.allowed,
      additivity: 'none',
      legal_basis: null,
      status: 'certified',
      certified_at: AT,
      certified_by: 'u-long',
      change_note: null,
      override_id: null,
      proposal_id: null,
      valid_from: AT,
      valid_to: null,
      created_at: AT,
    },
  ],
  usage: { data_product_ids: [], knowledge_page_ids: [] },
});

const entityRow = (e: (typeof ENTITIES)[number]): Schemas['EntityView'] => ({
  id: `entity-${e.key}`,
  key: e.key,
  data_space_id: DS,
  name: e.name,
  description: null,
  key_columns: [],
  canonical_entity_id: null,
  status: 'active',
  primary_dataset: null,
  relationships: [],
  created_at: AT,
  updated_at: AT,
});

const entityDetail = (e: (typeof ENTITIES)[number]): Schemas['EntityDetail'] => ({
  ...entityRow(e),
  dimensions: DIMS.filter((d) => d.entity === e.key).map((d) => ({
    id: d.id,
    code: d.code,
    name: d.name,
    expression: d.code,
    parent_dimension_id: d.parent,
    is_time: d.is_time,
  })),
  measures: [],
  metrics: [],
});

/* ─── Kết quả: dạng dài như trình biên dịch (metric, chiều…, value) ─────── */

/** A stable pseudo-figure per metric and cell. */
function figure(seed: string, metric: Met): number {
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 100_000;
  if (metric.code === 'NPL_RATIO') return Math.round((1.2 + (h % 120) / 100) * 100) / 100;
  if (metric.code === 'LOAN_COUNT') return 800 + (h % 4000);
  return 100 + (h % 900);
}

function valuesOf(dim: Dim, filters: { dimension_id: string; values: string[] }[]) {
  const own = filters.find((f) => f.dimension_id === dim.id);
  let values = dim.values;
  if (dim.id === 'dim-branch') {
    const region = filters.find((f) => f.dimension_id === 'dim-region');
    if (region) values = region.values.flatMap((r) => BRANCHES[r] ?? []);
  }
  return own ? values.filter((v) => own.values.includes(v)) : values;
}

function exploreRun(body: Schemas['ExploreQueryCreate']): Schemas['QueryRunView'] | Response {
  const metrics = body.metric_ids.map((id) => METS.find((m) => m.id === id));
  if (metrics.some((m) => !m))
    return problem(422, 'validation_failed', 'Unknown metric in this Data Space.');
  const dims = (body.dimensions ?? []).map((id) => DIMS.find((d) => d.id === id));
  if (dims.some((d) => !d)) return problem(422, 'validation_failed', 'Unknown dimension.');
  for (const m of metrics as Met[])
    for (const d of dims as Dim[])
      if (!d.is_time && !m.allowed.includes(d.id))
        return problem(
          422,
          'business_rule_violated',
          `Chiều “${d.name}” không dùng được với chỉ số “${m.name}”.`,
        );
  const filters = body.filters ?? [];
  let cells: string[][] = [[]];
  for (const d of dims as Dim[])
    cells = cells.flatMap((prefix) => valuesOf(d, filters).map((v) => [...prefix, v]));
  const rows = (metrics as Met[]).flatMap((m) =>
    cells.map((cell) => [m.code, ...cell, figure(`${cell.join('|')}|${filters.length}`, m)]),
  );
  return recordQueryRun({
    columns: [
      { name: 'metric', type: 'text' },
      ...(dims as Dim[]).map((d) => ({ name: d.code, type: 'text' })),
      { name: 'value', type: 'numeric' },
    ],
    rows,
    sql: `SELECT ... FROM semantic.credit_risk_retail -- ${(metrics as Met[]).map((m) => m.code).join(', ')}`,
  });
}

/** The decomposition skill's reading: where the metric rose between the first and last series. */
function explain(run: StoredRun): { headline: string; summary: string } {
  const names = run.columns.map((c) => c.name);
  const dims = names.filter((n) => n !== 'metric' && n !== 'value');
  const byCategory = new Map<string, number[]>();
  for (const row of run.rows) {
    const key = String(row[1] ?? '');
    byCategory.set(key, [...(byCategory.get(key) ?? []), Number(row[row.length - 1])]);
  }
  const up = [...byCategory.values()].filter((v) => v.length > 1 && v[v.length - 1] > v[0]);
  const label = DIMS.find((d) => d.code === dims[0])?.name.toLowerCase() ?? 'nhóm';
  return dims.length > 1
    ? {
        headline: `Tăng ở ${up.length}/${byCategory.size} ${label}`,
        summary: 'So với kỳ trước, mức tăng tập trung ở vài nơi; xem từng cột để đi sâu.',
      }
    : {
        headline: `${byCategory.size} ${label} trong kết quả`,
        summary: 'Thêm một chiều thời gian để agent so sánh hai kỳ.',
      };
}

/** "Hỏi bằng lời": the understood chips, by keywords of the question. */
function understand(text: string) {
  const t = text.toLowerCase();
  const chip = (kind: string, value: string, label: string) => ({
    kind,
    value,
    label,
    source: 'default',
    confidence: 0.9,
  });
  const chips = [];
  const metric = t.includes('dư nợ')
    ? METS[1]
    : t.includes('số khoản')
      ? METS[2]
      : t.includes('nợ xấu')
        ? METS[0]
        : null;
  if (metric) chips.push(chip('metric', metric.code, metric.name));
  for (const d of DIMS)
    if (t.includes(d.name.toLowerCase())) chips.push(chip('dimension', d.code, d.name));
  return chips;
}

/* ─── Artifact: một câu trả lời ─────────────────────────────────────────── */

const ARTIFACT_ANSWER = 'ans-npl-region';
const ARTIFACT_QUESTION = 'q-npl-region';

function artifactAnswer(queryRunId: string): Schemas['AnswerView'] {
  const figure = (key: string, label: string, value: string) => ({
    key,
    label,
    value,
    unit: '%',
    decimals: 2,
    dimensions: {},
    display_state: 'shown',
    query_run_id: queryRunId,
    metric_version_id: 'exp-npl-ratio-v4',
    trust: 'certified',
  });
  return {
    id: ARTIFACT_ANSWER,
    question_id: ARTIFACT_QUESTION,
    plan_id: 'plan-npl-region',
    kind: 'data',
    forecast_job_id: null,
    number_check: { numbers_total: 3, traced: 3, untraceable_final: 0 },
    text: 'Nợ xấu bán lẻ tăng mạnh nhất ở DH Bắc Bộ, giảm nhẹ ở Đông Nam Bộ.',
    figures: [
      figure('f1', 'DH Bắc Bộ', '0.61'),
      figure('f2', 'Tây Nguyên', '0.32'),
      figure('f3', 'Đông Nam Bộ', '-0.04'),
    ],
    chart_spec: { type: 'bar', x: 'vung', y: 'value' },
    citations: [],
    filed_vs_current: null,
    reconcile_badge: null,
    agent_name: 'Agent Phân tích',
    latency_ms: 1200,
    lineage_hash: null,
    data_product_id: null,
    product_scope: 'out_of_scope',
    created_at: '2026-10-08T09:58:00+07:00',
  };
}

function question(id: string, text: string, chips: unknown[] | null): Schemas['QuestionView'] {
  return {
    id,
    conversation_id: 'conv-explore',
    seq: 1,
    text,
    parent_question_id: null,
    as_of: null,
    knowledge_mode: 'latest',
    knowledge_at: null,
    purpose: chips ? 'explore' : null,
    status: chips ? 'planning' : 'answered',
    received_at: AT,
    answered_at: null,
    plan: chips
      ? {
          id: `plan-${id}`,
          question_id: id,
          version: 1,
          chips,
          steps: [],
          confidence: 0.9,
          edited_by_user: false,
          edit_note: null,
          created_at: AT,
        }
      : null,
  } as Schemas['QuestionView'];
}

/* ─── Onboarding (PROPOSAL) ─────────────────────────────────────────────── */

function onboardingFixture(): Onboarding {
  return {
    state: 'pending',
    steps: [
      { key: 'domain', label: 'Chọn domain', state: 'done' },
      { key: 'sources', label: 'Kết nối nguồn', state: 'done' },
      { key: 'first_agent', label: 'Agent đầu tiên', state: 'current' },
      { key: 'invite', label: 'Mời đồng nghiệp', state: 'todo' },
    ],
    context_text: 'Gói Ngân hàng đã kết nối Core banking, DWH và Sổ cái.',
    templates: [
      {
        ref: 'tpl-metric-monitor',
        title: 'Agent Giám sát chỉ số',
        description: 'Theo dõi 18 chỉ số rủi ro, báo khi vượt ngưỡng và tự tìm nguyên nhân',
        badge: { label: 'Đề xuất cho bạn', recommended: true },
      },
      {
        ref: 'tpl-periodic-report',
        title: 'Agent Báo cáo định kỳ',
        description: 'Soạn bản tin tuần từ chỉ số và biến động, bạn duyệt trước khi gửi',
        badge: { label: 'Phổ biến', recommended: false },
      },
      {
        ref: 'tpl-data-quality',
        title: 'Agent Chất lượng dữ liệu',
        description: 'Kiểm tra dữ liệu sau mỗi lần ETL, báo bất thường và xung đột nguồn',
        badge: { label: 'Nên có', recommended: false },
      },
    ],
  };
}

export function createExploreHandlers() {
  queryRuns = new Map();
  runSeq = 0;
  const artifactRun = recordQueryRun({
    columns: [
      { name: 'metric', type: 'text' },
      { name: 'vung', type: 'text' },
      { name: 'value', type: 'numeric' },
    ],
    rows: [
      ['NPL_RATIO', 'DH Bắc Bộ', 0.61],
      ['NPL_RATIO', 'Tây Nguyên', 0.32],
      ['NPL_RATIO', 'Đông Nam Bộ', -0.04],
    ],
    sql: 'SELECT vung, npl_ratio_delta AS value FROM semantic.credit_risk_retail GROUP BY vung',
  });
  const skillRuns = new Map<string, string>();
  const asked = new Map<string, string>();
  let tiles = 0;
  // Dev mock only: the page reload resets MSW state, so finishing or skipping onboarding would
  // bring it back on every reload. The real server keeps this per user (GET /v1/me/onboarding).
  const STORED = 'mock:onboarding-state';
  const stored = (() => {
    try {
      return sessionStorage.getItem(STORED) as Onboarding['state'] | null;
    } catch {
      return null;
    }
  })();
  let onboarding: Onboarding = { ...onboardingFixture(), ...(stored ? { state: stored } : {}) };
  const remember = (state: Onboarding['state']) => {
    try {
      sessionStorage.setItem(STORED, state);
    } catch {
      /* storage unavailable: the mock forgets on reload */
    }
  };

  return [
    http.get('*/v1/metrics', ({ request }) =>
      new URL(request.url).searchParams.get('certified') === 'true'
        ? page(METS.map(metricRow))
        : undefined,
    ),
    http.get('*/v1/metrics/:id', ({ params }) => {
      const m = METS.find((x) => x.id === params.id);
      return m ? HttpResponse.json(metricDetail(m)) : undefined;
    }),
    http.get('*/v1/entities', ({ request }) =>
      new URL(request.url).searchParams.get('status') === 'active'
        ? page(ENTITIES.map(entityRow))
        : undefined,
    ),
    http.get('*/v1/entities/:key', ({ params }) => {
      const e = ENTITIES.find((x) => x.key === params.key);
      return e ? HttpResponse.json(entityDetail(e)) : undefined;
    }),

    http.post('*/v1/explore/queries', async ({ request }) => {
      const result = exploreRun((await request.json()) as Schemas['ExploreQueryCreate']);
      return result instanceof Response ? result : accepted(result);
    }),
    http.get('*/v1/query-runs/:id', ({ params }) => {
      const run = queryRuns.get(String(params.id));
      return run ? HttpResponse.json(run.view) : undefined;
    }),
    http.get('*/v1/query-runs/:id/rows', ({ params, request }) => {
      const run = queryRuns.get(String(params.id));
      if (!run) return undefined;
      const url = new URL(request.url);
      const size = Number(url.searchParams.get('page_size') ?? 50);
      const offset = Number(url.searchParams.get('cursor') || 0);
      const end = offset + size;
      return HttpResponse.json({
        columns: run.columns,
        items: run.rows.slice(offset, end),
        next_page_token: end < run.rows.length ? String(end) : '',
        truncated: false,
        expires_at: '2026-11-08T09:00:00+07:00',
      } satisfies Schemas['QueryRowsPage']);
    }),
    http.get('*/v1/query-runs/:id/sql', ({ params }) => {
      const run = queryRuns.get(String(params.id));
      return run
        ? HttpResponse.json({
            sql_generated: run.sql,
            sql_executed: run.sql,
            policies_applied: [],
          } satisfies Schemas['QuerySqlView'])
        : undefined;
    }),

    // Agent giải thích: the decomposition skill of the Data Space, run on the result.
    http.get('*/v1/skills', ({ request }) =>
      new URL(request.url).searchParams.get('category') === 'decomposition'
        ? page([
            {
              id: 'skill-explore-variance',
              data_space_id: DS,
              code: 'phan_ra_bien_dong',
              name: 'Phân rã biến động',
              description: null,
              category: 'decomposition',
              scope: 'data_space',
              owner_id: 'u-long',
              origin: 'pack',
              origin_ref: null,
              current_version: 3,
              replaced_by_skill_id: null,
              run_count_30d: 42,
              created_at: AT,
              updated_at: AT,
            },
          ])
        : undefined,
    ),
    http.post('*/v1/skills/skill-explore-variance/runs', async ({ request }) => {
      const body = (await request.json()) as { params: { query_run_id: string } };
      const id = `srun-explore-${skillRuns.size + 1}`;
      skillRuns.set(id, body.params.query_run_id);
      return HttpResponse.json(
        { id, status: 'queued', poll_after_ms: 0 },
        { status: 202, headers: { Location: `/v1/skill-runs/${id}` } },
      );
    }),
    http.get('*/v1/skill-runs/:id', ({ params }) => {
      const runId = skillRuns.get(String(params.id));
      const run = runId ? queryRuns.get(runId) : undefined;
      if (!runId) return undefined;
      return HttpResponse.json({
        id: String(params.id),
        skill_id: 'skill-explore-variance',
        skill_version_id: 'skill-explore-variance-v3',
        version: 3,
        data_space_id: DS,
        caller_kind: 'user',
        caller_ref: null,
        principal_id: 'u-yen',
        params: { query_run_id: runId },
        as_of: null,
        status: run ? 'succeeded' : 'failed',
        result: run ? explain(run) : null,
        result_ref: null,
        started_at: AT,
        finished_at: AT,
        error: run ? null : 'Không đọc được kết quả.',
        steps: [],
        created_at: AT,
      } satisfies Schemas['RunDetail']);
    }),

    // "Hỏi bằng lời": only the questions asked with purpose explore are this mock's.
    http.post('*/v1/questions', async ({ request }) => {
      const body = (await request.clone().json()) as { text: string; purpose?: string | null };
      if (body.purpose !== 'explore') return undefined;
      const id = `q-explore-${asked.size + 1}`;
      asked.set(id, body.text);
      return HttpResponse.json(
        {
          id,
          question_id: id,
          conversation_id: 'conv-explore',
          status: 'received',
          poll_after_ms: 0,
          stream_url: null,
        } satisfies Schemas['QuestionAccepted'],
        { status: 202, headers: { Location: `/v1/questions/${id}` } },
      );
    }),
    http.get('*/v1/questions/:id', ({ params }) => {
      const id = String(params.id);
      if (id === ARTIFACT_QUESTION)
        return HttpResponse.json(question(id, 'Nợ xấu bán lẻ tăng ở đâu so với T8?', null));
      const text = asked.get(id);
      return text === undefined
        ? undefined
        : HttpResponse.json(question(id, text, understand(text)));
    }),

    http.get(`*/v1/answers/${ARTIFACT_ANSWER}`, () =>
      HttpResponse.json(artifactAnswer(artifactRun.id)),
    ),

    // "Lưu vào phiên": the tile as the api returns it (201).
    http.post('*/v1/workspaces/:id/tiles', async ({ params, request }) => {
      const body = (await request.json()) as Schemas['TileCreate'];
      if (!body.kind || typeof body.spec !== 'object')
        return problem(422, 'validation_failed', 'kind and spec are required.');
      tiles += 1;
      return HttpResponse.json(
        {
          id: `tile-saved-${tiles}`,
          workspace_id: String(params.id),
          position: 100 + tiles,
          kind: body.kind,
          title: body.title ?? null,
          spec: body.spec,
          depends_on: body.depends_on ?? [],
          author_kind: 'user',
          agent_suggestion_state: null,
          latest_snapshot_id: null,
          stale: true,
          created_at: AT,
          updated_at: AT,
        } satisfies Schemas['TileView'],
        { status: 201 },
      );
    }),

    http.get('*/v1/me/onboarding', () => HttpResponse.json(onboarding)),
    http.post('*/v1/me/onboarding\\:complete', () => {
      onboarding = { ...onboarding, state: 'done' };
      remember('done');
      return HttpResponse.json(onboarding);
    }),
    http.post('*/v1/me/onboarding\\:skip', () => {
      onboarding = { ...onboarding, state: 'skipped' };
      remember('skipped');
      return HttpResponse.json(onboarding);
    }),
    http.post('*/v1/me/onboarding\\:try-agent', async ({ request }) => {
      const body = (await request.json()) as { template_ref: string };
      if (!onboarding.templates.some((t) => t.ref === body.template_ref))
        return problem(422, 'invalid_request', 'Không có mẫu agent này.');
      onboarding = {
        ...onboarding,
        steps: onboarding.steps.map((s) =>
          s.key === 'first_agent'
            ? { ...s, state: 'done' }
            : s.key === 'invite'
              ? { ...s, state: 'current' }
              : s,
        ),
      };
      return HttpResponse.json(
        { agent_id: 'agent-metric-monitor', run_id: 'run-onboarding-1' },
        { status: 202 },
      );
    }),
  ];
}
