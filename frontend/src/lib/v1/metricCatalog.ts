import { type Page, api, ok } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * "v3.1 · Dữ liệu & tri thức" (2056:1150 and its tabs, UC-07-01): metrics as a catalog — browse
 * and filter them, and read one: its definition and lineage, the agents using it, a change an
 * agent drafted, its versions, where it is used and its quality checks. The list is GET
 * /v1/metrics with PROPOSAL display fields (docs/v3/05-ui-v3.md §4); one metric is read from A2
 * (below). Editing stays on the definition page (GET/POST /v1/metrics/{id}…).
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
export type CatalogMetricWire = Schemas['MetricView'] &
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
  const page = await ok<Page<CatalogMetricWire>>(
    api.GET('/v1/metrics', {
      params: {
        query: {
          view: 'catalog',
          page_size: 500,
        },
      },
    }),
  );
  return { ...page, items: page.items.map(readCatalogMetric) };
};

/**
 * A metric read whole: A2 has no single "profile", so it is put together from GET /v1/metrics/{id}
 * (the metric with its versions and where it is used), GET /v1/lineage (from source to use) and
 * the quality results of its canonical dataset (GET /v1/quality-rules, GET /v1/quality-results).
 */
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

type MetricDetailWire = Schemas['MetricDetail'];
type VersionView = Schemas['MetricVersionView'];
type LineageGraph = Schemas['LineageGraph'];
type LineageNode = Schemas['LineageNode'];

const KIND_LABEL: Record<string, string> = {
  source: 'Nguồn',
  dataset: 'Dataset',
  column: 'Cột',
  metric: 'Chỉ số',
  tile: 'Ô',
  report: 'Báo cáo',
  skill: 'Kỹ năng',
  agent: 'Agent',
  data_product: 'Data product',
};

const day = (iso: string | null | undefined) =>
  iso ? iso.slice(0, 10).split('-').reverse().join('/') : '—';

/** draft, testing and pending_review are not in use yet; superseded and broken are history. */
function versionStatus(status: string): MetricProfile['versions'][number]['status'] {
  if (status === 'certified') return 'certified';
  return status === 'superseded' || status === 'broken' ? 'archived' : 'draft';
}

/**
 * Upstream nodes farthest first, then the metric, and the downstream ones apart: edges run from
 * source to target, so walking them backwards from the root gives what the metric is built from.
 */
function splitLineage(graph: LineageGraph) {
  const byKey = new Map(graph.nodes.map((n) => [n.key, n]));
  const walk = (from: 'source' | 'target', to: 'source' | 'target') => {
    const seen = new Set([graph.root.key]);
    const order: LineageNode[] = [];
    let frontier = [graph.root.key];
    while (frontier.length) {
      const next: string[] = [];
      for (const key of frontier)
        for (const e of graph.edges)
          if (e[to] === key && !seen.has(e[from])) {
            seen.add(e[from]);
            next.push(e[from]);
            const node = byKey.get(e[from]);
            if (node && !node.hidden) order.push(node);
          }
      frontier = next;
    }
    return order;
  };
  return { upstream: walk('source', 'target').reverse(), downstream: walk('target', 'source') };
}

const nodeTitle = (n: LineageNode) => n.name ?? n.key;

async function readQuality(datasetId: string | null): Promise<MetricProfile['quality']> {
  if (!datasetId) return [];
  const [rules, results] = await Promise.all([
    ok<Page<Schemas['QualityRuleView']>>(
      api.GET('/v1/quality-rules', { params: { query: { dataset_id: datasetId } } }),
    ),
    ok<Page<Schemas['QualityResultView']>>(
      api.GET('/v1/quality-results', {
        params: {
          query: {
            dataset_id: datasetId,
            page_size: 100,
          },
        },
      }),
    ),
  ]);
  const ruleOf = new Map(rules.items.map((r) => [r.id, r]));
  // The latest result of each rule: results come newest first.
  const latest = new Map<string, Schemas['QualityResultView']>();
  for (const r of results.items) if (!latest.has(r.rule_id)) latest.set(r.rule_id, r);
  return [...latest.values()].map((r) => {
    const rule = ruleOf.get(r.rule_id);
    const what = rule ? [rule.kind, rule.column_name].filter(Boolean).join(' · ') : 'Kiểm tra';
    return {
      ok: r.passed,
      label: r.passed ? 'Đạt' : 'Cảnh báo',
      text: `${what} · ${r.passed ? 'không vi phạm' : `${r.violations} vi phạm`} · ${day(r.checked_at)}`,
    };
  });
}

