import type { Page } from './client';
import { findDecompositionSkill } from './skillAuthoring';
import { V1Error, api, ok, okAs } from './client';
import type { components } from './schema';
import type { DataSpace } from './types';
import { getMe } from './me';

/*
 * Khám phá (UC-01-04), the full-screen artifact (UC-01-01) and the global first-visit screens
 * (UC-11-08: Chọn Data Space, onboarding, "Data Agent hoạt động thế nào").
 *
 * Khám phá works on the certified metrics of the Data Space (SCR-02 "cây chỉ số và chiều đã chứng
 * nhận"), not on tables: the fields are GET /v1/metrics?certified=true, the dimensions a metric
 * may be split by are its current version's allowed_dimensions, named from the semantic layer's
 * entities. A query is POST /v1/explore/queries, a query_run (A2 Truy vấn) read back through
 * GET /v1/query-runs/{id}/rows. The artifact is an answer (GET /v1/answers/{id}). Onboarding is
 * still a PROPOSAL (no A2 endpoint).
 */

type Schemas = components['schemas'];

/* ─── Truy vấn: a query_run and its rows (A2) ─────────────────────────── */

export type QueryRunWire = Schemas['QueryRunView'];
type QueryRowsWire = Schemas['QueryRowsPage'];

const FINISHED = new Set(['succeeded', 'failed', 'cancelled', 'timeout']);

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * A query_run is recorded `queued` with 202 and run by worker-agent (A1.6): poll it until it is
 * finished. A run that did not succeed is thrown with the worker's reason, as the 422 a refused
 * query would be, so the screens show it the same way.
 */
export async function awaitQueryRun(
  run: QueryRunWire,
  { intervalMs = 500, timeoutMs = 60_000 } = {},
): Promise<QueryRunWire> {
  let current = run;
  const deadline = Date.now() + timeoutMs;
  while (!FINISHED.has(current.status)) {
    if (Date.now() > deadline)
      throw new V1Error(504, 'timeout', 'Truy vấn chạy quá lâu, thử lại sau.');
    await sleep(intervalMs);
    current = await ok<QueryRunWire>(
      api.GET('/v1/query-runs/{id}', { params: { path: { id: current.id } } }),
    );
  }
  if (current.status !== 'succeeded')
    throw new V1Error(
      422,
      current.status === 'timeout' ? 'timeout' : 'query_failed',
      current.error ?? 'Truy vấn không chạy được.',
    );
  return current;
}

export interface QueryRows {
  columns: { name: string; type: string | null }[];
  /** One array per row, in column order. */
  rows: unknown[][];
  truncated: boolean;
  next_page_token: string;
}

/** The worker stores columns as {name, type} and rows as arrays; both forms are read. */
export function readQueryRows(raw: QueryRowsWire): QueryRows {
  const columns = (Array.isArray(raw.columns) ? raw.columns : []).map((c: unknown) =>
    typeof c === 'string'
      ? { name: c, type: null }
      : {
          name: String((c as { name?: unknown }).name ?? ''),
          type:
            typeof (c as { type?: unknown }).type === 'string'
              ? ((c as { type: string }).type ?? null)
              : null,
        },
  );
  const rows = raw.items.map((item: unknown) =>
    Array.isArray(item)
      ? item
      : typeof item === 'object' && item !== null
        ? columns.map((c) => (item as Record<string, unknown>)[c.name])
        : [item],
  );
  return {
    columns,
    rows,
    truncated: raw.truncated,
    next_page_token: raw.next_page_token ?? '',
  };
}

export const getQueryRows = async (queryRunId: string, pageSize = 500) =>
  readQueryRows(
    await ok<QueryRowsWire>(
      api.GET('/v1/query-runs/{id}/rows', {
        params: {
          path: { id: queryRunId },
          query: {
            page_size: pageSize,
          },
        },
      }),
    ),
  );

/** The SQL as written and as run after the policy rewrite (audited by the api). */
export const getQuerySql = async (queryRunId: string) =>
  ok<Schemas['QuerySqlView']>(
    api.GET('/v1/query-runs/{id}/sql', { params: { path: { id: queryRunId } } }),
  );

