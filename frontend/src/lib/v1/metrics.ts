import { pathId, v1Get, v1Send } from './client';

/*
 * A metric of the context layer (context.metric, context.metric_version) for "P07 · Chỉ số ·
 * Định nghĩa" (2301:19011, UC-07-01). A2: GET /v1/metrics/{id} "kèm danh sách phiên bản và nơi
 * đang dùng", POST /v1/metrics/{id}/versions (a draft), POST /v1/metric-versions/{id}:preview
 * (202; six periods against the version in use and the filed figures).
 */
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
  /** "nơi đang dùng (artifact, agent, báo cáo)" */
  usage: { artifacts: number; agents: number; reports: number };
  /** PROPOSAL: owner, main consumers and canonical source, each with what the role means here. */
  stewardship: { role: string; who: string; note: string }[];
}

export const getMetric = async (id: string) => v1Get<MetricDetail>(`/metrics/${pathId(id)}`);

/** A new draft starts as a copy of the version in use; the change note says why. */
export const createMetricDraft = async (id: string, from: MetricVersion, changeNote: string) =>
  v1Send<MetricVersion>('POST', `/metrics/${pathId(id)}/versions`, {
    display_name: from.display_name,
    expression: from.expression,
    unit: from.unit,
    decimals: from.decimals,
    direction: from.direction,
    period_semantics: from.period_semantics,
    time_grain: from.time_grain,
    change_note: changeNote,
  });

export const previewMetricVersion = async (versionId: string) =>
  v1Send<{ status: 'queued' }>('POST', `/metric-versions/${pathId(versionId)}:preview`, {
    periods: 6,
  });
