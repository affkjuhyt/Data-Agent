/* ─── Tab types ────────────────────────────────────────────────────── */
export type GraphHubTab = 'DATABASE' | 'QDRANT' | 'NEO4J';
export type DBSubType = 'All' | 'Document' | 'Media' | 'Web' | 'Warehouse';

/* ─── Database document (Gold layer) ─────────────────────────────── */
export interface DBDocument {
  id: string;
  name: string;
  type: string;
  added_date: string;
  language: string;
  author: string;
  version: string;
  subType: DBSubType;
}

export const getSubType = (type: string): DBSubType => {
  if (!type) return 'Document';
  if (type.startsWith('Doc/'))    return 'Document';
  if (type.startsWith('Video/') || type.startsWith('Image/')) return 'Media';
  if (type.toLowerCase() === 'web') return 'Web';
  if (type.startsWith('Warehouse/')) return 'Warehouse';
  return 'Document';
};

/* ─── Warehouse types ────────────────────────────────────────────── */
export interface WTableEntry {
  id: string;
  schema: string;
  tableName: string;
  rowCount: number;
  columns: number;
}

export interface WConfig {
  id: string;
  name: string;
  version: string;
  createdAt: string;
  status: 'Active' | 'Draft' | 'Archived';
  syncSchedule: string;
  tables: WTableEntry[];
  connection?: Record<string, string>; // real credentials from backend
  row_version: string;
}

export interface WConnection {
  platform: string;
  host: string;
  database?: string;
  catalog?: string;
  warehouse?: string;
  cluster?: string;
  role?: string;
}

export const getWarehouseConnection = (
  type: string,
  stored?: Record<string, string>,
): WConnection => {
  const platform = type.startsWith('Warehouse/') ? type.replace('Warehouse/', '') : type;
  const platformLabel = platform.charAt(0).toUpperCase() + platform.slice(1);

  if (stored && Object.keys(stored).length > 0) {
    return {
      platform:  platformLabel,
      host:      stored.account ?? stored.host ?? '',
      database:  stored.database,
      catalog:   stored.catalog,
      warehouse: stored.warehouse,
      cluster:   stored.cluster,
      role:      stored.role,
    };
  }
  return { platform: platformLabel, host: '' };
};

export const formatRows = (n: number): string => {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toString();
};

export const nextVersion = (existing: string[]): string => {
  if (existing.length === 0) return 'v1.0';
  let major = 1, minor = 0;
  for (const v of existing) {
    const m = v.match(/v?(\d+)\.(\d+)/);
    if (!m) continue;
    const majN = parseInt(m[1], 10), minN = parseInt(m[2], 10);
    if (majN > major || (majN === major && minN >= minor)) { major = majN; minor = minN; }
  }
  return `v${major}.${minor + 1}`;
};

/* ─── Chunk / Version types ──────────────────────────────────────── */
export interface ChunkVersion {
  version_number: string;
  create_at: string;
  status: 'Active' | 'Inactive';
  embedding_models: string;
  entities: string[];
  intent: string;
  text: string;
}

export interface Chunk {
  id: string;
  title: string;
  text: string;
  versions: ChunkVersion[];
}

/* ─── Qdrant types ───────────────────────────────────────────────── */
export interface QdrantCollection {
  id: string;
  name: string;
  points: number;          // scoped to the current tenant
  total_points?: number;   // all tenants (context)
  active: boolean;
  dimensions: number;
  distance: string;
  indexed: number;
  embedding_model?: string;
}

export interface QdrantPoint {
  point_id: string;
  score: number;
  summary: string;
  entities: string[];
  intent: string[];
}

/* ─── Neo4j graph types ──────────────────────────────────────────── */
export interface GraphNode { id: string; name: string; description?: string; type?: string | null; }
export interface GraphEdge { from: string; to: string; description?: string; score?: number; }
export interface Neo4jSchema { entities: string[]; connections: Record<string, string[]>; }

/* ─── Node colouring by type ─────────────────────────────────────── */
/* Fixed colours for the known entity labels; anything else gets a stable
 * colour hashed from its name so every distinct type still reads differently. */