/* ─── Khám phá: chỉ số và chiều ─────────────────────────────────────────── */

type Unit = 'percent' | 'count' | 'amount';

export interface ExploreMetric {
  id: string;
  label: string;
  code: string;
  /** The ✓ in the field list: the metric has a certified version. */
  certified: boolean;
  /** The current version, for "NPL_RATIO v4". */
  version: number | null;
  unit: Unit;
  /** direction lower_better: a rise is bad news, so increases show in the error colour. */
  increase_is_bad: boolean;
  /** The dimensions the owner allows (BR-81); time dimensions are always allowed. */
  allowed_dimension_ids: string[];
}

export interface ExploreDimension {
  id: string;
  /** The column name the compiler gives it in a result. */
  code: string;
  label: string;
  /** The group heading in the field list: the entity it belongs to. */
  group_label: string;
  /** The values seen in results so far (A3 has no list of a dimension's values). */
  values: { value: string; label: string }[];
  /** Chosen metrics that may not be split by it, shown dimmed with the reason. */
  incompatible_metric_ids: string[];
  incompatible_reason: string | null;
  /** The coarser dimension it refines (chi nhánh → vùng): a click on a column drills into it. */
  parent_id: string | null;
  is_time: boolean;
}

export interface ExploreModel {
  metrics: ExploreMetric[];
  dimensions: ExploreDimension[];
}

export type ExploreFilter = {
  dimension_id: string;
  op: 'eq' | 'in';
  values: string[];
};

export interface ExploreQueryInput {
  metric_ids: string[];
  dimension_ids: string[];
  filters: ExploreFilter[];
}

const unitOf = (unit: string | null | undefined): Unit =>
  unit === '%' || unit?.toLowerCase() === 'percent'
    ? 'percent'
    : unit && /vnd|đồng|tỷ|triệu/i.test(unit)
      ? 'amount'
      : 'count';

function readMetric(row: Schemas['MetricView'], detail: Schemas['MetricDetail'] | null) {
  const version = detail?.versions?.find((v) => v.version === row.current_version) ?? null;
  return {
    id: row.id,
    label: version?.display_name ?? row.display_name ?? row.code,
    code: row.code,
    certified: row.certified ?? false,
    version: row.current_version,
    unit: unitOf(version?.unit),
    increase_is_bad: version?.direction === 'lower_better',
    allowed_dimension_ids: version?.allowed_dimensions ?? [],
  } satisfies ExploreMetric;
}

/**
 * The model behind the field list: the certified metrics with their current version (one GET
 * per metric: the list does not carry allowed_dimensions), and the dimensions of the active
 * entities (one GET per entity: the list does not carry them).
 */
export async function getExploreModel(): Promise<ExploreModel> {
  const [metricPage, entityPage] = await Promise.all([
    ok<Page<Schemas['MetricView']>>(
      api.GET('/v1/metrics', { params: { query: { certified: true, page_size: 100 } } }),
    ),
    ok<Page<Schemas['EntityView']>>(
      api.GET('/v1/entities', { params: { query: { status: 'active', page_size: 100 } } }),
    ),
  ]);
  const [details, entities] = await Promise.all([
    Promise.all(
      metricPage.items.map((m) =>
        ok<Schemas['MetricDetail']>(
          api.GET('/v1/metrics/{id}', { params: { path: { id: m.id } } }),
        ).catch(() => null),
      ),
    ),
    Promise.all(
      entityPage.items.map((e) =>
        ok<Schemas['EntityDetail']>(
          api.GET('/v1/entities/{key}', { params: { path: { key: e.key } } }),
        ).catch(() => null),
      ),
    ),
  ]);
  const metrics = metricPage.items
    .map((m, i) => readMetric(m, details[i]))
    .filter((m) => m.version !== null);
  const dimensions = entities.flatMap((entity) =>
    (entity?.dimensions ?? []).map(
      (d): ExploreDimension => ({
        id: d.id,
        code: d.code,
        label: d.name,
        group_label: entity!.name,
        values: [],
        incompatible_metric_ids: d.is_time
          ? []
          : metrics.filter((m) => !m.allowed_dimension_ids.includes(d.id)).map((m) => m.id),
        incompatible_reason: d.is_time ? null : 'Chỉ số đã chọn không được chia theo chiều này.',
        parent_id: d.parent_dimension_id,
        is_time: d.is_time,
      }),
    ),
  );
  return { metrics, dimensions };
}

