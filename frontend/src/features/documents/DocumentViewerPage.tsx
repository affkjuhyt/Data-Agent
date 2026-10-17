import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getDocumentPage,
  getDocumentOriginal,
  getDocumentView,
  listActivity,
  listChunks,
  resolveQualityIssue,
  type DocumentPage,
  type DocumentView,
  type ExtractedField,
  type FieldIssue,
  type PageBox,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { CallViewer } from './CallViewer';
import { EmailViewer } from './EmailViewer';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';
import { takePage } from './focusPage';

/*
 * "v3.1 · Phi cấu trúc · Trình xem tài liệu" (2069:3041, UC-05-04, UC-05-05) at /data/document/<id>:
 * the page on the left with the extracted fields outlined on it (or its OCR text), and on the
 * right what the agent made of it in three tabs — Trường (2069:3169), Đoạn văn (2101:12453) and
 * Lịch sử (2101:12848). Figma's Liên kết tab (2101:12637) is not in SCR-10/SCR-34 and A2 has no
 * source for it, so it is not drawn. An email opens "Trình xem email" (2075:5236) and a
 * recording "Trình xem ghi âm" (2075:5061) at the same address.
 */
export default function DocumentViewerPage() {
  const { routeId } = useAppState();
  const doc = useV1(routeId ? () => getDocumentView(routeId) : null, [routeId]);
  // A doc in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(doc.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  if (!routeId)
    return (
      <EmptyState
        className="m-da-3xl"
        icon="file"
        title="Chưa chọn tài liệu"
        description="Mở một tài liệu từ bộ sưu tập hoặc danh sách tài liệu."
      />
    );
  return (
    <AsyncRegion state={doc} what="tài liệu" skeletonRows={6}>
      {(data) => {
        const view = normalize(data);
        if (view.kind === 'email' && view.email)
          return <EmailViewer doc={view} thread={view.email} onChanged={doc.reload} />;
        if (view.kind === 'call' && view.call) return <CallViewer doc={view} call={view.call} />;
        return <FileViewer key={view.id} doc={view} onChanged={doc.reload} />;
      }}
    </AsyncRegion>
  );
}

/*
 * GET /v1/documents/{id} may come from a server (or mock) that only knows the pre-processing
 * shape of the collection screen; the viewer fields then default to an empty reading.
 */
function normalize(doc: DocumentView): DocumentView {
  return {
    ...doc,
    kind: doc.kind ?? 'file',
    file_name: doc.file_name ?? doc.title,
    collection: doc.collection ?? null,
    page_count: doc.page_count ?? 1,
    focus_page: doc.focus_page ?? 1,
    summary_text: doc.summary_text ?? null,
    type_label: doc.type_label ?? doc.title,
    signals: doc.signals ?? [],
    fields: doc.fields ?? [],
    issue: doc.issue ?? null,
    index_text: doc.index_text ?? null,
  };
}

type Tab = 'fields' | 'chunks' | 'history';
type Mode = 'original' | 'ocr';

