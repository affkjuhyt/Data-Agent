import React from 'react';
import { Database, FileText, Video, Globe, ServerCog } from 'lucide-react';

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

export const DB_SUBTYPES: { id: DBSubType; label: string; icon: React.ElementType }[] = [
  { id: 'All',       label: 'All',       icon: Database  },
  { id: 'Document',  label: 'Document',  icon: FileText  },
  { id: 'Media',     label: 'Media',     icon: Video     },
  { id: 'Web',       label: 'Web',       icon: Globe     },
  { id: 'Warehouse', label: 'Warehouse', icon: ServerCog },
];

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
  points: number;
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
export interface GraphNode { id: string; name: string; description?: string; }
export interface GraphEdge { from: string; to: string; description?: string; score?: number; }
export interface LayoutNode { id: string; x: number; y: number; r: number; label: string; core: boolean; }
export interface Neo4jSchema { entities: string[]; connections: Record<string, string[]>; }

export function computeGraphLayout(
  nodes: GraphNode[],
  edges: GraphEdge[],
): {
  layoutNodes: LayoutNode[];
  layoutEdges: Array<[number, number, number, number]>;
  layoutRelLabels: Array<[number, number, string]>;
} {
  if (!nodes.length) return { layoutNodes: [], layoutEdges: [], layoutRelLabels: [] };

  const cx = 450, cy = 280, radius = 190;
  const layoutNodes: LayoutNode[] = nodes.map((n, i) => {
    if (i === 0) return { id: n.id, x: cx, y: cy, r: 18, label: n.name, core: true };
    const angle = (2 * Math.PI * (i - 1)) / Math.max(nodes.length - 1, 1) - Math.PI / 2;
    return {
      id: n.id,
      x: Math.round(cx + radius * Math.cos(angle)),
      y: Math.round(cy + radius * Math.sin(angle)),
      r: 12,
      label: n.name,
      core: false,
    };
  });

  const posMap = new Map(layoutNodes.map(n => [n.id, { x: n.x, y: n.y }]));
  const layoutEdges: Array<[number, number, number, number]> = [];
  const layoutRelLabels: Array<[number, number, string]> = [];

  for (const e of edges) {
    const from = posMap.get(e.from);
    const to   = posMap.get(e.to);
    if (!from || !to) continue;
    layoutEdges.push([from.x, from.y, to.x, to.y]);
    const label = e.description?.match(/\[(\w+)\]/)?.[1] ?? '';
    if (label) layoutRelLabels.push([
      Math.round((from.x + to.x) / 2),
      Math.round((from.y + to.y) / 2),
      label,
    ]);
  }

  return { layoutNodes, layoutEdges, layoutRelLabels };
}

/* ─── Neo4j query builder ────────────────────────────────────────── */
export interface QueryStep { id: string; relationship: string; nodeType: string; }

export const AVAILABLE_NODES = ['Person','Organization','Location','Policy','Contract','Regulation','Product','Department','Event','Agreement'];
export const AVAILABLE_RELS  = ['WORKS_FOR','GOVERNED_BY','LOCATED_IN','PART_OF','REFERENCES','COMPLIES_WITH','CAUSES','RELATED_TO','SUPERSEDES','AUTHORIZED_BY'];

export const buildCypher = (startNode: string, steps: QueryStep[]): string => {
  let c = `MATCH (a:${startNode})`;
  const r = ['a'];
  steps.forEach((s, i) => {
    const v = String.fromCharCode(98 + i);
    c += `-[:${s.relationship}]->(${v}:${s.nodeType})`;
    r.push(v);
  });
  return c + `\nRETURN ${r.join(', ')}`;
};

export const PAGE_SIZE = 20;
