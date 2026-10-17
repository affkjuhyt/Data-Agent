import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { RunStep } from '../../design-system/RunStep';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  addDocument,
  getCollection,
  getDocument,
  listDocuments,
  type CollectionDetail,
  type DocumentDetail,
  type StepState,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P05 · Bộ sưu tập · Tài liệu & tiền xử lý" (2300:18794, UC-05-01, UC-05-02): the documents of
 * one collection with the eight pre-processing steps as a strip per row, validity and status.
 * A row opens the document on the right (400px), as "v3.1 · Phi cấu trúc · Tệp & tài liệu"
 * (2065:2161) draws it: what the Document agent did, the quoted clause, validity and impact.
 */
const STEPS = [
  'định danh',
  'trích xuất',
  'chuẩn hoá',
  'lọc',
  'dữ liệu cá nhân',
  'chia đoạn',
  'metadata',
  'kiểm tra',
];

const CELL: Record<StepState, string> = {
  done: 'bg-success opacity-50',
  warn: 'bg-warning',
  error: 'bg-error',
  todo: 'bg-subtle',
};

const STATE_WORD: Record<StepState, string> = {
  done: 'xong',
  warn: 'chờ xem lại',
  error: 'lỗi',
  todo: 'chưa chạy',
};

export default function CollectionPage() {
  const { routeId } = useAppState();
  const collection = useV1(routeId ? () => getCollection(routeId) : null, [routeId]);
  // A collection in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(collection.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={collection} what="bộ sưu tập" skeletonRows={6}>
      {(data) => <CollectionView collection={data} />}
    </AsyncRegion>
  );
}

