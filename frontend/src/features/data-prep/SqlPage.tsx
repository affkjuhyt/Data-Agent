import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatSql, getSqlCatalog, runSql, type SqlResult } from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { SaveDatasetDialog } from './SaveDatasetDialog';

/*
 * "v3.1 · Dữ liệu · SQL" (2082:6880, UC-06-01 SQL branch, UC-06-02): a schema browser of the
 * tables the caller may read, a read-only editor (⌘↵ runs), the agent's hint about the query,
 * and the result grid with the permissions that applied. "Lưu thành dataset" turns the query
 * into a derived table. Runs and formatting go through the server so the editor, pipelines and
 * the permission check agree on one SQL dialect.
 */
const seconds = (ms: number) => `${(ms / 1000).toFixed(1).replace('.', ',')}`;

const quoteIdentifier = (value: string) => `"${value.replaceAll('"', '""')}"`;

const starterFor = (table: { name: string; columns: { name: string }[] }) => {
  const columns = table.columns
    .slice(0, 12)
    .map((column) => `  ${quoteIdentifier(column.name)}`)
    .join(',\n');
  return `SELECT\n${columns || '  *'}\nFROM ${table.name}\nLIMIT 100;`;
};

export default function SqlPage() {
  const catalog = useV1(getSqlCatalog, []);
  const toast = useToast();
  const [sql, setSql] = useState('');
  const [filter, setFilter] = useState('');
  const [result, setResult] = useState<SqlResult>();
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string>();
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<HTMLTextAreaElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (sql || !catalog.data) return;
    const firstTable = catalog.data.items[0]?.tables[0];
    if (firstTable) setSql(starterFor(firstTable));
  }, [catalog.data, sql]);

  const run = async () => {
    if (running) return;
    setRunning(true);
    setError(undefined);
    try {
      const res = await runSql(sql);
      setResult(res);
      toast({
        tone: 'success',
        message: `Truy vấn xong · ${res.row_count} dòng · ${seconds(res.duration_ms)} giây`,
        action: {
          label: 'Xem',
          onClick: () => resultRef.current?.scrollIntoView({ behavior: 'smooth' }),
        },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  };

  const format = async () => {
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

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
      event.preventDefault();
      void run();
    }
  };

  const insertTable = (table: string) => {
    const el = editorRef.current;
    if (!el) return;
    if (document.activeElement === el) {
      const at = el.selectionStart;
      setSql(sql.slice(0, at) + table + sql.slice(el.selectionEnd));
      el.focus();
      requestAnimationFrame(() => {
        const next = at + table.length;
        el.setSelectionRange(next, next);
      });
      return;
    }

    // Clicking the table browser moves focus to the sidebar button. In that case the browser's
    // selectionStart falls back to zero, which used to produce `tableSELECT ...` and a misleading
    // 422 from the read-only SQL validator. Replace the first FROM target as the useful fallback.
    const fromTarget = /(\bfrom\s+)([a-zA-Z_][\w$]*(?:\.[a-zA-Z_][\w$]*)?)/i;
    if (fromTarget.test(sql)) {
      setSql(sql.replace(fromTarget, `$1${table}`));
    } else {
      setSql(`${sql.trimEnd()}\n${table}`);
    }
    el.focus();
  };

  const q = filter.trim().toLowerCase();
  const groups = (catalog.data?.items ?? [])
    .map((g) => ({
      ...g,
      tables: q
        ? g.tables.filter(
            (t) =>
              t.name.toLowerCase().includes(q) ||
              g.name.toLowerCase().includes(q) ||
              t.columns.some((column) => column.name.toLowerCase().includes(q)),
          )
        : g.tables,
    }))
    .filter((g) => g.tables.length > 0);
  const lineCount = sql.split('\n').length;

  return (
    <div className="flex min-h-full">
      <aside
        aria-label="Bảng có thể truy vấn"
        className="flex w-[240px] shrink-0 flex-col gap-da-xs border-r border-border p-da-lg"
      >
        <Input
          icon="search"
          aria-label="Tìm bảng, cột"
          placeholder="Tìm bảng, cột"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="mb-da-xs w-full"
        />
        {catalog.loading && !catalog.data && <SkeletonRows count={6} label="Đang tải bảng…" />}
        {catalog.error && (
          <Notice tone="error">Không tải được danh sách bảng. {catalog.error.message}</Notice>
        )}
        {groups.map((g) => (
          <div key={g.id} className="flex flex-col gap-da-xs">
            <p className="flex items-center gap-da-sm text-muted-foreground da-label">
              <Icon name="database" />
              {g.name}
            </p>
            {g.tables.map((t) => (
              <div key={t.id} className="flex flex-col gap-da-2xs pl-da-xl">
                <button
                  type="button"
                  title="Chèn tên bảng vào câu truy vấn"
                  onClick={() => insertTable(t.name)}
                  className="rounded-sm text-left text-foreground da-mono hover:bg-subtle"
                >
                  {t.name}
                </button>
                {t.columns.map((column) => (
                  <button
                    key={`${t.id}-${column.name}`}
                    type="button"
                    title={`Chèn cột ${column.name}`}
                    onClick={() => insertTable(column.name)}
                    className="truncate rounded-sm pl-da-md text-left text-muted-foreground da-caption hover:bg-subtle"
                  >
                    {column.name}
                    {column.type ? ` · ${column.type}` : ''}
                  </button>
                ))}
              </div>
            ))}
          </div>
        ))}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl py-da-lg">
        <div className="flex items-center gap-da-sm">
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
          <Button kind="primary" icon="play" loading={running} onClick={run}>
            Chạy ⌘↵
          </Button>
        </div>
        <div className="flex overflow-hidden rounded-md border border-border bg-card">
          <div
            aria-hidden
            className="flex flex-col bg-subtle p-da-md text-right text-muted-foreground da-mono"
          >
            {Array.from({ length: lineCount }, (_, i) => (
              <span key={i}>{i + 1}</span>
            ))}
          </div>
          <textarea
            ref={editorRef}
            aria-label="Câu truy vấn SQL"
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            onKeyDown={onKeyDown}
            spellCheck={false}
            rows={Math.max(lineCount, 7)}
            className="min-w-0 flex-1 resize-none whitespace-pre bg-transparent px-da-lg py-da-md text-foreground outline-none da-mono"
          />
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {result && (
          <div ref={resultRef} className="flex flex-col gap-da-md">
            <div className="flex items-center gap-da-md">
              <StatusDot tone="success">
                {result.row_count} dòng · {seconds(result.duration_ms)} s
              </StatusDot>
              <p className="text-muted-foreground da-caption">
                Quyền: {result.permission_scope}
                {result.masked_columns.length > 0 &&
                  ` · cột ${result.masked_columns.join(', ')} đã che`}
              </p>
            </div>
            <div className="overflow-x-auto rounded-md border border-border bg-card">
              <table className="border-collapse da-mono">
                <thead>
                  <tr className="bg-subtle text-left text-foreground">
                    {result.columns.map((c) => (
                      <th
                        key={c}
                        className="min-w-[140px] border-r border-border px-da-md py-da-sm font-normal"
                      >
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.map((row, i) => (
                    <tr key={i} className="border-t border-border text-foreground">
                      {row.map((cell, j) => (
                        <td key={j} className="border-r border-border px-da-md py-da-xs">
                          {cell ?? '—'}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
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
    </div>
  );
}
