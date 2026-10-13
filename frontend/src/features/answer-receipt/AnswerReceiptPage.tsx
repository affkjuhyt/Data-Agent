import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { Drawer } from '../../design-system/Drawer';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Radio } from '../../design-system/Radio';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { PageHeader } from '../../components/shell/PageHeader';
import { formatDateTime } from '../../lib/formatVN';
import {
  getAnswerReceipt,
  rerunReceipt,
  type AnswerReceipt,
  type ReceiptFigure,
  type ReceiptRerun,
  type RerunMode,
} from '../../lib/v1/answerReceipts';
import { V1Error } from '../../lib/v1/client';
import { useV1 } from '../../lib/v1/useV1';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P01 · Biên nhận câu trả lời" (2420:15098, UC-01-07, SCR-68). Figma draws it as a 480px drawer
 * over the session; here it is its own page (/analysis/receipt/<answer id>) so a receipt can be
 * linked and audited without the conversation. Order as drawn: code + lock, date · data product,
 * the receipt hash, one card per figure (F1, F2…) back to its query or compute run, the models
 * that wrote the words, and "Chạy lại tại thời điểm cũ". The frame's "who asked" and the dataset's
 * load time are not in the api's receipt, so they are not drawn.
 *
 * The frame's "Biến thể số" and "Biến thể frontier" are not blocks of their own: they show how a
 * figure past its update window or without a value reads, and the line an external model adds;
 * the cards and the model row render them when the receipt says so.
 */

// Dates without a time are calendar days: parsing "2026-06-15" as a Date would shift it by the
// viewer's UTC offset.
const day = (iso: string) => iso.slice(0, 10).split('-').reverse().join('/');
const dayMonth = (iso: string) => iso.slice(0, 10).split('-').reverse().slice(0, 2).join('/');
const hourMinute = (iso: string) => iso.slice(11, 16);
const shortHash = (hash: string | null, head: number, tail: number) =>
  hash ? `${hash.slice(0, head)}…${hash.slice(-tail)}` : '—';

const RERUN_NOTE: Record<RerunMode, string> = {
  figures:
    'Câu chữ không sinh lại. Nếu sinh lại, câu chữ có thể khác vì mô hình ngôn ngữ không tất định; số giữ nguyên.',
  // copy: provisional — Figma draws the note of the "Chỉ số" mode only.
  figures_and_text:
    'Câu chữ đã sinh lại và có thể khác bản gốc vì mô hình ngôn ngữ không tất định; số giữ nguyên.',
};

/** The receipt of one answer: empty when the server never stored it, Thiếu quyền when refused. */
function ReceiptView({ answerId }: { answerId: string | undefined }) {
  const receipt = useV1(answerId ? () => getAnswerReceipt(answerId) : null, [answerId]);

  const forbidden = forbiddenOf(receipt.error);
  if (forbidden) return <AccessDenied access={forbidden} />;

  // A 404 is the expected case for an answer the server never stored (the Ask stream does not
  // hand out server answer ids yet), so it reads as an empty state rather than a load failure.
  const missing = !answerId || (receipt.error instanceof V1Error && receipt.error.status === 404);

  return missing ? (
    <EmptyState
      icon="info"
      title="Câu trả lời này chưa có biên nhận"
      description="Biên nhận được khoá khi câu trả lời được lưu trên máy chủ. Câu trả lời chưa lưu, hoặc đã bị xoá, thì không có biên nhận để mở."
    />
  ) : (
    <AsyncRegion state={receipt} what="biên nhận" skeletonRows={6}>
      {(r) => <ReceiptBody receipt={r} />}
    </AsyncRegion>
  );
}

/*
 * As Figma draws it: a 480px drawer over the conversation, so Hỏi đáp keeps its turns
 * (they live in the page's memory) while the receipt is open.
 */
export function ReceiptDrawer({
  answerId,
  onClose,
}: {
  answerId: string | null;
  onClose: () => void;
}) {
  return (
    <Drawer
      open={answerId !== null}
      width="receipt"
      title="Biên nhận câu trả lời"
      onClose={onClose}
    >
      <div className="flex flex-col gap-da-lg">
        <ReceiptView answerId={answerId ?? undefined} />
      </div>
    </Drawer>
  );
}

/** /analysis/receipt/<answer id>: the same receipt on its own page, for a shared link or a notification. */
export default function AnswerReceiptPage() {
  const { routeId } = useAppState();
  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Hỏi đáp', 'Biên nhận']}
        title="Biên nhận câu trả lời"
        className="max-[767px]:px-da-md"
      />
      <div className="px-da-3xl pb-da-2xl pt-da-xl max-[767px]:px-da-md">
        <div className="flex w-[680px] max-w-full flex-col gap-da-lg">
          <ReceiptView answerId={routeId} />
        </div>
      </div>
    </div>
  );
}