/* ─── Khám phá: kết quả ─────────────────────────────────────────────────── */

export interface ExploreRow {
  key: string;
  label: string;
  /** The axis label under a column. */
  short_label: string;
  /** One value per series, in series order. */
  values: (number | null)[];
  /** Last series minus the first, in the value's unit; null with one series. */
  delta: number | null;
}

export type NextStep =
  | {
      kind: 'drill';
      title: string;
      detail: string;
      /** Applied to the shelves: add this filter, split by these dimensions. */
      filter: ExploreFilter;
      dimension_ids: string[];
    }
  | { kind: 'rows' | 'lineage' | 'watch'; title: string; detail: string };

/** A finished query_run of the explorer, read into the chart and the value table. */
export interface ExploreResult {
  /** The query_run id. */
  id: string;
  title: string;
  /** "NPL_RATIO v4 · 0,8 giây" */
  subtitle_text: string;
  /** The dimension of the categories (columns of the chart, rows of the table). */
  category_label: string;
  category_dimension_id: string | null;
  series: { key: string; label: string }[];
  rows: ExploreRow[];
  unit: Unit;
  increase_is_bad: boolean;
  /** Rows the run returned; null when the api does not say. */
  row_count: number | null;
  /** The dimension a click on a column drills into, if any. */
  drill_dimension_id: string | null;
  /** PROPOSAL: no A3 field suggests next steps; always empty until one does. */
  next_steps: NextStep[];
  /** PROPOSAL: no A3 field says the figures match a filed report; always null. */
  reconciliation_text: string | null;
  data_space_id: string;
}

const numberOf = (value: unknown): number | null => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value)))
    return Number(value);
  return null;
};

/**
 * The compiler's result is long: a `metric` column (the metric code), the dimensions asked for
 * by their code, then `value`. The first dimension is the category; a second one, with a single
 * metric, becomes the series (Q2 and Q3 side by side); with several metrics they are the series.
 */
