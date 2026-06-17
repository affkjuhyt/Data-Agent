// Source-type filter chips. These tokens are sent verbatim to the backend
// (`?source=doc,media,web`), which maps them to KBData.source_type values
// (media → image+video) and does the `WHERE source_type IN (...)` filtering.
// Multi-select, default all-on — there is no "All" chip (all-on === everything).
export type SourceCategory = 'doc' | 'media' | 'web' | 'warehouse';

export const SOURCE_CATEGORIES: { id: SourceCategory; label: string }[] = [
  { id: 'doc',       label: 'Doc' },
  { id: 'media',     label: 'Media' },
  { id: 'web',       label: 'Web' },
  { id: 'warehouse', label: 'Warehouse' },
];

export const ALL_SOURCE_CATEGORIES: SourceCategory[] = SOURCE_CATEGORIES.map(c => c.id);

/** Stable comma-joined token for the `source` query param (canonical order). */
export const sourceParam = (selected: Set<SourceCategory>): string =>
  ALL_SOURCE_CATEGORIES.filter(c => selected.has(c)).join(',');

export interface ChunkVersion {
  version_number: string;
  create_at: string;
  status: string;
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

export interface TableColumn {
  name: string;
  type: string;
  nullable: boolean;
}

export interface DocTable {
  id: string;
  name: string;
  description: string;
  columns: TableColumn[];
  rows: Record<string, string | null>[];
}

export interface WarehouseConfigTable {
  name: string;
  schema: string;
  rowCount?: string;
  description: string;
}

export interface WarehouseConfigVersion {
  id: string;
  version_number: string;
  status: 'Active' | 'Inactive';
  created_at: string;
  connection: Record<string, string>;
  tables: WarehouseConfigTable[];
}

export const MOCK_DISCOVERY: Record<string, Omit<WarehouseConfigTable, 'description'>[]> = {
  snowflake: [
    { name: 'ORDERS',       schema: 'PUBLIC',  rowCount: '142,000'    },
    { name: 'PRODUCTS',     schema: 'PUBLIC',  rowCount: '8,500'      },
    { name: 'CUSTOMERS',    schema: 'PUBLIC',  rowCount: '45,000'     },
    { name: 'INVENTORY',    schema: 'PUBLIC',  rowCount: '23,100'     },
    { name: 'TRANSACTIONS', schema: 'FINANCE', rowCount: '890,000'    },
  ],
  databricks: [
    { name: 'user_embeddings', schema: 'feature_store', rowCount: '900,000'    },
    { name: 'item_features',   schema: 'feature_store', rowCount: '25,000'     },
    { name: 'training_logs',   schema: 'ml_ops',        rowCount: '45,600'     },
    { name: 'model_registry',  schema: 'ml_ops',        rowCount: '340'        },
    { name: 'raw_events',      schema: 'bronze',        rowCount: '12,400,000' },
  ],
};