function ReceiptBody({ receipt }: { receipt: AnswerReceipt }) {
  const toast = useToast();
  const { navigate } = useAppState();
  const external = receipt.models.filter((m) => m.external).map((m) => m.provider_label);
  const copyHash = async () => {
    try {
      await navigator.clipboard.writeText(receipt.receipt_sha256 ?? '');
      toast({ tone: 'success', message: 'Đã chép mã SHA-256 của biên nhận' });
    } catch {
      toast({ tone: 'error', message: 'Trình duyệt không cho chép. Hãy chọn và chép thủ công.' });
    }
  };

  return (
    <>
      <div className="flex items-center gap-da-md">
        <span className="text-foreground da-body font-mono">{receipt.code}</span>
        {receipt.status === 'locked' && <Badge tone="success">Đã khoá</Badge>}
      </div>
      <p className="text-muted-foreground da-caption">
        {formatDateTime(receipt.created_at)} ·{' '}
        <button
          type="button"
          className="text-info hover:underline"
          onClick={() => navigate('data', 'ANSWER_PRODUCTS', receipt.data_product.id)}
        >
          {receipt.data_product.name}
        </button>
      </p>

      {receipt.receipt_sha256 && (
        <div className="flex items-center gap-da-md rounded-sm bg-subtle py-da-sm pl-[10px] pr-da-sm">
          <span className="min-w-0 flex-1 truncate whitespace-pre text-foreground da-mono">
            {`SHA-256 biên nhận  ${shortHash(receipt.receipt_sha256, 8, 4)}`}
          </span>
          <Button size="sm" onClick={copyHash} aria-label="Chép mã SHA-256 của biên nhận">
            Chép
          </Button>
        </div>
      )}

      <section aria-labelledby="receipt-figures" className="flex flex-col gap-da-lg">
        <h2 id="receipt-figures" className="text-foreground da-body-strong font-semibold">
          Số trong câu trả lời · {receipt.figures.length}
        </h2>
        <ul aria-labelledby="receipt-figures" className="m-0 flex list-none flex-col gap-da-lg p-0">
          {receipt.figures.map((figure) => (
            <li key={figure.key}>
              <FigureCard figure={figure} />
            </li>
          ))}
        </ul>
      </section>

      {receipt.models.length > 0 && (
        <dl className="m-0 flex items-start gap-da-md da-caption">
          <dt className="w-[110px] shrink-0 text-muted-foreground">Mô hình viết câu</dt>
          <dd className="m-0 flex flex-col text-foreground da-label">
            {receipt.models.map((m) => (
              <span key={`${m.name}|${m.provider_label}`}>
                {m.name} · {m.provider_label}
              </span>
            ))}
          </dd>
        </dl>
      )}
      {receipt.frontier_used && (
        // A frontier provider only ever receives public data (its max_classification is public).
        <p className="rounded-sm bg-accent px-da-lg py-da-md text-foreground da-caption">
          Có dùng mô hình bên ngoài
          {external.length ? `: ${external.join(', ')}` : ''}, chỉ với dữ liệu công khai
        </p>
      )}

      <Rerun receipt={receipt} />
    </>
  );
}

function sourceRows(figure: ReceiptFigure): { label: string; value: string; mono?: boolean }[] {
  const { source } = figure;
  if (source.kind === 'compute_run')
    return [
      { label: 'Nguồn', value: 'compute_run · phép tính trên các số khác' },
      { label: 'Phép tính', value: `SHA ${shortHash(source.expr_sha256, 4, 2)}`, mono: true },
    ];
  if (source.kind === 'period_result')
    return [{ label: 'Nguồn', value: 'Số đã công bố của kỳ chốt' }];
  const rows: { label: string; value: string; mono?: boolean }[] = [];
  if (source.metric)
    rows.push({
      label: 'Chỉ số',
      value: [
        source.metric.version != null ? `v${source.metric.version}` : null,
        source.metric.certified_at ? `ký ${day(source.metric.certified_at)}` : null,
      ]
        .filter(Boolean)
        .join(' · '),
    });
  if (source.datasets.length)
    rows.push({
      label: 'Dataset',
      value: source.datasets
        .map((d) =>
          [d.version != null ? `v${d.version}` : null, d.as_of && `as_of ${day(d.as_of)}`]
            .filter(Boolean)
            .join(' · '),
        )
        .join('; '),
    });
  rows.push({
    label: 'Truy vấn',
    value: [`SHA ${shortHash(source.query.sql_sha256, 4, 2)}`, source.query.compiler]
      .filter(Boolean)
      .join(' · '),
    mono: true,
  });
  if (figure.due_at)
    rows.push({
      label: 'Độ tươi',
      value: `hạn ${dayMonth(figure.due_at)} ${hourMinute(figure.due_at)}`,
    });
  return rows;
}