export function readExploreResult(
  run: QueryRunWire,
  data: QueryRows,
  input: ExploreQueryInput,
  model: ExploreModel,
): ExploreResult {
  const names = data.columns.map((c) => c.name);
  const metricAt = names.indexOf('metric');
  const valueAt = names.lastIndexOf('value') >= 0 ? names.lastIndexOf('value') : names.length - 1;
  const dimAt = names.map((_, i) => i).filter((i) => i !== metricAt && i !== valueAt);
  const dimensionOf = (column: string) => model.dimensions.find((d) => d.code === column);
  const chosen = input.metric_ids
    .map((id) => model.metrics.find((m) => m.id === id))
    .filter((m): m is ExploreMetric => m !== undefined);
  const metricByCode = new Map(chosen.map((m) => [m.code, m]));
  const categoryAt = dimAt[0] ?? -1;
  const seriesAt = chosen.length > 1 ? metricAt : (dimAt[1] ?? -1);

  const seriesKeys: string[] = [];
  const categories = new Map<string, Map<string, number | null>>();
  for (const row of data.rows) {
    const category = categoryAt >= 0 ? String(row[categoryAt] ?? '—') : 'Tổng';
    const series = seriesAt >= 0 ? String(row[seriesAt] ?? '—') : (chosen[0]?.code ?? 'value');
    if (!seriesKeys.includes(series)) seriesKeys.push(series);
    const values = categories.get(category) ?? new Map<string, number | null>();
    // A third dimension is not drawn: the first value of a category and series is kept.
    if (!values.has(series)) values.set(series, numberOf(row[valueAt]));
    categories.set(category, values);
  }
  const seriesLabel = (key: string) =>
    chosen.length > 1 ? (metricByCode.get(key)?.label ?? key) : key;
  const series = seriesKeys.map((key) => ({
    key,
    label: seriesAt >= 0 ? seriesLabel(key) : (chosen[0]?.label ?? key),
  }));
  const rows = [...categories].map(([key, values]): ExploreRow => {
    const list = seriesKeys.map((s) => values.get(s) ?? null);
    const first = list[0];
    const last = list[list.length - 1];
    return {
      key,
      label: key,
      short_label: key,
      values: list,
      delta: list.length > 1 && first !== null && last !== null ? last - first : null,
    };
  });

  const category = categoryAt >= 0 ? dimensionOf(names[categoryAt]) : undefined;
  const categoryLabel = category?.label ?? (categoryAt >= 0 ? names[categoryAt] : 'Tổng');
  const metricText = chosen.map((m) => m.label).join(', ');
  const seriesText =
    series.length > 1 && chosen.length === 1
      ? ` · ${series.map((s) => s.label).join(' so với ')}`
      : '';
  const seconds =
    run.duration_ms !== null && run.duration_ms !== undefined
      ? ` · ${(run.duration_ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} giây`
      : '';
  return {
    id: run.id,
    title: `${metricText} theo ${categoryLabel.toLowerCase()}${seriesText}`,
    subtitle_text: `${chosen.map((m) => `${m.code}${m.version ? ` v${m.version}` : ''}`).join(', ')}${seconds}`,
    category_label: categoryLabel,
    category_dimension_id: category?.id ?? null,
    series,
    rows,
    unit: chosen[0]?.unit ?? 'count',
    increase_is_bad: chosen[0]?.increase_is_bad ?? false,
    row_count: run.row_count ?? null,
    drill_dimension_id: category
      ? (model.dimensions.find((d) => d.parent_id === category.id)?.id ?? null)
      : null,
    next_steps: [],
    reconciliation_text: null,
    data_space_id: run.data_space_id,
  };
}

/**
 * POST /v1/explore/queries, then the run's rows. 422 `business_rule_violated` when a dimension
 * cannot split a chosen metric (BR-81), with the reason as detail.
 */
export async function runExploreQuery(
  input: ExploreQueryInput,
  model: ExploreModel,
): Promise<ExploreResult> {
  const accepted = await ok<QueryRunWire>(
    api.POST('/v1/explore/queries', {
      body: {
        metric_ids: input.metric_ids,
        dimensions: input.dimension_ids,
        filters: input.filters,
      } satisfies Schemas['ExploreQueryCreate'],
    }),
  );
  const run = await awaitQueryRun(accepted);
  return readExploreResult(run, await getQueryRows(run.id), input, model);
}

/** The rows behind the chart, as the table under "Xem dòng kết quả". */
export interface ExploreRows {
  columns: string[];
  rows: string[][];
  /** null when there are more pages than this one. */
  total: number | null;
}

export const getExploreRows = async (queryRunId: string): Promise<ExploreRows> => {
  const data = await getQueryRows(queryRunId, 50);
  return {
    columns: data.columns.map((c) => c.name),
    rows: data.rows.map((r) =>
      r.map((cell) => (cell === null || cell === undefined ? '—' : String(cell))),
    ),
    total: data.next_page_token ? null : data.rows.length,
  };
};

/* ─── Agent giải thích: kỹ năng phân rã biến động trên kết quả ─────────── */

export interface ExploreExplanation {
  headline: string;
  body: string;
}

const textOf = (value: unknown) => (typeof value === 'string' && value.trim() ? value : null);

/**
 * The decomposition skill's params for what the explorer shows (worker-agent skill_runs.py):
 * the first metric, cut by the shelf's non-time dimension, the last month against the one
 * before. Null without a time dimension on the shelves and two months in the result, or without
 * a dimension to cut by: there is no change to decompose.
 */
