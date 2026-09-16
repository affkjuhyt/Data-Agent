// Source-type filter chips. These tokens are sent verbatim to the backend
// (`?source=doc,media,web`), which maps them to KBData.source_type values
// (media → image+video) and does the `WHERE source_type IN (...)` filtering.
// Multi-select, default all-on — there is no "All" chip (all-on === everything).
// Warehouses are managed in their own Warehouses sub-tab (WarehouseCenter), not in
// the Data Layer / Knowledge Hub document listings — so 'warehouse' is intentionally
// NOT a browsable source category here.
export type SourceCategory = 'doc' | 'media' | 'web' | 'api';

export const SOURCE_CATEGORIES: { id: SourceCategory; label: string }[] = [
  { id: 'doc',       label: 'Doc' },
  { id: 'media',     label: 'Media' },
  { id: 'web',       label: 'Web' },
  // Connector feeds (ankt.source). Without this chip the default all-on filter
  // sends `?source=doc,media,web`, which matches no `api` row — every artifact
  // a connector stored is in the database and invisible on the screen.
  { id: 'api',       label: 'API' },
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
  row_version: string;
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
  row_version: string;
}
