import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatNumber, formatPercent } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  deriveTable,
  getColumnStats,
  previewTable,
  type CellValue,
  type ExploreFilter,
  type FilterOp,
  type TablePreview,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../sessions/FormDialog';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "v3.1 · Dữ liệu · Khám phá bảng" (2082:6566, UC-04-02, UC-06-02): rows of one table under
 * filters, personal data masked, duplicates marked by the Data Quality agent; on the right the
 * statistics of the column picked in the header. "Tạo bảng dẫn xuất" saves the filtered rows as a
 * derived table. "Mở trong SQL" opens the SQL screen (P06); "Thao tác cột" of Figma is not drawn
 * because none of its actions has a screen. /data/explore/<dataset id>.
 */
const OPS: { value: FilterOp; label: string; sign: string }[] = [
  { value: 'eq', label: 'bằng', sign: '=' },
  { value: 'gte', label: 'lớn hơn hoặc bằng', sign: '≥' },
  { value: 'lte', label: 'nhỏ hơn hoặc bằng', sign: '≤' },
];

const filterText = (f: ExploreFilter) =>
  `${f.column} ${OPS.find((o) => o.value === f.op)?.sign ?? '='} ${f.value}`;

export default function TableExplorePage() {
  const { routeId } = useAppState();
  if (!routeId)
    return (
      <div className="flex flex-col">
        <PageHeader bordered breadcrumb={['Dữ liệu']} title="Khám phá dữ liệu" />
        <EmptyState
          className="m-da-3xl"
          icon="table"
          title="Chọn một bảng để khám phá"
          description="Mở một bảng từ Nguồn rồi chọn Dùng trong Khám phá."
        />
      </div>
    );
  return <Explorer key={routeId} id={routeId} />;
}

function Explorer({ id }: { id: string }) {
  const [filters, setFilters] = useState<ExploreFilter[]>([]);
  const preview = useV1(() => previewTable(id, filters), [id, filters]);
  // A preview in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(preview.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={preview} what="dữ liệu của bảng" skeletonRows={8}>
      {(data) => <ExploreView id={id} preview={data} filters={filters} onFilters={setFilters} />}
    </AsyncRegion>
  );
}