export function explanationParams(
  result: ExploreResult,
  input: ExploreQueryInput,
  model: ExploreModel,
): { metric: string; dimension: string; period: string; base_period: string } | null {
  const metric = model.metrics.find((m) => m.id === input.metric_ids[0]);
  const dims = input.dimension_ids
    .map((id) => model.dimensions.find((d) => d.id === id))
    .filter((d): d is ExploreDimension => !!d);
  const time = dims.find((d) => d.is_time);
  const cut = dims.find((d) => !d.is_time);
  if (!metric || !time || !cut) return null;
  const keys =
    result.category_dimension_id === time.id
      ? result.rows.map((r) => r.key)
      : result.series.map((s) => s.key);
  const periods = [...new Set(keys.map(periodKey))];
  // One grain only: a month is not compared with a quarter.
  if (periods.some((p) => p === null) || periods.length < 2) return null;
  const sorted = (periods as string[]).sort();
  if (new Set(sorted.map((p) => p.includes('Q'))).size > 1) return null;
  return {
    metric: metric.code,
    dimension: cut.code,
    period: sorted[sorted.length - 1],
    base_period: sorted[sorted.length - 2],
  };
}

/** A time member as the skill takes it: "YYYY-MM" or "YYYY-Qn" ("2026-09-30", "T9/2026",
 *  "09/2026", "Q3/2026", "2026-Q3"); null for anything else. */
export function periodKey(value: string): string | null {
  const v = value.trim();
  let m = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(v);
  if (m) return `${m[1]}-${m[2]}`;
  m = /^(\d{4})-Q([1-4])$/i.exec(v);
  if (m) return `${m[1]}-Q${m[2]}`;
  m = /^Q([1-4])\/(\d{4})$/i.exec(v);
  if (m) return `${m[2]}-Q${m[1]}`;
  m = /^(?:T|tháng\s*)?(\d{1,2})\/(\d{4})$/i.exec(v);
  if (m && Number(m[1]) >= 1 && Number(m[1]) <= 12) return `${m[2]}-${m[1].padStart(2, '0')}`;
  return null;
}

/**
 * SCR-06 "Agent giải thích" is the decomposition skill run on what the explorer shows (POST
 * /v1/skills/{id}/runs → GET /v1/skill-runs/{id}). null when the Data Space has no published
 * decomposition skill, when the shelves give no change to decompose, or when the result carries
 * no text to show.
 */
export async function getExploreExplanation(
  result: ExploreResult,
  input: ExploreQueryInput,
  model: ExploreModel,
  { intervalMs = 1_000, timeoutMs = 60_000 } = {},
): Promise<ExploreExplanation | null> {
  const params = explanationParams(result, input, model);
  if (!params) return null;
  const skill = await findDecompositionSkill();
  if (!skill) return null;
  const accepted = await ok<Schemas['services__api__modules__skill__schemas__Accepted']>(
    api.POST('/v1/skills/{id}/runs', {
      params: { path: { id: skill } },
      body: { params } as unknown as Schemas['RunCreate'],
    }),
  );
  const deadline = Date.now() + timeoutMs;
  let run = await ok<Schemas['RunDetail']>(
    api.GET('/v1/skill-runs/{id}', { params: { path: { id: accepted.id } } }),
  );
  while (!['succeeded', 'failed', 'cancelled', 'partial'].includes(run.status)) {
    if (Date.now() > deadline) throw new V1Error(504, 'timeout', 'Kỹ năng chạy quá lâu.');
    await sleep(Math.max(intervalMs, accepted.poll_after_ms ?? 0));
    run = await ok<Schemas['RunDetail']>(
      api.GET('/v1/skill-runs/{id}', { params: { path: { id: accepted.id } } }),
    );
  }
  if (run.status === 'failed' || run.status === 'cancelled')
    throw new V1Error(422, 'skill_failed', run.error ?? 'Kỹ năng không chạy được.');
  const out =
    typeof run.result === 'object' && run.result !== null
      ? (run.result as Record<string, unknown>)
      : {};
  // The worker's summary leads with the change; the rest, and the method, are the body.
  const summary = textOf(out.summary) ?? textOf(out.text) ?? textOf(out.body);
  const [first, ...rest] = (summary ?? '').split(/(?<=\.)\s+/);
  const headline = textOf(out.headline) ?? textOf(out.title) ?? textOf(first);
  const body = [rest.join(' '), textOf(out.method)].filter(Boolean).join(' ');
  if (!headline && !body) return null;
  return { headline: headline ?? '', body };
}

