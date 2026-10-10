import { pathId, v1Get, type Page } from './client';

/*
 * "v3.1 · Dữ liệu & tri thức" (2056:1150 and its tabs, UC-07-01): metrics as a catalog — browse
 * and filter them, and read one: its definition and lineage, the agents using it, a change an
 * agent drafted, its versions, where it is used and its quality checks. Both reads are PROPOSALs
 * over context.metric and context.metric_version (docs/v3/05-ui-v3.md §4); editing stays on the
 * definition page (GET/POST /v1/metrics/{id}…).
 */

export type MetricStatus = 'certified' | 'draft';

/** PROPOSAL: GET /v1/metrics?view=catalog */
export interface CatalogMetric {
  id: string;
  code: string;
  version: number;
  name: string;
  status: MetricStatus;
  domain: string;
  /** "5 agent · 23 artifact", "do Agent Phân tích đề xuất" */
  usage_text: string;
}

export const listCatalogMetrics = async () =>
  v1Get<Page<CatalogMetric>>('/metrics', { view: 'catalog', limit: 200 });

/** PROPOSAL: GET /v1/metrics/{id}/profile */
export interface MetricProfile {
  id: string;
  code: string;
  name: string;
  status: MetricStatus;
  meta_line: string;
  definition: { expression: string; note: string };
  lineage: { kind: string; title: string; detail: string; current?: boolean }[];
  agents: { name: string; state: string; tone: 'success' | 'warning' | 'idle' }[];
  change_notice: { title: string; text: string; draft_version: number } | null;
  versions: {
    version: number;
    change: string;
    effective: string;
    status: 'draft' | 'certified' | 'archived';
  }[];
  usage: { place: string; kind: string; version: string; last: string }[];
  usage_note: string;
  quality: { ok: boolean; label: string; text: string }[];
}

export const getMetricProfile = async (id: string) =>
  v1Get<MetricProfile>(`/metrics/${pathId(id)}/profile`);
