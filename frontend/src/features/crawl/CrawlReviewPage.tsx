import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  bulkReviewRecords,
  getCrawlRun,
  listRunRecords,
  publishCrawlRun,
  REVIEW_REASON_LABEL,
  reviewRecord,
  type CrawlRun,
  type ExtractedRecord,
  type ReviewGate,
  type ReviewReason,
} from '../../lib/v1/crawl';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P14 · Duyệt dữ liệu thu thập" (2390:14372, UC-14-04): the records of one run that need a
 * person — low confidence, anomalies against other sources or the source's history, missing
 * required fields, new values, and the random sample. The selected record shows the raw source
 * with the row it came from, the extracted values, the evidence and the agent's suggestion; it
 * is confirmed (as suggested), corrected or rejected. Records sharing a reason can be decided in
 * bulk. The run publishes only when nothing blocking is left (BR-153); corrections become
 * examples for the extractor.
 */
const REASON = REVIEW_REASON_LABEL;
const FILTERS: ReviewReason[] = [
  'low_confidence',
  'cross_source_anomaly',
  'new_value',
  'random_sample',
];
const GATE: Record<ReviewGate, string> = {
  auto: 'Tự động',
  exceptions: 'Duyệt ngoại lệ',
  all: 'Duyệt hết',
};

function badgeTone(r: ExtractedRecord): 'warning' | 'error' | 'info' | 'neutral' {
  if (r.reasons.includes('missing_required')) return 'error';
  if (r.blocking) return 'warning';
  if (r.reasons.includes('new_value')) return 'info';
  return 'neutral';
}

const fmt = (n: number) => n.toFixed(2).replace('.', ',');

export default function CrawlReviewPage() {
  const { routeId } = useAppState();
  const run = useV1(routeId ? () => getCrawlRun(routeId) : null, [routeId]);
  const records = useV1(routeId ? () => listRunRecords(routeId) : null, [routeId]);
  const reload = () => {
    run.reload();
    records.reload();
  };
  return (
    <AsyncRegion state={run} what="lượt thu">
      {(r) => <ReviewBody run={r} records={records.data?.items ?? []} onChanged={reload} />}
    </AsyncRegion>
  );
}

