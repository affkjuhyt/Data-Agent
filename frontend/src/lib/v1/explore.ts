import type { Page } from './client';
import { pathId, v1Get, v1Send } from './client';
import type { DataSpace } from './types';

/*
 * Khám phá (UC-01-04), the full-screen artifact (UC-01-01) and the global first-visit screens
 * (UC-11-08: Chọn Data Space, onboarding, "Data Agent hoạt động thế nào").
 *
 * A2 has no endpoint for self-service exploration over the semantic layer, artifacts outside a
 * notebook tile, the first-visit flow or the viewer's Data Space list with access states, so
 * every path below is a PROPOSAL; each field the screens need is marked where it sits.
 */

/* ─── Khám phá: bộ dữ liệu, chỉ số, chiều ──────────────────────────────── */

/** PROPOSAL: GET /v1/explore/datasets — certified datasets the viewer may explore. */
export interface ExploreDataset {
  id: string;
  display_name: string;
  certified: boolean;
  /** PROPOSAL: "dữ liệu tới 30/09" */
  data_until_text: string;
}

export interface ExploreMetric {
  id: string;
  label: string;
  code: string;
  /** The ✓ in the field list: the metric has a certified version. */
  certified: boolean;
}

export interface ExploreDimension {
  id: string;
  label: string;
  /** The group heading in the field list: "Chi nhánh", "Thời gian", "Khoản vay". */
  group_label: string;
  values: { value: string; label: string }[];
  /** PROPOSAL: metrics this dimension cannot split, and why (shown dimmed with the reason). */
  incompatible_metric_ids: string[];
  incompatible_reason: string | null;
}

/** PROPOSAL: GET /v1/explore/datasets/{id}/model — the semantic model behind the field list. */
export interface ExploreModel {
  dataset: ExploreDataset;
  metrics: ExploreMetric[];
  dimensions: ExploreDimension[];
  /** PROPOSAL: the view the dataset opens on; null = empty shelves. */
  default_query: ExploreQueryInput | null;
}

export interface ExploreFilter {
  dimension_id: string;
  op: 'eq' | 'in';
  values: string[];
}