function FileViewer({ doc, onChanged }: { doc: DocumentView; onChanged: () => void }) {
  const { navigate } = useAppState();
  const hasPages = doc.page_count > 0;
  // A citation opens the page it cites (focusPage.ts); otherwise the document's focus page.
  const [pageNo, setPageNo] = useState(() =>
    Math.min(Math.max(takePage(doc.id) ?? doc.focus_page, 1), Math.max(doc.page_count, 1)),
  );
  const [mode, setMode] = useState<Mode>('original');
  const [tab, setTab] = useState<Tab>('fields');
  // Newly uploaded documents have no pages until the ingest worker finishes. Do not send page 0
  // to the API: its schema correctly rejects it, but the viewer should show the designed waiting
  // state instead of leaking a 422 to the user.
  const page = useV1(hasPages ? () => getDocumentPage(doc.id, pageNo) : null, [doc.id, pageNo]);
  // Only a PDF is embedded as itself. Any other original (an uploaded HTML page above all) would
  // run in the app's origin from a blob: URL and lose the styles it links to; it is shown as the
  // text the worker extracted, in the document's own structure.
  const pdf = doc.media === 'pdf';
  const original = useV1(pdf ? () => getDocumentOriginal(doc.id) : null, [doc.id, pdf]);
  const paged = pdf || doc.media === 'image';
  const collection = doc.collection;

  return (
    <div className="flex min-h-full">
      <section
        aria-label="Trang tài liệu"
        className="flex min-w-0 flex-1 flex-col items-center bg-subtle pb-da-3xl"
      >
        {doc.summary_text && <Notice tone="info">{doc.summary_text}</Notice>}
        <div className="flex w-full flex-wrap items-center gap-da-md border-b border-border bg-card px-da-xl py-da-md">
          <span className="whitespace-nowrap text-muted-foreground da-caption">
            Phi cấu trúc ›{' '}
            {collection ? (
              <button
                type="button"
                className="hover:text-foreground hover:underline"
                onClick={() => navigate('data', 'COLLECTION', collection.id)}
              >
                {collection.name}
              </button>
            ) : null}{' '}
            ›
          </span>
          <span className="truncate text-foreground da-mono">{doc.file_name}</span>
          <span className="flex-1" />
          <span className="flex items-center gap-da-xs">
            <PageButton
              label="Trang trước"
              disabled={pageNo <= 1}
              onClick={() => setPageNo((n) => n - 1)}
              flip
            />
            <span
              className="whitespace-nowrap text-muted-foreground da-body-small"
              aria-live="polite"
            >
              {hasPages ? `Trang ${pageNo} / ${doc.page_count}` : 'Đang chuẩn bị trang'}
            </span>
            <PageButton
              label="Trang sau"
              disabled={!hasPages || pageNo >= doc.page_count}
              onClick={() => setPageNo((n) => n + 1)}
            />
          </span>
          {paged && (
            <Segmented<Mode>
              label="Cách xem trang"
              value={mode}
              onChange={setMode}
              items={[
                { value: 'original', label: 'Bản gốc' },
                { value: 'ocr', label: 'Văn bản OCR' },
              ]}
            />
          )}
        </div>
        <div className="h-da-2xl shrink-0" />
        {hasPages ? (
          <AsyncRegion state={page} what="trang" skeletonRows={8}>
            {(p) =>
              !paged ? (
                <PageText page={p} />
              ) : mode === 'original' && original.data ? (
                <OriginalPdf url={original.data} fileName={doc.file_name} />
              ) : mode === 'original' && hasGeometry(p) ? (
                <PageImage page={p} />
              ) : (
                <PageText page={p} />
              )
            }
          </AsyncRegion>
        ) : (
          <EmptyState
            className="m-da-3xl"
            icon="file"
            title="Tài liệu đang được chuẩn bị"
            description="Agent đang nhận dạng và tạo các trang đọc. Bạn có thể quay lại sau ít phút."
          />
        )}
      </section>

      <aside
        aria-label="Agent đã đọc"
        className="flex w-[460px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1023px]:hidden"
      >
        <div className="flex flex-col gap-da-xs">
          <h1 className="text-foreground da-heading-2">{doc.type_label}</h1>
          {doc.signals.length > 0 && (
            <div className="flex flex-wrap gap-x-da-sm gap-y-da-xs">
              {doc.signals.map((s) => (
                <StatusDot key={s.text} tone={s.tone}>
                  {s.text}
                </StatusDot>
              ))}
            </div>
          )}
        </div>
        <Tabs<Tab>
          label="Nội dung agent đọc"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'fields', label: `Trường · ${formatNumber(doc.fields.length)}` },
            { value: 'chunks', label: 'Đoạn văn' },
            { value: 'history', label: 'Lịch sử' },
          ]}
        />
        <div role="tabpanel" className="flex flex-col gap-da-lg">
          {tab === 'fields' && <FieldsTab doc={doc} onJump={setPageNo} onChanged={onChanged} />}
          {tab === 'chunks' && <ChunksTab doc={doc} onJump={setPageNo} />}
          {tab === 'history' && <HistoryTab id={doc.id} />}
        </div>
      </aside>
    </div>
  );
}

function OriginalPdf({ url, fileName }: { url: string; fileName: string }) {
  return (
    <iframe
      title={`Bản gốc ${fileName}`}
      src={url}
      className="h-[842px] w-[595px] max-w-full border border-border bg-card"
    />
  );
}

function PageButton({
  label,
  disabled,
  onClick,
  flip = false,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  flip?: boolean;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      aria-disabled={disabled || undefined}
      onClick={disabled ? undefined : onClick}
      className={cn(
        'inline-flex size-7 items-center justify-center rounded-md text-muted-foreground',
        disabled ? 'cursor-not-allowed opacity-50' : 'hover:bg-subtle hover:text-foreground',
      )}
    >
      <Icon name="chevron-right" className={flip ? 'rotate-180' : undefined} />
    </button>
  );
}