/** Lineage and quality are side panels: a failure there leaves the metric readable. */
const orEmpty = <T>(read: Promise<T>, empty: T) => read.catch(() => empty);

export function readMetricProfile(
  raw: MetricDetailWire,
  graph: LineageGraph | null,
  quality: MetricProfile['quality'],
): MetricProfile {
  const versions = [...(raw.versions ?? [])].sort((a, b) => b.version - a.version);
  const current: VersionView | undefined =
    versions.find((v) => v.version === raw.current_version) ?? versions[0];
  const draft = versions.find(
    (v) => versionStatus(v.status) === 'draft' && v.version > (raw.current_version ?? 0),
  );
  const { upstream, downstream } = graph ? splitLineage(graph) : { upstream: [], downstream: [] };
  const versionText = raw.current_version != null ? `v${raw.current_version}` : '—';
  const products = raw.usage?.data_product_ids?.length ?? 0;
  const pages = raw.usage?.knowledge_page_ids?.length ?? 0;
  return {
    id: raw.id,
    code: raw.code,
    name: current?.display_name ?? raw.display_name ?? raw.code,
    status: raw.certified ? 'certified' : 'draft',
    meta_line: [
      raw.code,
      versionText,
      current?.valid_from ? `hiệu lực từ ${day(current.valid_from)}` : null,
      current?.unit ? `đơn vị ${current.unit}` : null,
    ]
      .filter(Boolean)
      .join(' · '),
    definition: {
      expression: current?.expression ?? '',
      note: current?.description ?? '',
    },
    lineage: graph
      ? [
          ...upstream.map((n) => ({
            kind: KIND_LABEL[n.kind] ?? n.kind,
            title: nodeTitle(n),
            detail: '',
          })),
          {
            kind: KIND_LABEL.metric,
            title: `${raw.code} ${versionText}`,
            detail: '',
            current: true,
          },
          ...(downstream.length
            ? [{ kind: 'Sử dụng', title: `${downstream.length} nơi dùng`, detail: '' }]
            : []),
        ]
      : [],
    agents: downstream
      .filter((n) => n.kind === 'agent')
      .map((n) => ({ name: nodeTitle(n), state: 'Dùng chỉ số này', tone: 'idle' as const })),
    change_notice: draft
      ? {
          title: `Đang có bản nháp v${draft.version}`,
          text: draft.change_note ?? '',
          draft_version: draft.version,
        }
      : null,
    versions: versions.map((v) => ({
      version: v.version,
      change: v.change_note ?? '—',
      effective: day(v.valid_from),
      status: versionStatus(v.status),
    })),
    usage: downstream.map((n) => ({
      place: nodeTitle(n),
      kind: KIND_LABEL[n.kind] ?? n.kind,
      version: versionText,
      last: '—',
    })),
    usage_note: `Dùng ở ${products} data product, ${pages} trang tri thức.`,
    quality,
  };
}

export async function getMetricProfile(id: string): Promise<MetricProfile> {
  const raw = await ok<MetricDetailWire>(api.GET('/v1/metrics/{id}', { params: { path: { id } } }));
  const [graph, quality] = await Promise.all([
    orEmpty(
      ok<LineageGraph>(
        api.GET('/v1/lineage', {
          params: {
            query: {
              object_kind: 'metric',
              object_id: raw.id,
              direction: 'both',
              depth: 3,
            },
          },
        }),
      ),
      null,
    ),
    orEmpty(readQuality(raw.canonical_dataset_id), []),
  ]);
  return readMetricProfile(raw, graph, quality);
}