function CollectionView({ collection }: { collection: CollectionDetail }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const documents = useV1(() => listDocuments(collection.id), [collection.id]);
  const [selected, setSelected] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const view = useTableView(documents.data?.items ?? [], {
    search: (doc) => `${doc.title} ${doc.type_text} ${doc.validity?.text ?? ''} ${doc.status.text}`,
    filters: [{ id: 'kind', label: 'Loại', value: (doc) => doc.type_text.split(' · ')[0] }],
    sorts: [
      { id: 'title', value: (doc) => doc.title },
      { id: 'kind', value: (doc) => doc.type_text },
      { id: 'steps', value: (doc) => doc.steps.join(' ') },
      { id: 'validity', value: (doc) => doc.validity?.text ?? null },
      { id: 'status', value: (doc) => doc.status.text },
    ],
    pageSize: 50,
  });

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      await addDocument(collection.id, file);
      toast({ tone: 'success', message: `Đã nhận ${file.name}. Agent đang tiền xử lý.` });
      documents.reload();
    } catch (e) {
      toast({ tone: 'error', message: `Không tải lên được ${file.name}. ${(e as Error).message}` });
    } finally {
      setUploading(false);
    }
  };

  const facts = [
    `${formatNumber(collection.document_count)} tài liệu`,
    collection.sync_text,
    collection.data_space_name && `Data Space ${collection.data_space_name}`,
    collection.pages_pending_review > 0
      ? `${formatNumber(collection.pages_pending_review)} trang chờ xem lại`
      : null,
  ].filter(Boolean);

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          bordered
          breadcrumb={['Dữ liệu', 'Tài liệu', collection.name]}
          title={collection.name}
          description={facts.join(' · ')}
          actions={
            <>
              <input
                ref={fileRef}
                type="file"
                className="sr-only"
                aria-label="Chọn tài liệu để tải lên"
                onChange={(e) => {
                  void upload(e.target.files?.[0]);
                  e.target.value = '';
                }}
              />
              <Button loading={uploading} onClick={() => fileRef.current?.click()}>
                Tải lên
              </Button>
              <Button kind="primary" onClick={() => navigate('analysis', 'ASK')}>
                Hỏi bộ sưu tập này
              </Button>
            </>
          }
        />
        <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
          <p className="flex flex-wrap gap-x-[14px] text-muted-foreground da-caption">
            <span>8 bước tiền xử lý:</span>
            {STEPS.map((step, i) => (
              <span key={step}>
                {i + 1} {step}
              </span>
            ))}
          </p>
          <AsyncRegion
            state={documents}
            what="tài liệu"
            skeletonRows={5}
            isEmpty={(page) => page.items.length === 0}
            empty={
              <EmptyState
                icon="file"
                title="Bộ sưu tập chưa có tài liệu"
                description="Tài liệu xuất hiện khi nguồn đồng bộ, hoặc khi bạn tải lên."
              />
            }
          >
            {() => (
              <>
              <TableToolbar view={view} placeholder="Tìm tài liệu…" searchLabel="Tìm tài liệu" />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh view={view} column="title" className="px-da-lg py-da-md font-normal">
                      Tài liệu
                    </SortableTh>
                    <SortableTh view={view} column="kind" className="w-[110px] px-da-md py-da-md font-normal">
                      Loại
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="steps"
                      className="w-[220px] px-da-md py-da-md font-normal max-[1279px]:hidden"
                    >
                      Tiền xử lý
                    </SortableTh>
                    <SortableTh view={view} column="validity" className="w-[150px] px-da-md py-da-md font-normal">
                      Hiệu lực
                    </SortableTh>
                    <SortableTh view={view} column="status" className="w-[230px] px-da-md py-da-md font-normal">
                      Trạng thái
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((doc) => (
                    <tr
                      key={doc.id}
                      tabIndex={0}
                      aria-selected={selected === doc.id}
                      onClick={() => setSelected(doc.id)}
                      onKeyDown={(e) => e.key === 'Enter' && setSelected(doc.id)}
                      className={cn(
                        'cursor-pointer border-b border-border hover:bg-subtle focus-visible:bg-subtle',
                        selected === doc.id && 'bg-subtle',
                      )}
                    >
                      <td className="truncate px-da-lg py-da-md text-foreground da-body-strong">
                        {doc.title}
                      </td>
                      <td className="px-da-md py-da-md text-muted-foreground da-caption">
                        {doc.type_text}
                      </td>
                      <td className="px-da-md py-da-md max-[1279px]:hidden">
                        <span
                          className="flex h-3 items-center gap-[3px]"
                          role="img"
                          aria-label={doc.steps
                            .map((s, i) => `${STEPS[i]}: ${STATE_WORD[s]}`)
                            .join(', ')}
                        >
                          {doc.steps.map((s, i) => (
                            <span key={i} className={cn('h-2 w-[22px] rounded-[2px]', CELL[s])} />
                          ))}
                        </span>
                      </td>
                      <td
                        className={cn(
                          'px-da-md py-da-md da-body-small',
                          doc.validity?.warn ? 'text-warning' : 'text-foreground',
                        )}
                      >
                        {doc.validity?.text ?? '—'}
                      </td>
                      <td className="px-da-md py-da-md">
                        <StatusDot tone={doc.status.tone} className="max-w-full truncate">
                          {doc.status.text}
                        </StatusDot>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <TablePager view={view} />
              </>
            )}
          </AsyncRegion>
          <p className="text-muted-foreground da-caption">
            UC-05-01 · UC-05-02 · tài liệu mang quyền của thư mục gốc; dữ liệu cá nhân được che ở
            bước 5 trước khi chia đoạn
          </p>
        </div>
      </div>
      {selected && <DocumentPanel id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function DocumentPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const doc = useV1(() => getDocument(id), [id]);
  return (
    <aside
      aria-label="Chi tiết tài liệu"
      className="flex w-[400px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1023px]:hidden"
    >
      <AsyncRegion state={doc} what="tài liệu" skeletonRows={4}>
        {(d) => <DocumentBody doc={d} onClose={onClose} />}
      </AsyncRegion>
    </aside>
  );
}

function DocumentBody({ doc, onClose }: { doc: DocumentDetail; onClose: () => void }) {
  const { navigate } = useAppState();
  return (
    <>
      <div className="flex items-start gap-da-md">
        <h2 className="min-w-0 flex-1 text-foreground da-heading-2">{doc.title}</h2>
        <button
          type="button"
          onClick={onClose}
          aria-label="Đóng chi tiết tài liệu"
          className="inline-flex size-7 items-center justify-center rounded-md text-muted-foreground hover:bg-subtle"
        >
          <Icon name="x" />
        </button>
      </div>
      <p className="text-muted-foreground da-caption">{doc.origin_text}</p>
      <Button onClick={() => navigate('data', 'DOCUMENT', doc.id)}>Mở tài liệu</Button>
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
      {doc.effective_text && <Fact label="Hiệu lực">{doc.effective_text}</Fact>}
      {doc.impact_text && <Fact label="Ảnh hưởng">{doc.impact_text}</Fact>}
    </>
  );
}

function Fact({ label, children }: { label: string; children: string }) {
  return (
    <div className="flex gap-da-md">
      <span className="w-[90px] shrink-0 text-muted-foreground da-caption">{label}</span>
      <span className="min-w-0 flex-1 text-foreground da-body-small">{children}</span>
    </div>
  );
}
