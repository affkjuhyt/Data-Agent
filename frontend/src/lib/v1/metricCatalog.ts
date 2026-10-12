import { pathId, v1Get, type Page } from './client';
import type { components } from './schema';

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

/** GET /v1/metrics as the api sends it (MetricView), with the PROPOSAL catalog fields. */
export type CatalogMetricWire = components['schemas']['MetricView'] &
  Partial<Pick<CatalogMetric, 'version' | 'name' | 'domain' | 'usage_text'>>;

const readCatalogMetric = (raw: CatalogMetricWire): CatalogMetric => ({
  id: raw.id,
  code: raw.code,
  version: raw.version ?? raw.current_version ?? 0,
  name: raw.name ?? raw.display_name ?? raw.code,
  status: raw.certified ? 'certified' : 'draft',
  domain: raw.domain ?? '',
  usage_text: raw.usage_text ?? '',
});

/** `view=catalog` (PROPOSAL) asks for the catalog fields; the api ignores it and sends MetricView. */
export const listCatalogMetrics = async () => {
  const page = await v1Get<Page<CatalogMetricWire>>('/metrics', {
    view: 'catalog',
    page_size: 500,
  });
  return { ...page, items: page.items.map(readCatalogMetric) };
};

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
