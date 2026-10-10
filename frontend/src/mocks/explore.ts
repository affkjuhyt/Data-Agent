import { http, HttpResponse } from 'msw';
import type {
  Artifact,
  DataSpaceChoice,
  ExploreDataset,
  ExploreDimension,
  ExploreExplanation,
  ExploreMetric,
  ExploreModel,
  ExploreQueryInput,
  ExploreResult,
  ExploreRow,
  NextStep,
  Onboarding,
} from '../lib/v1/explore';

/*
 * Khám phá (2140:15461 and its states), the full-screen artifact (2074:4442) and the
 * first-visit screens (2230:14988 Chọn Data Space, 2076:5584 Onboarding). Values are the
 * figures of the frames; other shelf combinations get stable made-up figures so the screen can
 * be explored end to end in dev.
 */

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

/* ─── Bộ dữ liệu và mô hình ngữ nghĩa ─────────────────────────────────── */

const DATASETS: ExploreDataset[] = [
  {
    id: 'dataset-0',
    display_name: 'Rủi ro tín dụng bán lẻ',
    certified: true,
    data_until_text: 'dữ liệu tới 30/09',
  },
  {
    id: 'dataset-1',
    display_name: 'Dư nợ & huy động',
    certified: true,
    data_until_text: 'dữ liệu tới 30/09',
  },
  {
    id: 'dataset-5',
    display_name: 'Hiệu quả chi nhánh',
    certified: true,
    data_until_text: 'dữ liệu tới 29/09',
  },
];

const METRICS: ExploreMetric[] = [
  { id: 'metric-npl-ratio', label: 'Nợ xấu (%)', code: 'NPL_RATIO', certified: true },
  { id: 'metric-1', label: 'Dư nợ', code: 'LOAN_BALANCE', certified: true },
  { id: 'metric-npl-balance', label: 'Dư nợ nhóm 3–5', code: 'NPL_BALANCE', certified: true },
  { id: 'metric-risk-cost', label: 'Chi phí rủi ro', code: 'RISK_COST', certified: true },
  { id: 'metric-loan-count', label: 'Số khoản vay', code: 'LOAN_COUNT', certified: false },
];

const v = (value: string, label = value) => ({ value, label });

const REGIONS = [
  { key: 'dh-bac-bo', label: 'DH Bắc Bộ', short: 'DH Bắc Bộ', q2: 1.71, q3: 2.32 },
  { key: 'db-song-hong', label: 'ĐB sông Hồng', short: 'ĐB s.Hồng', q2: 1.58, q3: 1.79 },
  { key: 'bac-trung-bo', label: 'Bắc Trung Bộ', short: 'Bắc Trung Bộ', q2: 1.66, q3: 1.8 },
  { key: 'dong-nam-bo', label: 'Đông Nam Bộ', short: 'Đông Nam Bộ', q2: 1.49, q3: 1.61 },
  { key: 'tay-nam-bo', label: 'Tây Nam Bộ', short: 'Tây Nam Bộ', q2: 1.75, q3: 1.83 },
  { key: 'tay-nguyen', label: 'Tây Nguyên', short: 'Tây Nguyên', q2: 1.92, q3: 1.88 },
];

const BRANCHES: Record<string, string[]> = {
  'dh-bac-bo': ['CN Hải Phòng', 'CN Quảng Ninh', 'CN Thái Bình', 'CN Nam Định'],
  'db-song-hong': ['CN Hà Nội', 'CN Bắc Ninh', 'CN Hải Dương'],
  'bac-trung-bo': ['CN Thanh Hoá', 'CN Nghệ An', 'CN Huế'],
  'dong-nam-bo': ['CN TP.HCM', 'CN Bình Dương', 'CN Đồng Nai'],
  'tay-nam-bo': ['CN Cần Thơ', 'CN An Giang'],
  'tay-nguyen': ['CN Đắk Lắk', 'CN Lâm Đồng'],
};

const slug = (text: string) =>
  text
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/gi, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');

