import { useEffect, useMemo, useRef, useState } from 'react';
import { Tree, type NodeRendererProps, type TreeApi } from 'react-arborist';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { sqlBrowseTree, type SqlBrowseTable } from '../../lib/sqlText';
import type { SqlColumn } from '../../lib/v1/dataPrep';

/*
 * Schema browser. react-arborist owns selection, expand, and keyboard; CodeMirror owns
 * the statement. A click never writes a name into the editor — completion does that
 * while the analyst types. Columns load the first time a table is opened.
 */

interface CatalogNode {
  id: string;
  name: string;
  kind: 'source' | 'schema' | 'table' | 'column' | 'note';
  qualified?: string;
  type?: string;
  count?: number;
  children?: CatalogNode[];
}

interface SqlCatalogProps {
  tables: SqlBrowseTable[];
  columns: Record<string, SqlColumn[]>;
  failedIds: ReadonlySet<string>;
  loading: boolean;
  error?: string;
  onExpand: (id: string) => void;
}

const tableChildren = (
  table: SqlBrowseTable,
  columns: Record<string, SqlColumn[]>,
  failedIds: ReadonlySet<string>,
): CatalogNode[] => {
  const loaded = columns[table.id];
  if (!loaded) {
    return [
      {
        id: `note:${table.id}`,
        name: failedIds.has(table.id) ? 'Không tải được cột.' : 'Đang tải cột…',
        kind: 'note',
      },
    ];
  }
  if (loaded.length === 0) {
    return [{ id: `note:${table.id}`, name: 'Chưa có cột.', kind: 'note' }];
  }
  return loaded.map((column) => ({
    id: `column:${table.id}:${column.name}`,
    name: column.name,
    kind: 'column' as const,
    type: column.type,
  }));
};

const catalogNodes = (
  tables: SqlBrowseTable[],
  columns: Record<string, SqlColumn[]>,
  failedIds: ReadonlySet<string>,
): { nodes: CatalogNode[]; initiallyOpen: Record<string, boolean> } => {
  const browse = sqlBrowseTree(tables);
  const initiallyOpen: Record<string, boolean> = {};
  const sources: CatalogNode[] = browse.map((source) => {
    const sourceId = `source:${source.id}`;
    initiallyOpen[sourceId] = true;
    return {
      id: sourceId,
      name: source.name,
      kind: 'source',
      children: source.schemas.map((schema) => {
        const schemaId = `schema:${source.id}/${schema.name}`;
        if (source.schemas.length === 1) initiallyOpen[schemaId] = true;
        return {
          id: schemaId,
          name: schema.name,
          kind: 'schema' as const,
          count: schema.tables.length,
          children: schema.tables.map((table) => ({
            id: `table:${table.id}`,
            name: table.name,
            kind: 'table' as const,
            qualified: table.qualified,
            children: tableChildren(table, columns, failedIds),
          })),
        };
      }),
    };
  });
  const hideSource = sources.length <= 1;
  return {
    nodes: hideSource ? (sources[0]?.children ?? []) : sources,
    initiallyOpen,
  };
};

function CatalogRow({ node, style }: NodeRendererProps<CatalogNode>) {
  const data = node.data;
  return (
    <div
      style={style}
      title={data.qualified ?? undefined}
      className={`flex h-full min-w-0 items-center gap-da-xs pr-da-xs ${
        node.isSelected ? 'bg-subtle' : ''
      }`}
    >
      {!node.isLeaf && (
        <span aria-hidden className="text-muted-foreground">
          <Icon name={node.isOpen ? 'chevron-down' : 'chevron-right'} size={14} />
        </span>
      )}
      {data.kind === 'source' && <Icon name="database" size={14} />}
      {data.kind === 'schema' && <Icon name="folder" size={14} />}
      {data.kind === 'table' && <Icon name="sheet" size={14} />}
      <span
        className={`min-w-0 flex-1 truncate ${
          data.kind === 'note'
            ? 'text-muted-foreground da-caption'
            : data.kind === 'source'
              ? 'text-muted-foreground da-label'
              : data.kind === 'schema'
                ? 'text-foreground da-body-small'
                : 'text-foreground da-mono'
        }`}
      >
        {data.name}
      </span>
      {data.kind === 'schema' && (
        <span aria-hidden className="shrink-0 text-muted-foreground da-caption">
          {data.count}
        </span>
      )}
      {data.kind === 'column' && data.type && (
        <span aria-hidden className="shrink-0 truncate text-muted-foreground da-caption">
          {data.type}
        </span>
      )}
    </div>
  );
}

export function SqlCatalog({
  tables,
  columns,
  failedIds,
  loading,
  error,
  onExpand,
}: SqlCatalogProps) {
  const [filter, setFilter] = useState('');
  const host = useRef<HTMLDivElement>(null);
  const tree = useRef<TreeApi<CatalogNode>>(null);
  const [size, setSize] = useState({ width: 260, height: 480 });
  const model = useMemo(
    () => catalogNodes(tables, columns, failedIds),
    [tables, columns, failedIds],
  );
  const term = filter.trim();

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect || rect.width < 1 || rect.height < 1) return;
      setSize({ width: rect.width, height: rect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [tables.length]);

  return (
    <aside
      aria-label="Bảng có thể truy vấn"
      className="flex max-h-56 min-w-0 shrink-0 flex-col gap-da-xs overflow-hidden border-b border-border p-da-lg lg:max-h-none lg:w-[280px] lg:border-b-0 lg:border-r"
    >
      <Input
        icon="search"
        aria-label="Tìm schema, bảng, cột"
        placeholder="Tìm schema, bảng, cột"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        className="w-full"
      />
      <p className="text-muted-foreground da-caption">Gõ trong câu để gợi ý tên bảng và cột.</p>
      {loading && tables.length === 0 && <SkeletonRows count={6} label="Đang tải bảng…" />}
      {error && <Notice tone="error">Không tải được danh sách bảng. {error}</Notice>}
      {!loading && !error && tables.length === 0 && (
        <p className="text-muted-foreground da-caption">Không có bảng nào.</p>
      )}
      <div ref={host} className="min-h-0 flex-1">
        {tables.length > 0 && (
          <Tree<CatalogNode>
            ref={tree}
            data={model.nodes}
            initialOpenState={model.initiallyOpen}
            width={size.width}
            height={size.height}
            rowHeight={28}
            indent={16}
            openByDefault={false}
            disableDrag
            disableDrop
            disableEdit
            disableMultiSelection
            searchTerm={term}
            searchMatch={(node, query) => {
              const q = query.toLowerCase();
              const data = node.data;
              return (
                data.name.toLowerCase().includes(q) ||
                (data.qualified ?? '').toLowerCase().includes(q) ||
                (data.type ?? '').toLowerCase().includes(q)
              );
            }}
            onActivate={(node) => {
              if (node.isLeaf) return;
              node.toggle();
            }}
            onToggle={(id) => {
              if (!id.startsWith('table:')) return;
              if (tree.current?.isOpen(id)) onExpand(id.slice('table:'.length));
            }}
          >
            {CatalogRow}
          </Tree>
        )}
      </div>
    </aside>
  );
}
