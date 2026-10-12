import { useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { RunStep } from '../../design-system/RunStep';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { formatNumber, formatPercent, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getDocumentCounts,
  getDocumentView,
  listLibrary,
  type DocumentRow,
  type DocumentView,
  type LibraryQuery,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { HEAD_ROW, KeyValue, ROW } from './parts';
import { UploadDocumentsDialog } from './UploadDocumentsDialog';

/*
 * "v3.1 · Phi cấu trúc · Tệp & tài liệu" (2065:2161) with its tabs Quy định (2102:13656), Nội bộ
 * (2102:13857) and Đang xử lý (2102:14058): every document across collections, its validity,
 * clauses and links. A row opens what the Document agent did on the right (400px); "Mở tài liệu"
 * goes to the viewer. "Nạp tài liệu" opens "Hộp thoại · upload".
 */
type Tab = 'all' | 'regulation' | 'internal' | 'processing';

const QUERY: Record<Tab, LibraryQuery> = {
  all: {},
  regulation: { category: 'regulation' },
  internal: { category: 'internal' },
  processing: { state: 'processing' },
};

export default function DocumentsPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('all');
  const [selected, setSelected] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const counts = useV1(getDocumentCounts, []);
  const rows = useV1(() => listLibrary(QUERY[tab]), [tab]);
  // The panel opens on the first document until the reader picks one, as the frame draws it.
  const shown = selected ?? rows.data?.items[0]?.id ?? null;

  const label = (text: string, n: number | undefined) =>
    n === undefined ? text : `${text} · ${formatNumber(n)}`;

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <PageHeader
          breadcrumb={['Dữ liệu & tri thức › Tài liệu']}
          title="Tài liệu"
          description="Quy định, chính sách nội bộ, hợp đồng… Agent Tài liệu đọc, trích điều khoản, theo dõi hiệu lực và liên kết tới chỉ số liên quan."
          actions={
            <Button kind="primary" onClick={() => setUploading(true)}>
              Nạp tài liệu
            </Button>
          }
        />
        <Tabs<Tab>
          label="Lọc tài liệu"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'all', label: label('Tất cả', counts.data?.all) },
            { value: 'regulation', label: label('Quy định', counts.data?.regulation) },
            { value: 'internal', label: label('Nội bộ', counts.data?.internal) },
            { value: 'processing', label: label('Đang xử lý', counts.data?.processing) },
          ]}
        />
        <div role="tabpanel">
          <AsyncRegion
            state={rows}
            what="tài liệu"
            skeletonRows={5}
            isEmpty={(p) => p.items.length === 0}
            empty={
              <EmptyState
                icon="file"
                title={tab === 'processing' ? 'Không có tài liệu đang xử lý' : 'Chưa có tài liệu'}
                description="Tài liệu xuất hiện khi nguồn đồng bộ hoặc khi bạn nạp tệp."
              />
            }
          >
            {(p) =>
              tab === 'processing' ? (
                <ProcessingTable rows={p.items} selected={shown} onSelect={setSelected} />
              ) : (
                <LibraryTable rows={p.items} selected={shown} onSelect={setSelected} />
              )
            }
          </AsyncRegion>
        </div>
      </div>
      {shown && <DocumentPanel key={shown} id={shown} />}
      <UploadDocumentsDialog
        open={uploading}
        onClose={() => setUploading(false)}
        onUploaded={({ accepted, skipped }) => {
          setUploading(false);
          toast({
            tone: 'success',
            message: `Đã nhận ${formatNumber(accepted)} tệp${skipped ? `, bỏ qua ${formatNumber(skipped)} tệp trùng` : ''}. Agent Tài liệu đang tiền xử lý.`,
          });
          counts.reload();
          rows.reload();
        }}
      />
    </div>
  );
}

/* The library table: 12px side padding, 8px between cells; the processing table: 8px and 12px. */
const LIB_CELL = 'px-da-xs first:pl-da-lg last:pr-da-lg';
const RUN_CELL = 'px-[6px] py-da-sm first:pl-da-md last:pr-da-md';

function SelectableRow({
  row,
  selected,
  onSelect,
  children,
}: {
  row: DocumentRow;
  selected: boolean;
  onSelect: (id: string) => void;
  children: ReactNode;
}) {
  return (
    <tr
      tabIndex={0}
      aria-selected={selected}
      onClick={() => onSelect(row.id)}
      onKeyDown={(e) => e.key === 'Enter' && onSelect(row.id)}
      className={cn(
        ROW,
        'cursor-pointer hover:bg-subtle focus-visible:bg-subtle',
        selected && 'bg-subtle',
      )}
    >
      {children}
    </tr>
  );
}