export interface ExploreQueryInput {
  dataset_id: string;
  metric_ids: string[];
  dimension_ids: string[];
  filters: ExploreFilter[];
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

export interface ExploreRow {
  key: string;
  label: string;
  /** The axis label under a column: "ĐB s.Hồng" for "ĐB sông Hồng". */
  short_label: string;
  /** One value per series, in series order. */
  values: (number | null)[];
  /** Last series minus the first, in the value's unit; null with one series. */
  delta: number | null;
}

/** PROPOSAL: POST /v1/explore/queries — runs the shelves against the semantic layer (a query_run). */
export interface ExploreResult {
  id: string;
  title: string;
  /** "NPL_RATIO v4 · 0,8 giây" */
  subtitle_text: string;
  /** The dimension of the categories (columns of the chart, rows of the table). */
  category_label: string;
  series: { key: string; label: string }[];
  rows: ExploreRow[];
  unit: 'percent' | 'count' | 'amount';
  /** PROPOSAL: a rise is bad news for this metric (NPL), so increases show in the error colour. */
  increase_is_bad: boolean;
  /** How many detail rows sit behind the figures ("Xem 3.412 dòng gốc"). */
  detail_row_count: number;
  /** The dimension a click on a column drills into, if any. */
  drill_dimension_id: string | null;
  next_steps: NextStep[];
  /** "Khớp số đã nộp và Báo cáo rủi ro tháng 9", when the figures match a filed report. */
  reconciliation_text: string | null;
  data_space_id: string;
}

/** PROPOSAL: GET /v1/explore/queries/{id}/explanation — the agent reads the result. */
export interface ExploreExplanation {
  headline: string;
  body: string;
}

/**
 * PROPOSAL: GET /v1/explore/queries/{id}/rows — the detail rows behind the figures; 403
 * `aggregate_only` when the viewer's role sees aggregates only.
 */
export interface ExploreRows {
  columns: string[];
  rows: string[][];
  total: number;
}

export const listExploreDatasets = () =>
  v1Get<Page<ExploreDataset>>('/explore/datasets', { page_size: 100 });

export const getExploreModel = async (datasetId: string) =>
  v1Get<ExploreModel>(`/explore/datasets/${pathId(datasetId)}/model`);

/** 422 `incompatible_dimension` when the shelves cannot be answered, with the reason as detail. */
export const runExploreQuery = (input: ExploreQueryInput) =>
  v1Send<ExploreResult>('POST', '/explore/queries', input);

export const getExploreExplanation = async (queryId: string) =>
  v1Get<ExploreExplanation>(`/explore/queries/${pathId(queryId)}/explanation`);

export const getExploreRows = async (queryId: string) =>
  v1Get<ExploreRows>(`/explore/queries/${pathId(queryId)}/rows`, { page_size: 50 });

/** PROPOSAL: "Hỏi bằng lời" — the agent fills the shelves from a question for the viewer to check. */
export const interpretQuestion = (datasetId: string, question: string) =>
  v1Send<ExploreQueryInput>('POST', '/explore/queries:interpret', {
    dataset_id: datasetId,
    question,
  });

/** PROPOSAL: adds the current query as a tile of a dashboard. */
export const saveQueryToDashboard = async (queryId: string, dashboardId: string) =>
  v1Send<{ dashboard_id: string; tile_id: string }>(
    'POST',
    `/explore/queries/${pathId(queryId)}:save-to-dashboard`,
    { dashboard_id: dashboardId },
  );

/* ─── Artifact toàn màn (UC-01-01) ─────────────────────────────────────── */

export interface ArtifactBar {
  label: string;
  value: number;
  value_text: string;
  /** above = over the watch threshold (chart-3), down = a decrease (chart-1). */
  tone: 'above' | 'normal' | 'down';
}

export interface ArtifactRow {
  cells: string[];
  /** Direction of the change cell, for its status dot. */
  trend: 'up' | 'down' | 'flat';
}

export interface ArtifactVersion {
  version: number;
  summary: string;
  /** "Agent Phân tích · 09:58" */
  author_text: string;
}

export interface ArtifactUsage {
  id: string;
  /** "Báo cáo rủi ro T9 · Hình 1 · tự cập nhật" */
  text: string;
  /** PROPOSAL: the version a snapshot is pinned to; null = follows the current one. */
  pinned_version: number | null;
}

/** PROPOSAL: GET /v1/artifacts/{id} — one answer or notebook artifact with its versions. */
export interface Artifact {
  id: string;
  title: string;
  session: { id: string; title: string } | null;
  version: number;
  /** The figures passed the checks ("Đã kiểm tra số"). */
  checked: boolean;
  /** "Agent Phân tích · 09:58 · NPL_RATIO v4 · snapshot 30/09 v3 · dữ liệu tới 30/09 23:59" */
  meta_text: string;
  chart: { bars: ArtifactBar[]; caption: string };
  table: { columns: string[]; rows: ArtifactRow[]; change_column: number };
  sql: string;
  /** Newest first. */
  versions: ArtifactVersion[];
  /** What changed from the previous version. */
  compare: { label: string; text: string } | null;
  used_in: ArtifactUsage[];
  saved: boolean;
  watching: boolean;
}

export const getArtifact = async (id: string) => v1Get<Artifact>(`/artifacts/${pathId(id)}`);

/** PROPOSAL: makes an older version current again (a new version with its content). */
export const restoreArtifactVersion = async (id: string, version: number) =>
  v1Send<Artifact>('POST', `/artifacts/${pathId(id)}:restore`, { version });

export const saveArtifact = async (id: string) =>
  v1Send<Artifact>('POST', `/artifacts/${pathId(id)}:save`);

export const watchArtifact = async (id: string) =>
  v1Send<Artifact>('POST', `/artifacts/${pathId(id)}:watch`);

/** PROPOSAL: moves the given usages to the current version. */
export const updateArtifactUsages = async (id: string, usageIds: string[]) =>
  v1Send<Artifact>('POST', `/artifacts/${pathId(id)}:update-usages`, { usage_ids: usageIds });

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
  const page = await v1Get<Page<DataSpace>>('/data-spaces', { page_size: 500 });
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
      last_used_text: null,
      owner_name: null,
      request_pending: false,
    })),
    open_last_used: false,
  };
};

/** PROPOSAL: PUT /v1/me/data-space-preference */
export const setOpenLastUsed = (openLastUsed: boolean) =>
  v1Send<{ open_last_used: boolean }>('PUT', '/me/data-space-preference', {
    open_last_used: openLastUsed,
  });

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

export const getOnboarding = () => v1Get<Onboarding>('/me/onboarding');

export const skipOnboarding = () => v1Send<Onboarding>('POST', '/me/onboarding:skip');

/** PROPOSAL: the viewer went through every step; the server stops showing onboarding. */
export const completeOnboarding = () => v1Send<Onboarding>('POST', '/me/onboarding:complete');

/** PROPOSAL: creates the agent from the template and starts a test run on the viewer's data (202). */
export const tryOnboardingAgent = (templateRef: string) =>
  v1Send<{ agent_id: string; run_id: string }>('POST', '/me/onboarding:try-agent', {
    template_ref: templateRef,
  });