function FigureCard({ figure }: { figure: ReceiptFigure }) {
  const rows = sourceRows(figure);
  return (
    <article
      aria-label={`Số ${figure.key}`}
      className="flex flex-col gap-da-sm rounded-md border border-border bg-card px-da-lg py-[10px]"
    >
      <div className="flex items-center gap-da-md">
        <Badge tone="accent">{figure.key}</Badge>
        {figure.value_text && (
          <span className="text-foreground da-reading font-semibold">{figure.value_text}</span>
        )}
        <span className="flex-1" />
        {figure.state === 'fresh' && <Badge tone="success">Còn hạn</Badge>}
      </div>
      <p className="text-muted-foreground da-caption">{figure.label}</p>
      {figure.state === 'stale' && (
        <StatusDot tone="warning" className="whitespace-normal da-caption">
          {figure.key} · {figure.stale_note ?? 'đã quá hạn cập nhật'}
        </StatusDot>
      )}
      {figure.state === 'missing' && (
        <StatusDot tone="neutral" className="whitespace-normal da-caption">
          {figure.key} · CHƯA CÓ (kỳ chưa đến hạn hoặc dataset chưa có dữ liệu)
        </StatusDot>
      )}
      <dl className="m-0 flex flex-col gap-da-sm da-caption">
        {rows.map((row) => (
          <div key={row.label} className="flex items-start gap-da-md">
            <dt className="w-[70px] shrink-0 text-muted-foreground">{row.label}</dt>
            <dd className={`m-0 min-w-0 flex-1 text-foreground ${row.mono ? 'font-mono' : ''}`}>
              {row.value}
            </dd>
          </div>
        ))}
      </dl>
    </article>
  );
}

function rerunSummary(run: ReceiptRerun): string {
  // copy: provisional — Figma draws only the matching outcome.
  if (run.failed) return 'Lượt chạy lại không hoàn tất';
  if (run.outcome === 'not_reproducible') return 'Không tái lập được số tại thời điểm cũ';
  const head =
    run.figures_matched === run.figures_total
      ? `${run.figures_matched}/${run.figures_total} số tái lập y hệt (${run.values_text.join('; ')})`
      : // copy: provisional — Figma draws only the matching outcome.
        `${run.figures_matched}/${run.figures_total} số tái lập y hệt · khác: ${run.mismatches
          .map((m) => `${m.key} ${m.before ?? '—'} → ${m.after ?? '—'}`)
          .join('; ')}`;
  const hashes = run.hashes_match
    ? 'SHA-256 SQL và kết quả khớp'
    : // copy: provisional
      'SHA-256 SQL hoặc kết quả không khớp';
  return [head, hashes, run.cause_summary].filter(Boolean).join(' · ');
}

function Rerun({ receipt }: { receipt: AnswerReceipt }) {
  const toast = useToast();
  const [mode, setMode] = useState<RerunMode>('figures');
  const [busy, setBusy] = useState(false);
  // The receipt does not say whether it was re-run before: only this visit's re-run is shown.
  const [run, setRun] = useState<ReceiptRerun | null>(null);

  const start = async () => {
    setBusy(true);
    try {
      setRun(await rerunReceipt(receipt.id, mode));
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy lại được: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const reproduced =
    run && !run.failed && run.figures_matched === run.figures_total && run.hashes_match;

  return (
    <section
      aria-labelledby="receipt-rerun"
      className="flex flex-col gap-da-md rounded-md border border-border p-da-lg"
    >
      <h2 id="receipt-rerun" className="text-foreground da-body-strong font-semibold">
        Chạy lại tại thời điểm cũ
      </h2>
      <div className="flex flex-wrap items-center gap-da-md">
        <fieldset className="m-0 flex items-center gap-da-md border-0 p-0">
          <legend className="sr-only">Chạy lại phần nào</legend>
          <Radio
            name="rerun-mode"
            value="figures"
            checked={mode === 'figures'}
            onChange={() => setMode('figures')}
          >
            Chỉ số
          </Radio>
          <Radio
            name="rerun-mode"
            value="figures_and_text"
            checked={mode === 'figures_and_text'}
            onChange={() => setMode('figures_and_text')}
          >
            Chỉ số và câu chữ
          </Radio>
        </fieldset>
        <span className="flex-1" />
        <Button
          kind="primary"
          loading={busy}
          disabled={busy || receipt.status !== 'locked'}
          onClick={start}
        >
          Chạy lại
        </Button>
      </div>
      {run && (
        <div role="status" className="flex flex-col gap-da-md">
          <StatusDot
            tone={reproduced ? 'success' : 'warning'}
            className="whitespace-normal da-caption"
          >
            {rerunSummary(run)}
          </StatusDot>
          {run.answer_text && (
            <blockquote className="m-0 rounded-sm bg-subtle px-da-lg py-da-md text-foreground da-caption">
              {run.answer_text}
            </blockquote>
          )}
          <p className="text-muted-foreground da-caption">{RERUN_NOTE[run.mode]}</p>
        </div>
      )}
    </section>
  );
}
