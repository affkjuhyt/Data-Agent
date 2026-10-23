import { type Page, api, ok } from './client';
import { listForecastSeries } from './forecast';
import { readDataset, type Dataset, type DatasetWire } from './types';

/**
 * One row of the catalog (R1-04). The V3 design has no `catalog.asset` table: the catalog is a
 * view over what A2 serves, each row keeping its own object's id (dataset, column, document,
 * REST source). A series is a forecast-ledger series (`ledger~model~product`), the list
 * GET /v1/forecast-ledger returns: A2 serves no other series list (`skill.external_series` has no
 * read endpoint), so a Data Space with no forecast shows no series.
 */
export type CatalogKind = 'table' | 'column' | 'document' | 'series' | 'api';

export type CatalogLink = {
  area: 'data' | 'analysis';
  page: string;
  id?: string;
};

export type CatalogAsset = {
  id: string;
  kind: CatalogKind;
  name: string;
  source: string;
  /** Where the row opens. */
  open: CatalogLink;
  /** The thing this asset was taken from, when that thing has its own screen. */
  sourceLink: CatalogLink | null;
};

const ALL = { page_size: 500 };

const ORIGIN: Record<string, string> = {
  source: 'Nguồn đã kết nối',
  derived: 'Bảng dẫn xuất',
  upload: 'Tải lên',
  data_product: 'Sản phẩm dữ liệu',
  consolidated: 'Hợp nhất',
};

type DocumentRow = {
  id: string;
  title: string | null;
  file_name: string;
  doc_no: string | null;
  collection_id: string;
  document_source_id: string | null;
};

type SourceRow = {
  id: string;
  name: string;
  kind: string;
  host: string | null;
};

/** REST APIs are catalog assets. A database connection stays a source of its tables. */
export function restApis(sources: SourceRow[]): { id: string; name: string; endpoint: string }[] {
  return sources
    .filter((source) => source.kind === 'rest_api')
    .map((source) => ({ id: source.id, name: source.name, endpoint: source.host ?? 'API' }));
}

export type CatalogColumn = {
  id: string;
  name: string;
  display_name: string | null;
};

const COLUMN_FETCHES = 6;

async function mapLimited<T, R>(
  items: T[],
  limit: number,
  run: (item: T) => Promise<R>,
): Promise<R[]> {
  const results = new Array<R>(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const index = next;
      next += 1;
      results[index] = await run(items[index]);
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return results;
}

/** Current columns of each table, from GET /v1/datasets/{id}. A failed read leaves that table's columns out. */
export async function columnsByDataset(datasets: Dataset[]): Promise<Map<string, CatalogColumn[]>> {
  const details = await mapLimited(datasets, COLUMN_FETCHES, async (dataset) => {
    try {
      const detail = await ok<{ columns?: CatalogColumn[] }>(
        api.GET('/v1/datasets/{id}', { params: { path: { id: dataset.id } } }),
      );
      return [dataset.id, detail.columns ?? []] as [string, CatalogColumn[]];
    } catch {
      return [dataset.id, []] as [string, CatalogColumn[]];
    }
  });
  return new Map(details);
}

export function catalogAssets(
  datasets: Dataset[],
  documents: DocumentRow[],
  series: { id: string; label: string }[],
  apis: { id: string; name: string; endpoint: string }[] = [],
  columns: ReadonlyMap<string, CatalogColumn[]> = new Map(),
): CatalogAsset[] {
  const tables: CatalogAsset[] = datasets.flatMap((dataset) => {
    const table: CatalogAsset = {
      id: dataset.id,
      kind: 'table',
      name: dataset.display_name,
      source: dataset.source_id
        ? `${dataset.schema_name}.${dataset.table_name}`
        : (ORIGIN[dataset.origin] ?? dataset.origin),
      open: { area: 'data', page: 'TABLE', id: dataset.id },
      sourceLink: dataset.source_id
        ? { area: 'data', page: 'SOURCE', id: dataset.source_id }
        : null,
    };
    const owned = (columns.get(dataset.id) ?? []).map((column) => ({
      id: column.id,
      kind: 'column' as const,
      name: column.display_name || `${dataset.schema_name}.${dataset.table_name}.${column.name}`,
      source: `${dataset.schema_name}.${dataset.table_name}`,
      open: { area: 'data' as const, page: 'TABLE', id: dataset.id },
      sourceLink: dataset.source_id
        ? { area: 'data' as const, page: 'SOURCE' as const, id: dataset.source_id }
        : { area: 'data' as const, page: 'TABLE' as const, id: dataset.id },
    }));
    return [table, ...owned];
  });
  const docs: CatalogAsset[] = documents.map((document) => ({
    id: document.id,
    kind: 'document',
    name: document.title || document.file_name,
    source: document.doc_no ?? document.file_name,
    open: { area: 'data', page: 'DOCUMENT', id: document.id },
    sourceLink: { area: 'data', page: 'COLLECTION', id: document.collection_id },
  }));
  const lines: CatalogAsset[] = series.map((item) => ({
    id: item.id,
    kind: 'series',
    name: item.label,
    source: 'Sổ dự báo',
    open: { area: 'analysis', page: 'FORECAST_LEDGER', id: item.id },
    sourceLink: { area: 'analysis', page: 'FORECAST' },
  }));
  const apiRows: CatalogAsset[] = apis.map((api) => ({
    id: api.id,
    kind: 'api',
    name: api.name,
    source: api.endpoint,
    open: { area: 'data', page: 'SOURCE', id: api.id },
    sourceLink: null,
  }));
  return [...tables, ...docs, ...lines, ...apiRows];
}

export const KIND_LABEL: Record<CatalogKind, string> = {
  table: 'Bảng',
  column: 'Cột',
  document: 'Tài liệu',
  series: 'Chuỗi',
  api: 'API',
};

/** Tables, their columns, documents, forecast series and REST APIs, as one list. */
export async function listCatalog(): Promise<Page<CatalogAsset>> {
  const [datasets, documents, series, sources] = await Promise.all([
    ok<Page<DatasetWire>>(api.GET('/v1/datasets', { params: { query: ALL } })).then((page) =>
      page.items.map(readDataset),
    ),
    ok<Page<DocumentRow>>(api.GET('/v1/documents', { params: { query: ALL } })).then(
      (page) => page.items,
    ),
    listForecastSeries().catch(() => ({ items: [], next_page_token: '' })),
    ok<Page<SourceRow>>(api.GET('/v1/sources', { params: { query: ALL } })).catch(() => ({
      items: [],
      next_page_token: '',
    })),
  ]);
  const columns = await columnsByDataset(datasets);
  return {
    items: catalogAssets(datasets, documents, series.items, restApis(sources.items), columns),
    next_page_token: '',
  };
}