function LibraryTable({
  rows,
  selected,
  onSelect,
}: {
  rows: DocumentRow[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className={HEAD_ROW}>
            <th className={cn(LIB_CELL, 'py-da-sm font-medium')}>Tài liệu</th>
            <th className={cn(LIB_CELL, 'w-[108px] py-da-sm font-medium')}>Loại</th>
            <th className={cn(LIB_CELL, 'w-[128px] py-da-sm font-medium')}>Hiệu lực</th>
            <th className={cn(LIB_CELL, 'w-[88px] py-da-sm font-medium max-[1279px]:hidden')}>
              Điều khoản
            </th>
            <th className={cn(LIB_CELL, 'w-[166px] py-da-sm font-medium max-[1279px]:hidden')}>
              Liên kết
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <SelectableRow
              key={row.id}
              row={row}
              selected={selected === row.id}
              onSelect={onSelect}
            >
              <td className={cn(LIB_CELL, 'truncate py-da-md text-foreground da-body-strong')}>
                {row.title}
              </td>
              <td className={cn(LIB_CELL, 'py-da-md text-foreground da-body-small')}>
                {row.category_label}
              </td>
              <td className={cn(LIB_CELL, 'py-da-md')}>
                {!row.validity ? (
                  <span className="text-muted-foreground da-body-small">—</span>
                ) : row.validity.ended ? (
                  <Badge>{row.validity.text}</Badge>
                ) : (
                  <StatusDot tone={row.validity.tone} className="max-w-full truncate">
                    {row.validity.text}
                  </StatusDot>
                )}
              </td>
              <td className={cn(LIB_CELL, 'py-da-md text-foreground da-mono max-[1279px]:hidden')}>
                {row.clause_count ?? '—'}
              </td>
              <td
                className={cn(
                  LIB_CELL,
                  'truncate py-da-md text-foreground da-body-small max-[1279px]:hidden',
                )}
              >
                {row.links_text ?? '—'}
              </td>
            </SelectableRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ProcessingTable({
  rows,
  selected,
  onSelect,
}: {
  rows: DocumentRow[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className={HEAD_ROW}>
            <th className={cn(RUN_CELL, 'font-medium')}>Tài liệu</th>
            <th className={cn(RUN_CELL, 'w-[212px] font-medium')}>Bước hiện tại</th>
            <th className={cn(RUN_CELL, 'w-[132px] font-medium')}>Tiến độ</th>
            <th className={cn(RUN_CELL, 'w-[104px] font-medium')}>Bắt đầu</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <SelectableRow
              key={row.id}
              row={row}
              selected={selected === row.id}
              onSelect={onSelect}
            >
              <td className={cn(RUN_CELL, 'truncate text-foreground da-body-strong')}>
                {row.title}
              </td>
              <td className={cn(RUN_CELL, 'text-foreground da-body-small')}>
                {row.processing?.step_text ?? '—'}
              </td>
              <td className={cn(RUN_CELL, 'text-foreground da-mono')}>
                {row.processing && (
                  // Figma draws the progress as a plain percentage; the role keeps it a progress.
                  <span
                    role="progressbar"
                    aria-label={`Tiến độ ${row.title}`}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(row.processing.progress * 100)}
                  >
                    {formatPercent(row.processing.progress, 0)}
                  </span>
                )}
              </td>
              <td className={cn(RUN_CELL, 'text-foreground da-mono')}>
                {row.processing ? formatTime(row.processing.started_at) : '—'}
              </td>
            </SelectableRow>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/* What the Document agent did, as the panel of 2065:2161 draws it (same as the collection panel). */
function DocumentPanel({ id }: { id: string }) {
  const doc = useV1(() => getDocumentView(id), [id]);
  return (
    <aside
      aria-label="Chi tiết tài liệu"
      className="flex w-[400px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1023px]:hidden"
    >
      <AsyncRegion state={doc} what="tài liệu" skeletonRows={4}>
        {(d) => <PanelBody doc={d} />}
      </AsyncRegion>
    </aside>
  );
}

function PanelBody({ doc }: { doc: DocumentView }) {
  const { navigate } = useAppState();
  return (
    <>
      <h2 className="text-foreground da-heading-2">{doc.title}</h2>
      <p className="text-muted-foreground da-caption">{doc.origin_text}</p>
      <section className="flex flex-col gap-da-xs">
        <h3 className="text-muted-foreground da-overline">Agent Tài liệu đã xử lý</h3>
        {doc.processing.map((step) => (
          <RunStep
            key={step.title}
            state={step.state}
            title={step.title}
            detail={step.detail}
            duration={step.duration ?? undefined}
          />
        ))}
      </section>
      {doc.excerpt && (
        <blockquote className="flex flex-col gap-da-xs rounded-md border-l-2 border-border-strong px-da-lg py-da-md">
          <span className="text-muted-foreground da-label">{doc.excerpt.label}</span>
          <span className="text-foreground da-body-small">{doc.excerpt.text}</span>
        </blockquote>
      )}
      {doc.effective_text && <KeyValue label="Hiệu lực">{doc.effective_text}</KeyValue>}
      {doc.impact_text && <KeyValue label="Ảnh hưởng">{doc.impact_text}</KeyValue>}
      <div>
        <Button
          kind="ghost"
          icon="external-link"
          onClick={() => navigate('data', 'DOCUMENT', doc.id)}
        >
          Mở tài liệu
        </Button>
      </div>
    </>
  );
}