const NODE_TYPE_COLORS: Record<string, string> = {
  Person:        '#F2C14E',
  Organization:  '#5BA8F5',
  Location:      '#5BD6A0',
  Event:         '#E87D7D',
  Concept:       '#B98AE6',
  LawDocument:   '#F29D52',
  SystemProduct: '#4FD0D6',
  TimePeriod:    '#D6CF5B',
  Entity:        '#9AA4B2',
};

const _FALLBACK_PALETTE = ['#7FB3D5', '#76D7C4', '#F1948A', '#BB8FCE', '#F8C471', '#73C6B6', '#E59866', '#85C1E9'];

export const nodeColor = (type?: string | null): string => {
  if (!type) return '#B88719';
  if (NODE_TYPE_COLORS[type]) return NODE_TYPE_COLORS[type];
  let h = 0;
  for (let i = 0; i < type.length; i++) h = (h * 31 + type.charCodeAt(i)) >>> 0;
  return _FALLBACK_PALETTE[h % _FALLBACK_PALETTE.length];
};

/* ─── Neo4j query builder ────────────────────────────────────────── */
export interface QueryStep { id: string; relationship: string; nodeType: string; }


/**
 * `startNode` / step targets are node LABELS (entity types), not names, and the
 * graph uses many different relationship types — so we match on any relationship
 * (`-[r]-`, undirected) instead of a hard-coded type. With no steps we pull the
 * chosen type's immediate neighbourhood so the canvas always shows a connected
 * subgraph. Every query is capped so the backend never returns more than `max`.
 */
export const buildCypher = (startNode: string, steps: QueryStep[], max = 200): string => {
  if (!startNode) return '';
  // Every matched node is scoped to the caller's tenant via the $tenant_id param
  // (supplied by the backend) — same scoping the full-graph endpoint applies.
  if (steps.length === 0) {
    return `MATCH (a:${startNode})\nWHERE a.tenant_id = $tenant_id\n`
         + `OPTIONAL MATCH (a)-[r]-(b) WHERE b.tenant_id = $tenant_id\n`
         + `RETURN a, r, b\nLIMIT ${max}`;
  }
  let pattern = `(a:${startNode})`;
  const vars = ['a'];
  const tenantPreds = ['a.tenant_id = $tenant_id'];
  steps.forEach((s, i) => {
    const v = String.fromCharCode(98 + i); // b, c, d, …
    pattern += `-[r${i}]-(${v}:${s.nodeType})`;
    // Return the relationship vars too — result.graph() only includes edges that
    // are actually returned, so omitting them yields nodes with no links.
    vars.push(`r${i}`, v);
    tenantPreds.push(`${v}.tenant_id = $tenant_id`);
  });
  return `MATCH ${pattern}\nWHERE ${tenantPreds.join(' AND ')}\nRETURN ${vars.join(', ')}\nLIMIT ${max}`;
};

/**
 * Offline fallback for the query builder: the builder's start node is an entity
 * TYPE (label), so seed from every node of that type and expand `hops` steps.
 * Used only when the backend can't run the generated Cypher (Neo4j offline).
 */
export function subgraphByType(
  nodes: GraphNode[],
  edges: GraphEdge[],
  type: string,
  hops: number,
): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const want = type.trim().toLowerCase();
  const seeds = nodes.filter(n => (n.type ?? '').toLowerCase() === want);
  if (!seeds.length) return { nodes: [], edges: [] };

  const adj = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    (adj.get(e.from) ?? adj.set(e.from, []).get(e.from)!).push(e);
    (adj.get(e.to) ?? adj.set(e.to, []).get(e.to)!).push(e);
  }

  const keep = new Set(seeds.map(n => n.id));
  let frontier = [...keep];
  for (let d = 0; d < Math.max(hops, 1); d++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const e of adj.get(id) ?? []) {
        const other = e.from === id ? e.to : e.from;
        if (!keep.has(other)) { keep.add(other); next.push(other); }
      }
    }
    frontier = next;
  }

  return {
    nodes: nodes.filter(n => keep.has(n.id)),
    edges: edges.filter(e => keep.has(e.from) && keep.has(e.to)),
  };
}
