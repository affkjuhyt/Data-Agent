import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { Button } from '../../design-system/Button';
import { MatrixTable } from '../../design-system/tableView';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { ChartRenderer } from '../ask/components/ChartRenderer';
import {
  formatSql,
  getSqlCatalog,
  listSqlMetrics,
  loadSqlColumns,
  runSql,
  saveSqlToSession,
  type SqlColumn,
  type SqlResult,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import {
  chartFromGrid,
  columnKinds,
  mentionedMetric,
  sqlCompletionContext,
  sqlCompletionDefaults,
  sqlBrowseTree,
  sqlErrorLine,
  sqlSchemaTree,
  statementAround,
  type SqlBrowseTable,
} from '../../lib/sqlText';
import { SaveDatasetDialog } from './SaveDatasetDialog';
import { SaveToSessionDialog } from '../explore/SaveToSessionDialog';
import { SqlCatalog } from './SqlCatalog';
import { SqlEditor, type SqlEditorHandle } from './SqlEditor';

/*
 * "v3.1 · Dữ liệu · SQL" (2082:6880, UC-06-01 SQL branch, UC-06-02): an analyst workbench.
 * The catalog is a schema tree (react-arborist): a click selects and opens a branch.
 * It does not write into the statement. CodeMirror completes names as the analyst types.
 * Columns load when a table is opened.
 * ⌘↵ runs the selection, or the statement under the caret. The result is a table, a chart when the grid has a category
 * and a number, and the SQL the policy actually ran. A certified metric named in the text opens
 * that definition. Runs stay in this browser session because GET /v1/query-runs does not list them.
 */
const HISTORY_KEY = 'da.sql.history';
const NO_TABLES: SqlBrowseTable[] = [];

interface SqlHistoryEntry {
  id: string;
  sql: string;
  at: string;
  rowCount: number;
  durationMs: number;
}

const seconds = (ms: number) => `${(ms / 1000).toFixed(1).replace('.', ',')}`;

const quoteIdentifier = (value: string) => `"${value.replaceAll('"', '""')}"`;

const starterFor = (table: { name: string; columns: { name: string }[] }) => {
  const columns = table.columns
    .slice(0, 12)
    .map((column) => `  ${quoteIdentifier(column.name)}`)
    .join(',\n');
  return `SELECT\n${columns || '  *'}\nFROM ${table.name}\nLIMIT 100;`;
};

const readHistory = (): SqlHistoryEntry[] => {
  try {
    const raw = sessionStorage.getItem(HISTORY_KEY);
    const parsed = raw ? (JSON.parse(raw) as SqlHistoryEntry[]) : [];
    return Array.isArray(parsed) ? parsed.slice(0, 8) : [];
  } catch {
    return [];
  }
};

const remember = (entry: SqlHistoryEntry) => {
  const next = [entry, ...readHistory().filter((item) => item.id !== entry.id)].slice(0, 8);
  try {
    sessionStorage.setItem(HISTORY_KEY, JSON.stringify(next));
  } catch {
    /* a private window that refuses storage still shows the result */
  }
  return next;
};

type ResultTab = 'table' | 'chart' | 'sql';

export default function SqlPage() {
  const { navigate } = useAppState();
  const catalog = useV1(getSqlCatalog, []);
  const metrics = useV1(listSqlMetrics, []);
  const toast = useToast();
  const [sql, setSql] = useState('');
  const [columns, setColumns] = useState<Record<string, SqlColumn[]>>({});
  const [failedColumns, setFailedColumns] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<SqlResult>();
  const [tab, setTab] = useState<ResultTab>('table');
  const [category, setCategory] = useState<string | null>(null);
  const [measure, setMeasure] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const [selection, setSelection] = useState(false);
  const [saving, setSaving] = useState(false);
  const [session, setSession] = useState(false);
  const [history, setHistory] = useState<SqlHistoryEntry[]>(readHistory);
  const caret = useRef<{ start: number; end: number } | null>(null);
  const editorRef = useRef<SqlEditorHandle>(null);
  const columnsRef = useRef(columns);
  const inflight = useRef(new Set<string>());
  columnsRef.current = columns;

  useEffect(() => {
    const first = catalog.data
      ? sqlBrowseTree(catalog.data.tables)[0]?.schemas[0]?.tables[0]
      : undefined;
    if (!first) return;
    let live = true;
    void loadSqlColumns(first.id)
      .then((loaded) => {
        if (!live) return;
        setColumns((current) => (current[first.id] ? current : { ...current, [first.id]: loaded }));
        setSql((current) =>
          current ? current : starterFor({ name: first.qualified, columns: loaded }),
        );
      })
      .catch(() => {
        if (!live) return;
        setSql((current) =>
          current ? current : starterFor({ name: first.qualified, columns: [] }),
        );
      });
    return () => {
      live = false;
    };
  }, [catalog.data]);

  const ensureColumns = (id: string) => {
    if (columnsRef.current[id] || inflight.current.has(id)) return;
    inflight.current.add(id);
    setFailedColumns((current) => {
      if (!current.has(id)) return current;
      const next = new Set(current);
      next.delete(id);
      return next;
    });
    void loadSqlColumns(id)
      .then((loaded) => setColumns((current) => ({ ...current, [id]: loaded })))
      .catch(() => setFailedColumns((current) => new Set(current).add(id)))
      .finally(() => {
        inflight.current.delete(id);
      });
  };

  const textToRun = () => {
    const range = caret.current;
    if (range && range.end > range.start)
      return sql.slice(range.start, Math.min(range.end, sql.length));
    return statementAround(sql, range?.start ?? 0);
  };

  const run = async () => {
    if (running) return;
    const statement = textToRun();
    setRunning(true);
    setError(undefined);
    try {
      const res = await runSql(statement);
      setResult(res);
      setTab('table');
      const kinds = columnKinds(res.columns, res.rows);
      setCategory(kinds.text[0] ?? null);
      setMeasure(kinds.numeric[0] ?? null);
      setHistory(
        remember({
          id: res.id,
          sql: statement,
          at: new Date().toISOString(),
          rowCount: res.row_count,
          durationMs: res.duration_ms,
        }),
      );
      toast({
        tone: 'success',
        message: `Truy vấn xong · ${res.row_count} dòng · ${seconds(res.duration_ms)} giây`,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const format = async () => {
    caret.current = null;
    const before = sql;
    try {
      const { sql: formatted } = await formatSql(sql);
      setSql(formatted);
      toast({
        tone: 'success',
        message: 'Đã định dạng SQL',
        action: { label: 'Hoàn tác', onClick: () => setSql(before) },
      });
    } catch (e) {
      toast({ tone: 'error', message: `Không định dạng được: ${(e as Error).message}` });
    }
  };

  const tables = catalog.data?.tables ?? NO_TABLES;
  const lintTables = useMemo(() => tables.map((table) => ({ name: table.qualified })), [tables]);
  const schema = useMemo(
    () =>
      sqlSchemaTree(
        tables.map((table) => ({
          name: table.qualified,
          columns: columns[table.id] ?? [],
        })),
      ),
    [tables, columns],
  );
  const completion = sqlCompletionDefaults(schema, sqlCompletionContext(sql));
  const hint = mentionedMetric(sql, metrics.data ?? []);
  const kinds = result ? columnKinds(result.columns, result.rows) : { text: [], numeric: [] };
  const chart =
    result && category && measure
      ? chartFromGrid(result.columns, result.rows, category, measure)
      : null;

  return (
    <div className="flex h-full min-h-[32rem] flex-col lg:flex-row">
      <SqlCatalog
        tables={tables}
        columns={columns}
        failedIds={failedColumns}
        loading={catalog.loading}
        error={catalog.error?.message}
        onExpand={ensureColumns}
      />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-da-md px-da-xl py-da-lg">
        <div className="flex flex-wrap items-center gap-da-sm">
          <p className="text-foreground da-body-strong">
            Truy vấn chưa lưu{result ? ` · ${result.permission_scope}` : ''}
          </p>
          <span className="flex-1" />
          <Button kind="ghost" onClick={format}>
            Định dạng
          </Button>
          <Button onClick={() => setSaving(true)} disabled={!sql.trim()}>
            Lưu thành dataset
          </Button>
          <Button onClick={() => setSession(true)} disabled={!result}>
            Lưu vào phiên
          </Button>
          <Button kind="ghost" onClick={() => navigate('analysis', 'EXPLORE')} disabled={!result}>
            Mở trong Khám phá
          </Button>
          <Button kind="primary" icon="play" loading={running} onClick={() => void run()}>
            {selection ? 'Chạy vùng chọn' : 'Chạy ⌘↵'}
          </Button>
        </div>
        {hint && (
          <Notice tone="info">
            Câu này nhắc chỉ số đã chứng nhận {hint.code}
            {hint.version != null ? ` v${hint.version}` : ''}.{' '}
            <button
              type="button"
              className="underline"
              onClick={() => navigate('data', 'METRIC', hint.id)}
            >
              Mở {hint.code}
            </button>
          </Notice>
        )}
        <SqlEditor
          ref={editorRef}
          value={sql}
          onChange={setSql}
          onRun={() => void run()}
          errorLine={error ? sqlErrorLine(error) : null}
          onSelect={setSelection}
          onCaret={(start, end) => {
            caret.current = { start, end };
          }}
          schema={schema}
          defaultSchema={completion.defaultSchema}
          defaultTable={completion.defaultTable}
          tables={lintTables}
        />
        {error && <Notice tone="error">{error}</Notice>}
        {result && (
          <div className="flex min-h-0 flex-1 flex-col gap-da-md overflow-y-auto">
            <div className="flex flex-wrap items-center gap-da-md">
              <StatusDot tone="success">
                {result.row_count} dòng · {seconds(result.duration_ms)} s
              </StatusDot>
              <p className="text-muted-foreground da-caption">
                Quyền: {result.permission_scope}
                {result.masked_columns.length > 0 &&
                  ` · cột ${result.masked_columns.join(', ')} đã che`}
              </p>
              <span className="flex-1" />
              <Segmented<ResultTab>
                variant="outline"
                label="Cách xem kết quả"
                value={tab}
                onChange={setTab}
                items={[
                  { value: 'table', label: 'Bảng' },
                  { value: 'chart', label: 'Biểu đồ' },
                  { value: 'sql', label: 'SQL đã chạy' },
                ]}
              />
            </div>
            {tab === 'table' && (
              <MatrixTable
                columns={result.columns}
                rows={result.rows}
                className="border-collapse da-mono"
                searchLabel="Tìm trong kết quả"
              />
            )}
            {tab === 'chart' &&
              (chart ? (
                <div className="flex flex-col gap-da-md">
                  <div className="flex flex-wrap gap-da-md">
                    <Select
                      size="sm"
                      label="Cột nhóm"
                      value={category}
                      options={kinds.text.map((name) => ({ value: name, label: name }))}
                      onChange={setCategory}
                    />
                    <Select
                      size="sm"
                      label="Cột số"
                      value={measure}
                      options={kinds.numeric.map((name) => ({ value: name, label: name }))}
                      onChange={setMeasure}
                    />
                  </div>
                  <ChartRenderer spec={chart} />
                </div>
              ) : (
                <Notice tone="info">Biểu đồ cần một cột chữ và một cột số.</Notice>
              ))}
            {tab === 'sql' && (
              <pre className="overflow-auto rounded-md border border-border bg-subtle p-da-lg text-foreground da-mono">
                {result.sql_executed ?? 'Không đọc được SQL đã chạy sau chính sách.'}
              </pre>
            )}
          </div>
        )}
      </div>
      <aside
        aria-label="Câu đã chạy"
        className="hidden w-[220px] shrink-0 flex-col gap-da-sm overflow-y-auto border-l border-border p-da-lg lg:flex"
      >
        <p className="flex items-center gap-da-sm text-muted-foreground da-label">
          <Icon name="clock" />
          Trong phiên này
        </p>
        {history.length === 0 && (
          <p className="text-muted-foreground da-caption">Chưa có câu nào.</p>
        )}
        {history.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              caret.current = null;
              setSql(item.sql);
            }}
            className="rounded-sm px-da-sm py-da-xs text-left hover:bg-subtle"
          >
            <span className="block truncate text-foreground da-caption">
              {item.sql.replace(/\s+/g, ' ').slice(0, 72)}
            </span>
            <span className="text-muted-foreground da-caption">
              {item.rowCount} dòng · {seconds(item.durationMs)} s
            </span>
          </button>
        ))}
      </aside>
      <SaveDatasetDialog
        open={saving}
        sql={sql}
        suggestedName="loans_npl_by_region"
        onClose={() => setSaving(false)}
        onSaved={(table) => {
          setSaving(false);
          toast({ tone: 'success', message: `Đã tạo bảng ${table.name}` });
        }}
      />
      {session && result && (
        <SaveToSessionDialog
          onClose={() => setSession(false)}
          onSave={(workspaceId) =>
            saveSqlToSession(
              workspaceId,
              result.id,
              sql.trim().split('\n')[0]?.slice(0, 80) || 'Truy vấn SQL',
            )
          }
          onSaved={(workspaceId, title) => {
            setSession(false);
            toast({
              tone: 'success',
              message: `Đã lưu vào ${title}`,
              action: {
                label: 'Mở phiên',
                onClick: () => navigate('analysis', 'SESSIONS', workspaceId),
              },
            });
          }}
        />
      )}
    </div>
  );
}