/* The page at 560px wide: its image when the server keeps one, else its layout boxes. */
function PageImage({ page }: { page: DocumentPage }) {
  const at = (box: PageBox) => ({
    left: `${(box.x / page.width) * 100}%`,
    top: `${(box.y / page.height) * 100}%`,
    width: `${(box.w / page.width) * 100}%`,
    height: `${(box.h / page.height) * 100}%`,
  });
  return (
    <div
      role="img"
      aria-label={`Trang ${page.number}${page.highlights.length ? `, đánh dấu: ${page.highlights.map((h) => h.label).join(', ')}` : ''}`}
      className="relative w-[560px] max-w-full shrink-0 overflow-hidden border border-border bg-card"
      style={{ aspectRatio: `${page.width} / ${page.height}` }}
    >
      {page.image_url ? (
        <img src={page.image_url} alt="" className="absolute inset-0 size-full object-contain" />
      ) : (
        page.blocks.map((b, i) =>
          b.kind === 'title' ? (
            <span
              key={i}
              className="absolute whitespace-nowrap text-center text-foreground da-heading-3"
              style={at(b.box)}
            >
              {b.text}
            </span>
          ) : (
            <span
              key={i}
              className={cn(
                'absolute rounded-[2px]',
                b.kind === 'heading' ? 'bg-border-strong' : 'bg-subtle',
              )}
              style={at(b.box)}
            />
          ),
        )
      )}
      {page.highlights.map((h) => (
        <span key={h.field}>
          <span
            className={cn(
              'absolute rounded-[3px] border-2',
              h.review ? 'border-warning' : 'border-primary',
            )}
            style={at(h.box)}
          />
          <span
            className={cn(
              'absolute whitespace-nowrap rounded-sm px-da-sm py-da-xs text-primary-foreground da-label',
              h.review ? 'bg-warning' : 'bg-primary',
            )}
            style={{
              right: `${((page.width - h.box.x - h.box.w) / page.width) * 100}%`,
              top: `${((h.box.y - 26) / page.height) * 100}%`,
            }}
          >
            {h.label}
          </span>
        </span>
      ))}
    </div>
  );
}

/* A page that can be drawn as a page: an image, or blocks placed on it. Text-based media
   (html, docx, email) have neither and are read as reflowed text. */
function hasGeometry(page: DocumentPage): boolean {
  return (
    !!page.image_url ||
    page.highlights.length > 0 ||
    page.blocks.some((b) => b.box.w > 0 && b.box.h > 0)
  );
}

/* A table as the worker writes it into a block: one row per line, cells joined by " | ". */
function tableRows(text: string): string[][] | null {
  const lines = text.split('\n');
  if (lines.length < 2 || !lines.every((line) => line.includes(' | '))) return null;
  return lines.map((line) => line.split(' | '));
}