function ReviewBody({
  run,
  records,
  onChanged,
}: {
  run: CrawlRun;
  records: ExtractedRecord[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [filter, setFilter] = useState<'all' | ReviewReason>('all');
  const [selectedId, setSelectedId] = useState<string>();
  const [bulkOpen, setBulkOpen] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState<string>();
  const published = run.status === 'published';

  const shown = records.filter((r) => filter === 'all' || r.reasons.includes(filter));
  const selected = shown.find((r) => r.id === selectedId) ?? shown[0];
  const count = (reason: ReviewReason) => records.filter((r) => r.reasons.includes(reason)).length;

  const publish = async () => {
    setPublishing(true);
    setError(undefined);
    try {
      await publishCrawlRun(run.id);
      toast({ tone: 'success', message: `Đã phát hành ${run.label}; hợp nhất bắt đầu chạy` });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPublishing(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Thu thập ngoài', `${run.source_name} · ${run.label}`]}
        title="Duyệt dữ liệu thu thập"
        description={[
          `${run.record_count} bản ghi trích được`,
          `${run.review_count} cần người xem`,
          `${run.blocking_count} mục chặn phát hành`,
          `mẫu ngẫu nhiên ${run.sample_count} bản ghi (${run.sample_pct}%)`,
        ].join(' · ')}
        actions={
          published ? (
            <StatusDot tone="success">Đã phát hành</StatusDot>
          ) : (
            <>
              <Button disabled={records.length === 0} onClick={() => setBulkOpen(true)}>
                Duyệt hàng loạt theo lý do
              </Button>
              {run.blocking_count > 0 ? (
                <Tooltip content={`Còn ${run.blocking_count} mục chặn trong hàng duyệt (BR-153).`}>
                  <Button kind="primary" disabled>
                    Phát hành lượt
                  </Button>
                </Tooltip>
              ) : (
                <Button kind="primary" loading={publishing} onClick={publish}>
                  Phát hành lượt
                </Button>
              )}
            </>
          )
        }
      />
      <div className="flex items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl max-[1279px]:flex-col">
        <nav
          aria-label="Hàng duyệt"
          className="flex w-[380px] shrink-0 flex-col gap-da-lg max-[1279px]:w-full"
        >
          <div role="radiogroup" aria-label="Lý do" className="flex flex-wrap gap-[6px]">
            {(['all', ...FILTERS] as const).map((f) => (
              <button
                key={f}
                type="button"
                role="radio"
                aria-checked={filter === f}
                onClick={() => setFilter(f)}
                className={cn(
                  'rounded-full px-da-md py-da-xs da-label',
                  filter === f ? 'bg-accent text-primary' : 'bg-subtle text-muted-foreground',
                )}
              >
                {f === 'all' ? `Tất cả ${records.length}` : `${REASON[f]} ${count(f)}`}
              </button>
            ))}
          </div>
          {shown.length === 0 && (
            <p className="text-muted-foreground da-body-small">Không còn bản ghi nào cần xem.</p>
          )}
          {shown.map((r) => (
            <button
              key={r.id}
              type="button"
              aria-current={r.id === selected?.id ? 'true' : undefined}
              onClick={() => setSelectedId(r.id)}
              className={cn(
                'flex flex-col items-start gap-da-xs rounded-md border px-da-lg py-[10px] text-left',
                r.id === selected?.id
                  ? 'border-ring bg-accent'
                  : 'border-border bg-card hover:bg-subtle',
              )}
            >
              <span className="flex w-full items-center gap-da-md text-foreground da-body-small">
                <span className="min-w-0 flex-1 da-body-strong">{r.title}</span>
                <span className="da-heading-3">{r.value_text}</span>
              </span>
              <Badge tone={badgeTone(r)}>
                {r.reason_text}
                {r.blocking ? ' · chặn' : ''}
              </Badge>
            </button>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col gap-da-xl max-[1279px]:w-full">
          {error && <Notice tone="error">Không phát hành được: {error}</Notice>}
          {selected && !published && (
            <RecordCard
              key={selected.id}
              record={selected}
              runLabel={run.label}
              onDone={onChanged}
            />
          )}
          <section className="flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl">
            <h2 className="text-foreground da-heading-3">Độ chính xác của lượt</h2>
            <div className="flex gap-da-xl max-[1023px]:flex-wrap">
              <Stat
                label="Mẫu ngẫu nhiên đã duyệt"
                value={`${run.accuracy.sample_reviewed} / ${run.sample_count}`}
                note={`${run.sample_count - run.accuracy.sample_reviewed} còn chờ`}
              />
              <Stat
                label="Chính xác trên mẫu"
                value={
                  run.accuracy.sample_accuracy_pct === null
                    ? '—'
                    : `${String(run.accuracy.sample_accuracy_pct).replace('.', ',')}%`
                }
                note={
                  run.accuracy.recent_runs_ok === null
                    ? ''
                    : run.accuracy.recent_runs_ok
                      ? '4 lượt gần nhất ≥ 98%'
                      : 'chưa đủ 4 lượt ≥ 98%'
                }
                tone={run.accuracy.recent_runs_ok ? 'success' : undefined}
              />
              <Stat
                label="Cổng duyệt"
                value={GATE[run.accuracy.gate]}
                note={run.accuracy.gate_note}
              />
            </div>
            <p className="text-muted-foreground da-caption">
              Lượt chỉ phát hành khi không còn mục chặn (BR-153). Giá trị bạn sửa được lưu làm ví dụ
              để agent trích tốt hơn ở lượt sau.
            </p>
          </section>
        </div>
      </div>
      {bulkOpen && (
        <BulkDialog
          run={run}
          reasons={FILTERS.concat(['source_anomaly', 'missing_required']).filter(
            (r) => count(r) > 0,
          )}
          onClose={() => setBulkOpen(false)}
          onDone={(n) => {
            toast({ tone: 'success', message: `Đã duyệt ${n} bản ghi` });
            setBulkOpen(false);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function RecordCard({
  record,
  runLabel,
  onDone,
}: {
  record: ExtractedRecord;
  runLabel: string;
  onDone: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(record.values.map((v) => [v.field, v.value])),
  );
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const decide = async (
    decision: 'confirm' | 'correct' | 'reject',
    corrected?: Record<string, string>,
  ) => {
    if (decision === 'reject' && !reason.trim()) {
      setError('Ghi lý do bỏ bản ghi.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await reviewRecord(record.id, {
        decision,
        corrected_values: corrected,
        reason: decision === 'reject' ? reason.trim() : undefined,
      });
      toast({
        tone: 'success',
        message:
          decision === 'reject'
            ? `Đã bỏ ${record.title}`
            : decision === 'correct'
              ? `Đã sửa ${record.title}`
              : `Đã xác nhận ${record.title}`,
      });
      setRejecting(false);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Bản ghi đang xem"
      className="flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl"
    >
      <h2 className="text-foreground da-heading-3">
        {record.title} · {runLabel}
      </h2>
      <div className="flex items-start gap-da-xl max-[1279px]:flex-col">
        <div className="flex w-[340px] shrink-0 flex-col gap-da-md rounded-sm bg-subtle p-da-lg max-[1279px]:w-full">
          {record.raw ? (
            <>
              <p className="text-muted-foreground da-mono">{record.raw.caption}</p>
              <table className="w-full border-collapse overflow-hidden rounded-md border border-border bg-card da-body-small">
                <thead>
                  <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                    {record.raw.columns.map((c) => (
                      <th key={c} className="px-da-lg py-da-md font-normal">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {record.raw.rows.map((row, i) => (
                    <tr
                      key={row.join('|')}
                      className={cn(
                        'border-b border-border last:border-b-0 text-foreground',
                        i === record.raw?.highlighted_row && 'bg-accent',
                      )}
                    >
                      {row.map((cell, j) => (
                        <td key={j} className="px-da-lg py-[10px]">
                          {cell}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
              {record.raw.footnote && (
                <p className="text-muted-foreground da-caption">{record.raw.footnote}</p>
              )}
            </>
          ) : (
            <p className="break-all text-muted-foreground da-mono">
              Bản gốc · {record.source_url ?? '—'}
            </p>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-[10px]">
          <table className="w-full border-collapse overflow-hidden rounded-md border border-border da-body-small">
            <thead>
              <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Trường</th>
                <th className="px-da-lg py-da-md font-normal">Giá trị trích</th>
                <th className="w-[68px] px-da-lg py-da-md font-normal">Độ tin</th>
              </tr>
            </thead>
            <tbody>
              {record.values.map((v) => (
                <tr
                  key={v.field}
                  className="border-b border-border last:border-b-0 text-foreground"
                >
                  <td className="px-da-lg py-[10px] da-mono">{v.field}</td>
                  <td className="px-da-lg py-[6px]">
                    {editing ? (
                      <input
                        aria-label={`Giá trị ${v.field}`}
                        value={values[v.field] ?? ''}
                        onChange={(e) => setValues({ ...values, [v.field]: e.target.value })}
                        className="w-full rounded-sm border border-border-strong bg-card px-da-md py-da-xs da-body-small"
                      />
                    ) : (
                      v.value
                    )}
                  </td>
                  <td className="px-da-lg py-[10px]">{fmt(v.confidence)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {record.evidence && <StatusDot tone="warning">{record.evidence}</StatusDot>}
          {record.agent_note && (
            <p className="text-foreground da-body-small">Agent: {record.agent_note}</p>
          )}
        </div>
      </div>
      {error && !rejecting && <Notice tone="error">{error}</Notice>}
      <div className="flex justify-end gap-da-md">
        {editing ? (
          <>
            <Button kind="ghost" disabled={busy} onClick={() => setEditing(false)}>
              Huỷ sửa
            </Button>
            <Button kind="primary" loading={busy} onClick={() => decide('correct', values)}>
              Lưu giá trị sửa
            </Button>
          </>
        ) : (
          <>
            <Button disabled={busy} onClick={() => setRejecting(true)}>
              Bỏ bản ghi
            </Button>
            <Button disabled={busy} onClick={() => setEditing(true)}>
              Sửa giá trị
            </Button>
            <Button
              kind="primary"
              loading={busy}
              onClick={() =>
                record.proposed_values
                  ? decide('correct', { ...values, ...record.proposed_values })
                  : decide('confirm')
              }
            >
              {record.proposed_values ? 'Xác nhận theo đề xuất' : 'Xác nhận'}
            </Button>
          </>
        )}
      </div>
      <ConfirmDialog
        open={rejecting}
        title={`Bỏ bản ghi ${record.title}?`}
        confirmLabel="Bỏ bản ghi"
        cancelLabel="Quay lại"
        loading={busy}
        error={rejecting ? error : undefined}
        onCancel={() => {
          setRejecting(false);
          setError(undefined);
        }}
        onConfirm={() => decide('reject')}
      >
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Lý do (bắt buộc)</span>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={3}
            className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
          />
        </label>
      </ConfirmDialog>
    </section>
  );
}

function BulkDialog({
  run,
  reasons,
  onClose,
  onDone,
}: {
  run: CrawlRun;
  reasons: ReviewReason[];
  onClose: () => void;
  onDone: (updated: number) => void;
}) {
  const [reasonKind, setReasonKind] = useState<ReviewReason | null>(reasons[0] ?? null);
  const [decision, setDecision] = useState<'confirm' | 'reject'>('confirm');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    if (!reasonKind || !note.trim()) {
      setError('Chọn lý do lọc và ghi lý do quyết định.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const { updated } = await bulkReviewRecords({
        run_id: run.id,
        review_reasons: [reasonKind],
        decision,
        reason: note.trim(),
      });
      onDone(updated);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open
      title="Duyệt hàng loạt theo lý do"
      confirmLabel="Áp dụng"
      cancelLabel="Huỷ"
      tone="primary"
      size="md"
      loading={busy}
      error={error}
      onCancel={onClose}
      onConfirm={submit}
    >
      <div className="flex flex-col gap-da-md">
        <div className="flex flex-col gap-da-xs">
          <span className="text-muted-foreground da-caption">Bản ghi có lý do</span>
          <Select<ReviewReason>
            label="Lý do lọc"
            value={reasonKind}
            onChange={setReasonKind}
            options={reasons.map((r) => ({ value: r, label: REASON[r] }))}
          />
        </div>
        <div role="radiogroup" aria-label="Quyết định" className="flex gap-da-xl">
          <Radio
            name="bulk"
            value="confirm"
            checked={decision === 'confirm'}
            onChange={() => setDecision('confirm')}
          >
            Xác nhận
          </Radio>
          <Radio
            name="bulk"
            value="reject"
            checked={decision === 'reject'}
            onChange={() => setDecision('reject')}
          >
            Bỏ
          </Radio>
        </div>
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Lý do (bắt buộc)</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
          />
        </label>
      </div>
    </ConfirmDialog>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'success';
}) {
  return (
    <div className="flex min-w-[160px] flex-1 flex-col gap-[6px] rounded-md border border-border bg-card px-da-xl py-[14px]">
      <p className="text-muted-foreground da-caption">{label}</p>
      <p className="text-foreground da-metric">{value}</p>
      <p className={cn('da-caption', tone === 'success' ? 'text-success' : 'text-foreground')}>
        {note}
      </p>
    </div>
  );
}