/* ─── Hỏi bằng lời: câu hỏi purpose explore, kệ điền từ chip cách hiểu ─── */

type QuestionView = Schemas['QuestionView'];

const UNDERSTOOD_END = new Set(['answered', 'refused', 'failed', 'clarifying', 'cancelled']);

/**
 * "Hỏi bằng lời": POST /v1/questions {purpose: explore}; the plan's chips (what the system
 * understood: metric, dimension, filter "code=value") fill the shelves for the viewer to check.
 * 422 when no chip names a metric of the model.
 */
export async function interpretQuestion(
  question: string,
  model: ExploreModel,
  { intervalMs = 500, timeoutMs = 30_000 } = {},
): Promise<ExploreQueryInput> {
  const accepted = await ok<Schemas['QuestionAccepted']>(
    api.POST('/v1/questions', {
      body: {
        text: question,
        purpose: 'explore',
        knowledge_mode: 'latest',
        stream: false,
      } satisfies Schemas['QuestionCreate'],
    }),
  );
  const deadline = Date.now() + timeoutMs;
  let view = await ok<QuestionView>(
    api.GET('/v1/questions/{id}', { params: { path: { id: accepted.question_id } } }),
  );
  while (!view.plan && !UNDERSTOOD_END.has(view.status)) {
    if (Date.now() > deadline) throw new V1Error(504, 'timeout', 'Agent đọc câu hỏi quá lâu.');
    await sleep(Math.max(intervalMs, accepted.poll_after_ms ?? 0));
    view = await ok<QuestionView>(
      api.GET('/v1/questions/{id}', { params: { path: { id: accepted.question_id } } }),
    );
  }
  const chips = (Array.isArray(view.plan?.chips) ? view.plan.chips : []) as {
    kind?: unknown;
    value?: unknown;
  }[];
  const of = (kind: string) =>
    chips
      .filter((c) => c.kind === kind && typeof c.value === 'string')
      .map((c) => c.value as string);
  const metric_ids = of('metric')
    .map((code) => model.metrics.find((m) => m.code === code)?.id)
    .filter((id): id is string => id !== undefined);
  if (metric_ids.length === 0)
    throw new V1Error(422, 'not_understood', 'Agent không nhận ra chỉ số nào trong câu hỏi.');
  const dimensionId = (code: string) => model.dimensions.find((d) => d.code === code)?.id;
  const dimension_ids = of('dimension')
    .map(dimensionId)
    .filter((id): id is string => id !== undefined);
  const filters = new Map<string, string[]>();
  for (const chip of of('filter')) {
    const [code, ...rest] = chip.split('=');
    const id = dimensionId(code);
    if (id && rest.length) filters.set(id, [...(filters.get(id) ?? []), rest.join('=')]);
  }
  return {
    metric_ids,
    dimension_ids,
    filters: [...filters].map(([dimension_id, values]) => ({
      dimension_id,
      op: values.length === 1 ? 'eq' : 'in',
      values,
    })),
  };
}

/* ─── Lưu kết quả vào phiên (P02) ───────────────────────────────────────── */

/**
 * There are no dashboards in the design: a result is kept as a tile of a session
 * (POST /v1/workspaces/{id}/tiles). The tile records the query run and the shelves.
 */
export const saveExploreToSession = (
  workspaceId: string,
  result: ExploreResult,
  input: ExploreQueryInput,
) =>
  ok<Schemas['TileView']>(
    api.POST('/v1/workspaces/{id}/tiles', {
      params: { path: { id: workspaceId } },
      body: {
        kind: 'chart',
        title: result.title,
        spec: {
          query_run_id: result.id,
          metric_ids: input.metric_ids,
          dimensions: input.dimension_ids,
          filters: input.filters,
        },
      } as unknown as Schemas['TileCreate'],
    }),
  );

