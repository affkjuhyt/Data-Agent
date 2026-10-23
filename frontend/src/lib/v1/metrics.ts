import { api, ok } from './client';
import { getMe } from './me';
import type { components } from './schema';

/*
 * A metric of the context layer (context.metric, context.metric_version) for "P07 · Chỉ số ·
 * Định nghĩa" (2301:19011, UC-07-01). A2: GET /v1/metrics/{id} "kèm danh sách phiên bản và nơi
 * đang dùng", POST /v1/metrics/{id}/versions (a draft), POST /v1/metric-versions/{id}:preview
 * (202; six periods against the version in use and the filed figures). The api's MetricDetail
 * and MetricVersionView are read into the screen's shapes below; the PROPOSAL fields fall back
 * to what the api has.
 */

type Schemas = components['schemas'];
export type MetricVersionStatus =
  | 'draft'
  | 'testing'
  | 'pending_review'
  | 'certified'
  | 'superseded'
  | 'broken';

export interface MetricVersion {
  id: string;
  version: number;
  display_name: string;
  expression: string;
  unit: string | null;
  decimals: number;
  direction: 'higher_better' | 'lower_better' | 'neutral';
  period_semantics: 'end_of_period' | 'sum_over_period' | 'average_over_period';
  time_grain: 'day' | 'month' | 'quarter' | 'year';
  /** PROPOSAL: allowed_dimensions are uuids; the screen needs their names. */
  dimension_labels: string[];
  change_note: string | null;
  status: MetricVersionStatus;
  certified_at: string | null;
  /** PROPOSAL: the certifier's name for the line under the title. */
  certified_by_name: string | null;
  created_at: string;
}

export interface MetricDetail {
  id: string;
  code: string;
  status: 'draft' | 'active' | 'retired';
  current_version: number | null;
  versions: MetricVersion[];
  /** "nơi đang dùng", in words: "dùng ở 2 data product, 1 trang tri thức". */
  usage_text: string;
  /** PROPOSAL: owner, main consumers and canonical source, each with what the role means here. */
  stewardship: { role: string; who: string; note: string }[];
}

/** PROPOSAL: the display fields the mock sends with a version and a metric. */
export type VersionWire = Schemas['MetricVersionView'] &
  Partial<Pick<MetricVersion, 'dimension_labels' | 'certified_by_name'>>;
export type MetricWire = Omit<Schemas['MetricDetail'], 'versions'> & {
  versions?: VersionWire[];
  stewardship?: MetricDetail['stewardship'];
  /** PROPOSAL: the counts "P07 · Chỉ số · Định nghĩa" was drawn with. */
  usage_counts?: { artifacts: number; agents: number; reports: number };
};

const readVersion = (raw: VersionWire): MetricVersion => ({
  id: raw.id,
  version: raw.version,
  display_name: raw.display_name,
  expression: raw.expression,
  unit: raw.unit,
  decimals: raw.decimals,
  direction: raw.direction as MetricVersion['direction'],
  period_semantics: raw.period_semantics as MetricVersion['period_semantics'],
  time_grain: raw.time_grain as MetricVersion['time_grain'],
  // allowed_dimensions are what the version allows; their names are PROPOSAL.
  dimension_labels: raw.dimension_labels ?? raw.allowed_dimensions,
  change_note: raw.change_note,
  status: raw.status as MetricVersionStatus,
  certified_at: raw.certified_at,
  certified_by_name: raw.certified_by_name ?? null,
  created_at: raw.created_at,
});

function usageText(raw: MetricWire): string {
  if (raw.usage_counts) {
    const { artifacts, agents, reports } = raw.usage_counts;
    return `dùng ở ${artifacts} artifact, ${agents} agent, ${reports} báo cáo định kỳ`;
  }
  const products = raw.usage?.data_product_ids?.length ?? 0;
  const pages = raw.usage?.knowledge_page_ids?.length ?? 0;
  return `dùng ở ${products} data product, ${pages} trang tri thức`;
}

const readMetric = (raw: MetricWire): MetricDetail => ({
  id: raw.id,
  code: raw.code,
  status: raw.status as MetricDetail['status'],
  current_version: raw.current_version,
  versions: (raw.versions ?? []).map(readVersion),
  usage_text: usageText(raw),
  stewardship: raw.stewardship ?? [],
});

export const getMetric = async (id: string) =>
  readMetric(await ok<MetricWire>(api.GET('/v1/metrics/{id}', { params: { path: { id } } })));

/** A new draft starts as a copy of the version in use; the change note says why. */
export const createMetricDraft = async (id: string, from: MetricVersion, changeNote: string) =>
  readVersion(
    await ok<VersionWire>(
      api.POST('/v1/metrics/{id}/versions', {
        params: { path: { id } },
        body: {
          display_name: from.display_name,
          expression: from.expression,
          unit: from.unit,
          decimals: from.decimals,
          direction: from.direction,
          period_semantics: from.period_semantics,
          time_grain: from.time_grain,
          change_note: changeNote,
        } satisfies Schemas['MetricVersionCreate'],
      }),
    ),
  );

export interface NewMetric {
  code: string;
  display_name: string;
  expression: string;
  unit: string | null;
  period_semantics: MetricVersion['period_semantics'];
  canonical_dataset_id: string | null;
}

/**
 * POST /v1/metrics (steward, UC-07-01 step 1): the metric with its first version as a draft,
 * owned by whoever creates it; the draft is then previewed and proposed like any other version.
 * Returns the new metric's id.
 */
export const createMetric = async (metric: NewMetric): Promise<string> => {
  const me = await getMe();
  const created = await ok<{ id: string }>(
    api.POST('/v1/metrics', {
      body: {
        code: metric.code,
        owner_id: me.id,
        canonical_dataset_id: metric.canonical_dataset_id,
        definition: {
          display_name: metric.display_name,
          expression: metric.expression,
          unit: metric.unit,
          period_semantics: metric.period_semantics,
          change_note: 'Tạo chỉ số',
        },
      } satisfies Schemas['MetricCreate'],
    }),
  );
  return created.id;
};

export const previewMetricVersion = async (versionId: string) =>
  ok<Schemas['services__api__modules__context__schemas__Accepted']>(
    api.POST('/v1/metric-versions/{id}:preview', {
      params: { path: { id: versionId } },
      body: { periods: 6 } satisfies Schemas['MetricPreview'],
    }),
  );