const DIMENSIONS: ExploreDimension[] = [
  {
    id: 'region',
    label: 'Vùng',
    group_label: 'Chi nhánh',
    values: REGIONS.map((r) => v(r.key, r.label)),
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
  {
    id: 'branch',
    label: 'Chi nhánh',
    group_label: 'Chi nhánh',
    values: Object.values(BRANCHES)
      .flat()
      .map((b) => v(slug(b), b)),
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
  {
    id: 'month',
    label: 'Tháng',
    group_label: 'Thời gian',
    values: ['T7/2026', 'T8/2026', 'T9/2026'].map((m) => v(slug(m), m)),
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
  {
    id: 'quarter',
    label: 'Quý',
    group_label: 'Thời gian',
    values: [v('q1-2026', 'Q1/2026'), v('q2-2026', 'Q2/2026'), v('q3-2026', 'Q3/2026')],
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
  {
    id: 'product',
    label: 'Sản phẩm',
    group_label: 'Khoản vay',
    values: ['Thẻ tín dụng', 'Vay tiêu dùng', 'Vay mua nhà', 'Vay kinh doanh'].map((p) =>
      v(slug(p), p),
    ),
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
  {
    id: 'debt_group',
    label: 'Nhóm nợ',
    group_label: 'Khoản vay',
    values: [1, 2, 3, 4, 5].map((n) => v(`nhom-${n}`, `Nhóm ${n}`)),
    incompatible_metric_ids: ['metric-npl-ratio'],
    incompatible_reason: 'Nợ xấu (%) đã gộp sẵn theo nhóm nợ',
  },
  {
    id: 'segment',
    label: 'Phân khúc',
    group_label: 'Khoản vay',
    values: [v('ban-le', 'Bán lẻ'), v('doanh-nghiep', 'Doanh nghiệp')],
    incompatible_metric_ids: [],
    incompatible_reason: null,
  },
];

const DEFAULT_QUERY = (datasetId: string): ExploreQueryInput => ({
  dataset_id: datasetId,
  metric_ids: ['metric-npl-ratio'],
  dimension_ids: ['region', 'quarter'],
  filters: [
    { dimension_id: 'segment', op: 'eq', values: ['ban-le'] },
    { dimension_id: 'quarter', op: 'in', values: ['q2-2026', 'q3-2026'] },
  ],
});

const TIME = new Set(['quarter', 'month']);

/* A stable pseudo-figure for combinations the frames do not show. */
function figure(seed: string, metricId: string): number {
  let h = 0;
  for (const c of seed) h = (h * 31 + c.charCodeAt(0)) % 100_000;
  if (metricId === 'metric-npl-ratio') return Math.round((1.2 + (h % 120) / 100) * 100) / 100;
  if (metricId === 'metric-loan-count') return 800 + (h % 4000);
  return 100 + (h % 900);
}

function valuesOf(dimension: ExploreDimension, input: ExploreQueryInput) {
  const filter = input.filters.find((f) => f.dimension_id === dimension.id);
  let values = dimension.values;
  if (dimension.id === 'branch') {
    const region = input.filters.find((f) => f.dimension_id === 'region');
    if (region) {
      const names = region.values.flatMap((r) => BRANCHES[r] ?? []);
      values = names.map((b) => v(slug(b), b));
    }
  }
  return filter ? values.filter((x) => filter.values.includes(x.value)) : values;
}

const label = (dimensionId: string, value: string) =>
  DIMENSIONS.find((d) => d.id === dimensionId)?.values.find((x) => x.value === value)?.label ??
  value;

function runQuery(input: ExploreQueryInput, seq: number): ExploreResult | Response {
  const metric = METRICS.find((m) => m.id === input.metric_ids[0]);
  const dims = input.dimension_ids
    .map((id) => DIMENSIONS.find((d) => d.id === id))
    .filter(Boolean) as ExploreDimension[];
  if (!metric || dims.length === 0)
    return problem(422, 'invalid_request', 'Thêm ít nhất một chỉ số và một chiều.');
  const clash = dims.find((d) =>
    input.metric_ids.some((m) => d.incompatible_metric_ids.includes(m)),
  );
  if (clash)
    return problem(
      422,
      'incompatible_dimension',
      `Chiều “${clash.label}” không dùng được với chỉ số “${metric.label}” vì chỉ số đã gộp sẵn theo ${clash.label.toLowerCase()}. Thử bỏ chiều này hoặc dùng “Dư nợ”.`,
    );

  const time = dims.find((d) => TIME.has(d.id));
  const category = dims.find((d) => !TIME.has(d.id)) ?? time!;
  const series = time && time !== category ? valuesOf(time, input) : [v('all', metric.label)];
  const isFigmaCase =
    metric.id === 'metric-npl-ratio' && category.id === 'region' && time?.id === 'quarter';

  const rows: ExploreRow[] = valuesOf(category, input).map((c) => {
    const region = REGIONS.find((r) => r.key === c.value);
    const values = series.map((s) => {
      if (isFigmaCase && region && s.value === 'q2-2026') return region.q2;
      if (isFigmaCase && region && s.value === 'q3-2026') return region.q3;
      return figure(
        `${c.value}|${s.value}|${input.filters.map((f) => f.values.join()).join()}`,
        metric.id,
      );
    });
    const delta =
      values.length > 1 ? Math.round((values[values.length - 1] - values[0]) * 100) / 100 : null;
    return { key: c.value, label: c.label, short_label: region?.short ?? c.label, values, delta };
  });

  const segment = input.filters.find((f) => f.dimension_id === 'segment');
  const segmentText = segment
    ? ` ${segment.values.map((x) => label('segment', x).toLowerCase()).join(', ')}`
    : '';
  const shortName = metric.label.replace(/ \(%\)$/, '');
  const compare =
    series.length > 1
      ? ` · ${series[0].label.split('/')[0]} so với ${series[series.length - 1].label}`
      : '';
  const drill = category.id === 'region' ? 'branch' : null;
  const top = [...rows].sort((a, b) => (b.delta ?? 0) - (a.delta ?? 0))[0];
  const fmt = (n: number) => n.toFixed(2).replace('.', ',');

  const nextSteps: NextStep[] = [];
  if (top && top.delta !== null)
    nextSteps.push({
      kind: 'drill',
      title: `Đi sâu: ${top.short_label} theo sản phẩm`,
      detail: `tách mức tăng ${fmt(top.delta)} điểm`,
      filter: { dimension_id: category.id, op: 'eq', values: [top.key] },
      dimension_ids: ['product', ...(time ? [time.id] : [])],
    });
  nextSteps.push(
    { kind: 'rows', title: 'Xem dòng gốc', detail: '3.412 khoản vay đang nợ xấu' },
    { kind: 'lineage', title: 'Vì sao số này?', detail: 'công thức, nguồn, thời điểm dữ liệu' },
    {
      kind: 'watch',
      title: 'Giao agent theo dõi hằng tuần',
      detail: 'báo khi vùng nào tăng > 0,3 điểm',
    },
  );

  return {
    id: `qr-${seq}`,
    title: `${shortName}${segmentText} theo ${category.label.toLowerCase()}${compare}`,
    subtitle_text: `${metric.code} v4 · 0,8 giây${drill ? ' · bấm vào cột để đi sâu theo chi nhánh' : ''}`,
    category_label: category.label,
    series: series.map((s) => ({ key: s.value, label: s.label })),
    rows,
    unit:
      metric.id === 'metric-npl-ratio'
        ? 'percent'
        : metric.id === 'metric-loan-count'
          ? 'count'
          : 'amount',
    increase_is_bad: metric.id === 'metric-npl-ratio' || metric.id === 'metric-risk-cost',
    detail_row_count: 3412,
    drill_dimension_id: drill,
    next_steps: nextSteps,
    reconciliation_text: isFigmaCase ? 'Khớp số đã nộp và Báo cáo rủi ro tháng 9' : null,
    data_space_id: 'ds-rui-ro-tin-dung',
  };
}

function explain(result: ExploreResult): ExploreExplanation {
  if (result.reconciliation_text)
    return {
      headline: 'Nợ xấu tăng ở 5/6 vùng',
      body: 'Duyên hải Bắc Bộ tăng mạnh nhất (+0,61 điểm), chiếm 74% mức tăng toàn hệ thống. Tây Nguyên là vùng duy nhất giảm.',
    };
  const up = result.rows.filter((r) => (r.delta ?? 0) > 0).length;
  return {
    headline: `Tăng ở ${up}/${result.rows.length} ${result.category_label.toLowerCase()}`,
    body: 'Agent so sánh kỳ đầu và kỳ cuối trên kệ hiện tại.',
  };
}

/* "Hỏi bằng lời": a keyword reading, enough for the mock. */
function interpret(datasetId: string, question: string): ExploreQueryInput {
  const q = question.toLowerCase();
  const dims: string[] = [];
  if (q.includes('chi nhánh')) dims.push('branch');
  else if (q.includes('vùng')) dims.push('region');
  if (q.includes('sản phẩm')) dims.push('product');
  if (q.includes('tháng')) dims.push('month');
  else dims.push('quarter');
  const quarters = Array.from(q.matchAll(/q([1-4])/g), (m) => `q${m[1]}-2026`);
  return {
    dataset_id: datasetId,
    metric_ids: [q.includes('dư nợ') && !q.includes('nợ xấu') ? 'metric-1' : 'metric-npl-ratio'],
    dimension_ids: dims,
    filters: [
      ...(q.includes('bán lẻ')
        ? [{ dimension_id: 'segment', op: 'eq' as const, values: ['ban-le'] }]
        : []),
      ...(quarters.length
        ? [{ dimension_id: 'quarter', op: 'in' as const, values: [...new Set(quarters)].sort() }]
        : []),
    ],
  };
}

/* ─── Artifact ─────────────────────────────────────────────────────────── */

function artifactFixture(): Artifact {
  const row = (
    name: string,
    t8: string,
    t9: string,
    change: string,
    extra: string,
    trend: 'up' | 'down',
  ) => ({
    cells: [name, t8, t9, change, extra],
    trend,
  });
  return {
    id: 'art-npl-region',
    title: 'Đóng góp vào mức tăng NPL · theo vùng',
    session: { id: 'ws-npl-retail-t9', title: 'Điều tra nợ xấu bán lẻ T9' },
    version: 3,
    checked: true,
    meta_text:
      'Agent Phân tích · 09:58 · NPL_RATIO v4 · snapshot 30/09 v3 · dữ liệu tới 30/09 23:59',
    chart: {
      bars: [
        { label: 'Duyên hải Bắc Bộ', value: 0.61, value_text: '+0,61', tone: 'above' },
        { label: 'Đồng bằng sông Hồng', value: 0.21, value_text: '+0,21', tone: 'normal' },
        { label: 'Bắc Trung Bộ', value: 0.14, value_text: '+0,14', tone: 'normal' },
        { label: 'Đông Nam Bộ', value: 0.12, value_text: '+0,12', tone: 'normal' },
        { label: 'Tây Nam Bộ', value: 0.08, value_text: '+0,08', tone: 'normal' },
        { label: 'Tây Nguyên', value: -0.04, value_text: '−0,04', tone: 'down' },
      ],
      caption:
        'Điểm phần trăm thay đổi NPL, T8 → T9/2026. Màu cam: vùng vượt ngưỡng theo dõi 0,3 điểm.',
    },
    table: {
      columns: ['Vùng', 'T8', 'T9', 'Thay đổi', 'Dư nợ nhóm 3–5'],
      change_column: 3,
      rows: [
        row('Duyên hải Bắc Bộ', '1,71%', '2,32%', '+0,61', '412 tỷ', 'up'),
        row('Đồng bằng sông Hồng', '1,58%', '1,79%', '+0,21', '688 tỷ', 'up'),
        row('Bắc Trung Bộ', '1,66%', '1,80%', '+0,14', '241 tỷ', 'up'),
        row('Đông Nam Bộ', '1,49%', '1,61%', '+0,12', '530 tỷ', 'up'),
        row('Tây Nam Bộ', '1,75%', '1,83%', '+0,08', '219 tỷ', 'up'),
        row('Tây Nguyên', '1,92%', '1,88%', '−0,04', '98 tỷ', 'down'),
      ],
    },
    sql: [
      'SELECT region,',
      "       npl_ratio(period => '2026-08') AS t8,",
      "       npl_ratio(period => '2026-09') AS t9",
      'FROM semantic.credit_risk_retail',
      "WHERE segment = 'retail'",
      'GROUP BY region',
      'ORDER BY t9 - t8 DESC;',
    ].join('\n'),
    versions: [
      {
        version: 3,
        summary: 'Đổi sang NPL_RATIO v4 sau khi bạn chọn định nghĩa',
        author_text: 'Agent Phân tích · 09:58',
      },
      {
        version: 2,
        summary: 'Theo định nghĩa nội bộ nhóm 2–5',
        author_text: 'Agent Phân tích · 09:41',
      },
      { version: 1, summary: 'Bản đầu, chưa tách vùng', author_text: 'Agent Phân tích · 09:35' },
    ],
    compare: {
      label: 'v2 → v3',
      text: 'DH Bắc Bộ +0,92 → +0,61 · trọng tâm chuyển từ thẻ tín dụng sang vay tiêu dùng',
    },
    used_in: [
      {
        id: 'use-report-t9',
        text: 'Báo cáo rủi ro T9 · Hình 1 · tự cập nhật',
        pinned_version: null,
      },
      { id: 'use-weekly-41', text: 'Bản tin tuần 41 · bản chụp v2 (cũ)', pinned_version: 2 },
      { id: 'use-agent-npl', text: 'Agent Theo dõi nợ xấu · làm mẫu', pinned_version: null },
    ],
    saved: false,
    watching: false,
  };
}

/* ─── Lần đầu vào ──────────────────────────────────────────────────────── */

function dataSpaceChoicesFixture(): DataSpaceChoice[] {
  const base = { scope_text: null, owner_name: null, request_pending: false };
  return [
    {
      ...base,
      id: 'ds-rui-ro-tin-dung',
      name: 'Rủi ro tín dụng',
      access: 'member',
      role_label: 'Phân tích viên',
      role_kind: 'analyst',
      source_count: 12,
      metric_count: 148,
      agent_count: 5,
      last_used_text: 'hôm nay 08:40',
    },
    {
      ...base,
      id: 'ds-tai-chinh-ke-toan',
      name: 'Tài chính – Kế toán',
      access: 'member',
      role_label: 'Phân tích viên',
      scope_text: 'chỉ vùng Bắc',
      role_kind: 'analyst',
      source_count: 6,
      metric_count: 92,
      agent_count: 3,
      last_used_text: '03/10',
    },
    {
      ...base,
      id: 'ds-ban-le',
      name: 'Bán lẻ',
      access: 'member',
      role_label: 'Người xem',
      role_kind: 'viewer',
      source_count: 9,
      metric_count: 117,
      agent_count: 4,
      last_used_text: null,
    },
    {
      ...base,
      id: 'ds-kiem-soat-noi-bo',
      name: 'Kiểm soát nội bộ',
      access: 'none',
      role_label: null,
      role_kind: null,
      source_count: 0,
      metric_count: 0,
      agent_count: 0,
      last_used_text: null,
      owner_name: 'Trần Quốc An',
    },
  ];
}

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
  const results = new Map<string, ExploreResult>();
  let seq = 0;
  let artifact = artifactFixture();
  let spaces = dataSpaceChoicesFixture();
  let openLastUsed = true;
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

  const model = (id: string): ExploreModel | undefined => {
    const dataset = DATASETS.find((d) => d.id === id);
    return (
      dataset && {
        dataset,
        metrics: METRICS,
        dimensions: DIMENSIONS,
        default_query: DEFAULT_QUERY(id),
      }
    );
  };

  return [
    http.get('*/v1/explore/datasets', () =>
      HttpResponse.json({ items: DATASETS, next_page_token: '' }),
    ),
    http.get('*/v1/explore/datasets/:id/model', ({ params }) => {
      const found = model(String(params.id));
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Không tìm thấy bộ dữ liệu.');
    }),
    http.post('*/v1/explore/queries\\:interpret', async ({ request }) => {
      const body = (await request.json()) as { dataset_id: string; question: string };
      if (!body.question.trim()) return problem(422, 'invalid_request', 'Câu hỏi trống.');
      return HttpResponse.json(interpret(body.dataset_id, body.question));
    }),
    http.post('*/v1/explore/queries', async ({ request }) => {
      const input = (await request.json()) as ExploreQueryInput;
      const result = runQuery(input, ++seq);
      if (result instanceof Response) return result;
      results.set(result.id, result);
      return HttpResponse.json(result, { status: 201 });
    }),
    http.get('*/v1/explore/queries/:id/explanation', ({ params }) => {
      const result = results.get(String(params.id));
      return result
        ? HttpResponse.json(explain(result))
        : problem(404, 'not_found', 'Không tìm thấy truy vấn.');
    }),
    // The viewer of the frames sees aggregates only ("Thiếu quyền", 2165:18122).
    http.get('*/v1/explore/queries/:id/rows', () =>
      problem(403, 'aggregate_only', 'Vai trò của bạn chỉ được xem theo vùng.'),
    ),
    http.post('*/v1/explore/queries/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'save-to-dashboard' || !results.has(id))
        return problem(404, 'not_found', 'Không tìm thấy truy vấn.');
      const body = (await request.json()) as { dashboard_id: string };
      return HttpResponse.json(
        { dashboard_id: body.dashboard_id, tile_id: `tile-${id}` },
        { status: 201 },
      );
    }),

    http.get('*/v1/artifacts/:id', ({ params }) =>
      params.id === artifact.id
        ? HttpResponse.json(artifact)
        : problem(404, 'not_found', 'Không tìm thấy kết quả này.'),
    ),
    http.post('*/v1/artifacts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== artifact.id) return problem(404, 'not_found', 'Không tìm thấy kết quả này.');
      if (verb === 'save') artifact = { ...artifact, saved: true };
      else if (verb === 'watch') artifact = { ...artifact, watching: true };
      else if (verb === 'restore') {
        const { version } = (await request.json()) as { version: number };
        const old = artifact.versions.find((x) => x.version === version);
        if (!old) return problem(404, 'not_found', 'Không có phiên bản này.');
        const next = artifact.version + 1;
        artifact = {
          ...artifact,
          version: next,
          versions: [
            {
              version: next,
              summary: `Khôi phục v${version}: ${old.summary}`,
              author_text: 'Bạn · vừa xong',
            },
            ...artifact.versions,
          ],
          compare: null,
        };
      } else if (verb === 'update-usages') {
        const { usage_ids } = (await request.json()) as { usage_ids: string[] };
        artifact = {
          ...artifact,
          used_in: artifact.used_in.map((u) =>
            usage_ids.includes(u.id)
              ? {
                  ...u,
                  pinned_version: artifact.version,
                  text: u.text.replace(/bản chụp v\d+ \(cũ\)/, `bản chụp v${artifact.version}`),
                }
              : u,
          ),
        };
      } else return problem(404, 'not_found', 'Không có thao tác này.');
      return HttpResponse.json(artifact);
    }),

    http.get('*/v1/me/data-spaces', () =>
      HttpResponse.json({ items: spaces, open_last_used: openLastUsed }),
    ),
    http.put('*/v1/me/data-space-preference', async ({ request }) => {
      const body = (await request.json()) as { open_last_used: boolean };
      openLastUsed = body.open_last_used;
      return HttpResponse.json({ open_last_used: openLastUsed });
    }),
    // Only marks the space here; the request itself is POST /v1/access-requests (adminAccess mock).
    http.post('*/v1/access-requests', async ({ request }) => {
      const body = (await request.clone().json()) as { data_space_id: string };
      spaces = spaces.map((s) =>
        s.id === body.data_space_id ? { ...s, request_pending: true } : s,
      );
      return undefined;
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