/* ─── Artifact toàn màn (UC-01-01): một câu trả lời ─────────────────────── */

export interface ArtifactBar {
  label: string;
  value: number;
  value_text: string;
  tone: 'normal' | 'down';
}

export interface Artifact {
  /** The answer id. */
  id: string;
  /** The question asked; "Câu trả lời" when it cannot be read. */
  title: string;
  /** Every number in the text traced to a figure (number_check). */
  checked: boolean;
  /** "Agent Phân tích · 09:58" */
  meta_text: string;
  /** The answer's text. */
  text: string;
  /** Bars of the figures with a value; null with fewer than two. */
  chart: { bars: ArtifactBar[] } | null;
  table: { columns: string[]; rows: string[][] };
  /** The run behind the figures, for the SQL tab; null when the answer has none. */
  query_run_id: string | null;
}

interface FigureWire {
  label?: unknown;
  value?: unknown;
  unit?: unknown;
  decimals?: unknown;
  display_state?: unknown;
  query_run_id?: unknown;
}

function figureText(f: FigureWire): string {
  const value = numberOf(f.value);
  if (value === null) return '—';
  const digits = typeof f.decimals === 'number' ? f.decimals : 2;
  const text = value.toLocaleString('vi-VN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return typeof f.unit === 'string' && f.unit
    ? `${text}${f.unit === '%' ? '' : ' '}${f.unit}`
    : text;
}

export function readArtifact(answer: Schemas['AnswerView'], question: string | null): Artifact {
  const figures = (Array.isArray(answer.figures) ? answer.figures : []).filter(
    (f: FigureWire) => f.display_state === undefined || f.display_state === 'shown',
  ) as FigureWire[];
  const check =
    typeof answer.number_check === 'object' && answer.number_check !== null
      ? (answer.number_check as Record<string, unknown>)
      : {};
  const bars = figures.flatMap((f): ArtifactBar[] => {
    const value = numberOf(f.value);
    return value === null
      ? []
      : [
          {
            label: String(f.label ?? '—'),
            value,
            value_text: figureText(f),
            tone: value < 0 ? 'down' : 'normal',
          },
        ];
  });
  const at = new Date(answer.created_at);
  const time = Number.isNaN(at.getTime())
    ? null
    : at.toLocaleString('vi-VN', {
        hour: '2-digit',
        minute: '2-digit',
        day: '2-digit',
        month: '2-digit',
      });
  return {
    id: answer.id,
    title: question ?? 'Câu trả lời',
    checked:
      numberOf(check.numbers_total) !== null &&
      (numberOf(check.numbers_total) ?? 0) > 0 &&
      numberOf(check.untraceable_final) === 0,
    meta_text: [answer.agent_name, time].filter(Boolean).join(' · '),
    text: answer.text,
    chart: bars.length > 1 ? { bars } : null,
    table: {
      columns: ['Chỉ tiêu', 'Giá trị'],
      rows: figures.map((f) => [String(f.label ?? '—'), figureText(f)]),
    },
    query_run_id:
      figures.map((f) => f.query_run_id).find((id): id is string => typeof id === 'string') ?? null,
  };
}

export const getArtifact = async (id: string): Promise<Artifact> => {
  const answer = await ok<Schemas['AnswerView']>(
    api.GET('/v1/answers/{id}', { params: { path: { id } } }),
  );
  const question = await ok<QuestionView>(
    api.GET('/v1/questions/{id}', { params: { path: { id: answer.question_id } } }),
  ).catch(() => null);
  return readArtifact(answer, question?.text ?? null);
};

/** "Lưu": the answer becomes a question tile of a session (POST /v1/workspaces/{id}/tiles). */
export const saveArtifactToSession = (workspaceId: string, artifact: Artifact) =>
  ok<Schemas['TileView']>(
    api.POST('/v1/workspaces/{id}/tiles', {
      params: { path: { id: workspaceId } },
      body: {
        kind: 'question',
        title: artifact.title,
        spec: { question: artifact.title, answer_id: artifact.id },
      } as unknown as Schemas['TileCreate'],
    }),
  );

/* ─── Lần đầu vào: Chọn Data Space (UC-11-08) ─────────────────────────── */

export interface DataSpaceChoice {
  id: string;
  name: string;
  access: 'member' | 'none';
  /** "Phân tích viên", "Người xem"; null without access. */
  role_label: string | null;
  /** "chỉ vùng Bắc" when the role is narrowed. */
  scope_text: string | null;
  /** view = read-only role (grey dot), analyst = may ask and run (blue dot). */
  role_kind: 'analyst' | 'viewer' | null;
  source_count: number;
  metric_count: number;
  agent_count: number;
  /** "hôm nay 08:40", "03/10"; null = never used. */
  last_used_text: string | null;
  /** The owner to ask, for a space without access. */
  owner_name: string | null;
  request_pending: boolean;
}

/** Adapt the implemented membership list to the first-visit picker shape. */
export interface MyDataSpaces {
  items: DataSpaceChoice[];
  open_last_used: boolean;
}

export const getMyDataSpaces = async (): Promise<MyDataSpaces> => {
  const [page, me] = await Promise.all([
    ok<Page<DataSpace>>(api.GET('/v1/data-spaces', { params: { query: { page_size: 500 } } })),
    getMe(),
  ]);
  const defaultId = me.default_data_space_id;
  return {
    items: page.items.map((space) => ({
      id: space.id,
      name: space.name,
      access: 'member' as const,
      role_label: space.my_roles[0]?.name ?? null,
      scope_text: null,
      role_kind: space.my_capabilities.includes('ask') ? ('analyst' as const) : ('viewer' as const),
      source_count: 0,
      metric_count: 0,
      agent_count: 0,
      last_used_text: space.id === defaultId ? 'gần đây' : null,
      owner_name: null,
      request_pending: false,
    })),
    open_last_used: defaultId !== null,
  };
};

/** PUT /v1/me/data-space-preference */
export const setOpenLastUsed = (openLastUsed: boolean, dataSpaceId?: string) =>
  ok<{ open_last_used: boolean }>(
    api.PUT('/v1/me/data-space-preference', {
      body: {
        open_last_used: openLastUsed,
        ...(dataSpaceId ? { data_space_id: dataSpaceId } : {}),
      },
    }),
  );

/* ─── Onboarding lần đầu ─────────────────────────────────────────────── */

export type OnboardingStepKey = 'domain' | 'sources' | 'first_agent' | 'invite';

export interface AgentTemplateChoice {
  ref: string;
  title: string;
  description: string;
  /** "Đề xuất cho bạn" (recommended) or a plain badge: "Phổ biến", "Nên có". */
  badge: { label: string; recommended: boolean };
}

/**
 * PROPOSAL: GET /v1/me/onboarding. The server owns whether the viewer still has to go through
 * it (`state`), so a second browser or a cleared cache does not show it again.
 */
export interface Onboarding {
  state: 'pending' | 'done' | 'skipped';
  steps: { key: OnboardingStepKey; label: string; state: 'done' | 'current' | 'todo' }[];
  /** "Gói Ngân hàng đã kết nối Core banking, DWH và Sổ cái." */
  context_text: string;
  templates: AgentTemplateChoice[];
}

export const getOnboarding = () => okAs<Onboarding>(api.GET('/v1/me/onboarding'));

export const skipOnboarding = () => okAs<Onboarding>(api.POST('/v1/me/onboarding:skip'));

/** PROPOSAL: the viewer went through every step; the server stops showing onboarding. */
export const completeOnboarding = () => okAs<Onboarding>(api.POST('/v1/me/onboarding:complete'));

/** PROPOSAL: creates the agent from the template and starts a test run on the viewer's data (202). */
export const tryOnboardingAgent = (templateRef: string) =>
  ok<{ agent_id: string; run_id: string }>(
    api.POST('/v1/me/onboarding:try-agent', {
      body: {
        template_ref: templateRef,
      },
    }),
  );