function PageText({ page }: { page: DocumentPage }) {
  if (page.blocks.length > 0) {
    const structuredText = page.blocks.map((block) => block.text).join("\n");
    return (
      <div className="flex w-[560px] max-w-full shrink-0 flex-col gap-da-md border border-border bg-card p-da-3xl text-foreground">
        {page.blocks.map((block, index) =>
          block.kind === 'picture' && block.image_url ? (
            <figure key={index} className="flex flex-col gap-da-xs">
              <img src={block.image_url} alt="Hình trong tài liệu" className="max-h-[520px] max-w-full object-contain" />
              {block.text && <figcaption className="text-muted-foreground da-caption">{block.text}</figcaption>}
            </figure>
          ) : block.kind === 'heading' || block.kind === 'title' ? (
            <h3 key={index} className={cn('text-foreground', block.level === 1 ? 'da-heading-2' : block.level === 2 ? 'da-heading-3' : 'da-body-strong')}>
              {block.text}
            </h3>
          ) : tableRows(block.text) ? (
            <table key={index} className="w-full border-collapse da-body-small">
              <tbody>
                {tableRows(block.text)!.map((row, r) => (
                  <tr key={r} className="border-b border-border">
                    {row.map((cell, c) => (
                      <td key={c} className="px-da-sm py-da-xs align-top">{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p key={index} className="whitespace-pre-line da-body">{block.text}</p>
          ),
        )}
        {page.text && !structuredText.includes(page.text) && (
          <p className="whitespace-pre-line da-body">{page.text}</p>
        )}
      </div>
    );
  }
  return (
    <div className="w-[560px] max-w-full shrink-0 whitespace-pre-line border border-border bg-card p-da-3xl text-foreground da-body">
      {page.text}
    </div>
  );
}

function FieldsTab({
  doc,
  onJump,
  onChanged,
}: {
  doc: DocumentView;
  onJump: (page: number) => void;
  onChanged: () => void;
}) {
  if (doc.fields.length === 0)
    return (
      <p className="text-muted-foreground da-body-small">
        Tài liệu này chưa có lược đồ trích xuất; agent chỉ chia đoạn để tìm kiếm.
      </p>
    );
  return (
    <>
      <ul className="flex flex-col gap-da-xs">
        {doc.fields.map((field) => (
          <FieldRow key={field.name} field={field} onJump={onJump} />
        ))}
      </ul>
      {doc.issue && <IssueBox issue={doc.issue} onChanged={onChanged} />}
    </>
  );
}

function FieldRow({ field, onJump }: { field: ExtractedField; onJump: (page: number) => void }) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onJump(field.page)}
        aria-label={`${field.name}: ${field.value}, trang ${field.page}, độ tin ${field.confidence.toFixed(2).replace('.', ',')}`}
        className={cn(
          'flex w-full items-center gap-da-md rounded-md px-da-md py-da-sm text-left hover:bg-subtle',
          field.review ? 'border border-border' : 'border-b border-border',
        )}
      >
        <span className="w-[110px] shrink-0 truncate text-muted-foreground da-mono">
          {field.name}
        </span>
        <span className="min-w-0 flex-1 text-foreground da-body-strong">{field.value}</span>
        <span className="shrink-0 text-muted-foreground da-caption">tr.{field.page}</span>
        <StatusDot tone={field.review ? 'warning' : 'success'}>
          {field.confidence.toFixed(2).replace('.', ',')}
        </StatusDot>
      </button>
    </li>
  );
}

function IssueBox({ issue, onChanged }: { issue: FieldIssue; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<'correct' | 'keep' | null>(null);
  const resolve = async (action: 'correct' | 'keep') => {
    setBusy(action);
    try {
      await resolveQualityIssue(
        issue.quality_issue_id,
        action === 'correct'
          ? { action: 'correct', value: issue.suggested_value }
          : { action: 'keep_and_conflict' },
      );
      toast({
        tone: 'success',
        message:
          action === 'correct'
            ? `Đã sửa ${issue.field} thành ${issue.suggested_value}.`
            : 'Đã giữ giá trị đọc được và mở yêu cầu xung đột.',
      });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: `Không lưu được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="flex flex-col gap-da-sm rounded-md border border-warning px-da-lg py-da-md">
      <p className="text-foreground da-body-strong">{issue.title}</p>
      <p className="text-muted-foreground da-caption">{issue.detail}</p>
      <div className="flex flex-wrap gap-x-da-sm gap-y-da-md">
        <Button
          loading={busy === 'correct'}
          disabled={busy !== null && busy !== 'correct'}
          onClick={() => void resolve('correct')}
        >
          Sửa thành {issue.suggested_value}
        </Button>
        <Button
          kind="ghost"
          loading={busy === 'keep'}
          disabled={busy !== null && busy !== 'keep'}
          onClick={() => void resolve('keep')}
        >
          Giữ {issue.current_value} · báo xung đột
        </Button>
      </div>
    </div>
  );
}

function ChunksTab({ doc, onJump }: { doc: DocumentView; onJump: (page: number) => void }) {
  const chunks = useV1(() => listChunks(doc), [doc.id, doc.page_count]);
  return (
    <AsyncRegion
      state={chunks}
      what="đoạn văn"
      skeletonRows={3}
      isEmpty={(p) => p.items.length === 0}
      empty={<p className="text-muted-foreground da-body-small">Tài liệu chưa được chia đoạn.</p>}
    >
      {(p) => (
        <div className="flex flex-col gap-da-lg">
          <ul className="flex flex-col gap-da-lg">
            {p.items.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  onClick={() => onJump(c.page)}
                  className="flex w-full flex-col gap-da-xs rounded-md border border-border px-da-md py-da-sm text-left hover:bg-subtle"
                >
                  <span className="text-muted-foreground da-mono">
                    tr.{c.page} · đoạn {c.ordinal}
                  </span>
                  <span className="text-foreground da-body-small">{c.text}</span>
                </button>
              </li>
            ))}
          </ul>
          {doc.index_text && <p className="text-muted-foreground da-caption">{doc.index_text}</p>}
        </div>
      )}
    </AsyncRegion>
  );
}

function HistoryTab({ id }: { id: string }) {
  const activity = useV1(() => listActivity(id), [id]);
  return (
    <AsyncRegion
      state={activity}
      what="lịch sử"
      skeletonRows={4}
      isEmpty={(p) => p.items.length === 0}
      empty={<p className="text-muted-foreground da-body-small">Chưa có hoạt động nào.</p>}
    >
      {(p) => (
        <ul className="flex flex-col gap-da-lg">
          {p.items.map((a) => (
            <li key={a.id} className="text-foreground da-body-small">
              {a.when_text} · {a.text}
            </li>
          ))}
        </ul>
      )}
    </AsyncRegion>
  );
}