function ExploreView({
  id,
  preview,
  filters,
  onFilters,
}: {
  id: string;
  preview: TablePreview;
  filters: ExploreFilter[];
  onFilters: (filters: ExploreFilter[]) => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [deriving, setDeriving] = useState<'filtered' | 'dedup' | null>(null);
  const [column, setColumn] = useState(
    () => preview.columns.find((c) => c.numeric && !c.masked)?.name ?? preview.columns[0]?.name,
  );
  const duplicates = new Set(preview.duplicate_rows);

  const derive = async (dropDuplicates: boolean) => {
    setDeriving(dropDuplicates ? 'dedup' : 'filtered');
    try {
      const table = await deriveTable(id, { filters, drop_duplicates: dropDuplicates });
      toast({
        tone: 'success',
        message: `Đã tạo bảng dẫn xuất ${table.name} · ${formatNumber(table.row_count)} dòng.`,
      });
    } catch (e) {
      toast({ tone: 'error', message: `Không tạo được bảng dẫn xuất. ${(e as Error).message}` });
    } finally {
      setDeriving(null);
    }
  };

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl py-da-lg">
        <PageHeader
          breadcrumb={['Khám phá dữ liệu', preview.source_name, preview.name]}
          title={`${preview.name} · ${formatNumber(preview.row_count)} dòng`}
          actions={
            <>
              <Button kind="ghost" icon="agent" onClick={() => navigate('analysis', 'ASK')}>
                Hỏi agent về bảng này
              </Button>
              <Button onClick={() => navigate('data', 'SQL')}>Mở trong SQL</Button>
              <Button
                kind="primary"
                loading={deriving === 'filtered'}
                disabled={deriving !== null}
                onClick={() => void derive(false)}
              >
                Tạo bảng dẫn xuất
              </Button>
            </>
          }
        />
        <div className="flex flex-wrap items-center gap-da-sm">
          <ul aria-label="Bộ lọc đang dùng" className="flex flex-wrap items-center gap-da-sm">
            {filters.map((f, i) => (
              <li key={`${f.column}-${i}`}>
                <button
                  type="button"
                  aria-label={`Bỏ lọc ${filterText(f)}`}
                  onClick={() => onFilters(filters.filter((_, j) => j !== i))}
                  className="inline-flex items-center gap-da-sm rounded-full px-da-xs text-foreground da-body-small hover:bg-subtle"
                >
                  <span aria-hidden className="size-[7px] rounded-full bg-primary" />
                  {filterText(f)}
                  <Icon name="x" className="text-muted-foreground" />
                </button>
              </li>
            ))}
          </ul>
          <button
            type="button"
            onClick={() => setAdding(true)}
            className="rounded-full border border-border px-da-md py-da-xs text-muted-foreground da-mono hover:bg-subtle"
          >
            + Thêm lọc
          </button>
          <span className="flex-1" />
          <span className="text-muted-foreground da-caption">
            {formatNumber(preview.matched_rows)} dòng khớp · {preview.snapshot_text}
          </span>
        </div>
        {preview.rows.length === 0 ? (
          <EmptyState
            icon="filter"
            title="Không có dòng nào khớp bộ lọc"
            description="Bỏ bớt một bộ lọc để xem lại dữ liệu."
          />
        ) : (
          <div className="overflow-x-auto rounded-md border border-border bg-card">
            <table className="min-w-full border-collapse">
              <thead>
                <tr className="bg-subtle text-left">
                  {preview.columns.map((c) => (
                    <th
                      key={c.name}
                      scope="col"
                      className="border-r border-border p-0 font-normal last:border-r-0"
                    >
                      <button
                        type="button"
                        aria-pressed={column === c.name}
                        onClick={() => setColumn(c.name)}
                        className={cn(
                          'flex w-full min-w-[90px] flex-col items-start px-da-md py-da-sm text-left',
                          column === c.name ? 'bg-accent' : 'hover:bg-background',
                        )}
                      >
                        <span className="text-foreground da-mono">{c.name}</span>
                        <span className="text-muted-foreground da-caption">{c.type_label}</span>
                      </button>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.map((row, i) => (
                  <tr
                    key={i}
                    className={cn('border-t border-border', duplicates.has(i) && 'bg-error-subtle')}
                    aria-label={duplicates.has(i) ? `Dòng ${i + 1}, trùng` : undefined}
                  >
                    {row.map((cell, j) => (
                      <td
                        key={j}
                        className={cn(
                          'whitespace-nowrap border-r border-border px-da-md py-da-xs da-mono last:border-r-0',
                          cell === null ? 'text-muted-foreground' : 'text-foreground',
                        )}
                      >
                        {cellText(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {preview.quality && (
          <Notice
            action={
              <Button
                loading={deriving === 'dedup'}
                disabled={deriving !== null}
                onClick={() => void derive(true)}
              >
                Loại trùng vào bảng dẫn xuất
              </Button>
            }
          >
            {preview.quality.agent_name}: {preview.quality.text}
          </Notice>
        )}
      </div>
      <aside
        aria-label={column ? `Cột ${column}` : 'Cột'}
        className="flex w-[340px] shrink-0 flex-col gap-da-md border-l border-border bg-card px-da-xl py-da-lg max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t"
      >
        {column && <ColumnPanel key={column} id={id} column={column} />}
      </aside>
      <AddFilterDialog
        open={adding}
        columns={preview.columns.filter((c) => !c.masked).map((c) => c.name)}
        onClose={() => setAdding(false)}
        onAdd={(f) => {
          onFilters([...filters, f]);
          setAdding(false);
        }}
      />
    </div>
  );
}

function cellText(cell: CellValue): string {
  if (cell === null) return 'NULL';
  return typeof cell === 'number' ? formatNumber(cell) : cell;
}

function ColumnPanel({ id, column }: { id: string; column: string }) {
  const stats = useV1(() => getColumnStats(id, column), [id, column]);
  return (
    <AsyncRegion state={stats} what={`thống kê cột ${column}`} skeletonRows={5}>
      {(s) => (
        <>
          <div className="flex items-center gap-da-md">
            <h2 className="min-w-0 flex-1 truncate text-foreground da-mono">{s.name}</h2>
            <Badge>{s.type_label}</Badge>
          </div>
          <dl className="flex flex-col gap-da-md">
            {[
              ['Rỗng', formatPercent(s.null_ratio)],
              ['Khác nhau', formatNumber(s.distinct)],
              ['Nhỏ nhất', s.min],
              ['Lớn nhất', s.max],
              ['Trung vị', s.median],
            ].map(([label, value]) => (
              <div key={label} className="flex items-start gap-da-md">
                <dt className="w-[90px] shrink-0 text-muted-foreground da-caption">{label}</dt>
                <dd className="min-w-0 flex-1 text-foreground da-body-small">{value}</dd>
              </div>
            ))}
          </dl>
          {s.histogram.length > 0 && (
            <>
              <span
                className="flex h-[70px] items-end gap-da-xs"
                role="img"
                aria-label={`Phân bố của ${s.name}`}
              >
                {s.histogram.map((bar, i) => (
                  <span
                    key={i}
                    className={cn('w-6 rounded-sm', bar.flagged ? 'bg-chart-3' : 'bg-chart-2')}
                    style={{ height: Math.max(2, Math.round(bar.height * 70)) }}
                  />
                ))}
              </span>
              <p className="text-muted-foreground da-caption">{s.buckets_text}</p>
            </>
          )}
        </>
      )}
    </AsyncRegion>
  );
}

function AddFilterDialog({
  open,
  columns,
  onClose,
  onAdd,
}: {
  open: boolean;
  columns: string[];
  onClose: () => void;
  onAdd: (filter: ExploreFilter) => void;
}) {
  const [column, setColumn] = useState<string | null>(null);
  const [op, setOp] = useState<FilterOp>('eq');
  const [value, setValue] = useState('');
  const valid = Boolean(column && value.trim());
  const add = () => {
    if (!column || !value.trim()) return;
    onAdd({ column, op, value: value.trim() });
    setColumn(null);
    setOp('eq');
    setValue('');
  };
  return (
    <FormDialog
      open={open}
      title="Thêm lọc"
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" disabled={!valid} onClick={add}>
            Thêm lọc
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-da-lg">
        <Field label="Cột">
          <Select
            label="Cột"
            value={column}
            onChange={setColumn}
            options={columns.map((c) => ({ value: c, label: c }))}
          />
        </Field>
        <Field label="Điều kiện">
          <Select<FilterOp> label="Điều kiện" value={op} onChange={setOp} options={OPS} />
        </Field>
        <Field label="Giá trị">
          <Input className="w-full" value={value} onChange={(e) => setValue(e.target.value)} />
        </Field>
      </div>
    </FormDialog>
  );
}
