import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { FilterChips } from '../../design-system/FilterChips';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import { cn } from '../../lib/utils';
import {
  createErrorBookEntry,
  dismissEntry,
  ERROR_KINDS,
  getErrorBookEntry,
  listErrorBookEntries,
  markEntryFixed,
  proposeEntryToPack,
  SOURCE_LABEL,
  updateErrorBookEntry,
  type EntryStatus,
  type ErrorBookEntry,
  type ErrorBookSummary,
  type ErrorKind,
  type SourceKind,
} from '../../lib/v1/errorBook';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Error Book" (2419:15027, SCR-69, UC-07-14): every confirmed wrong answer and forecast miss
 * of the Data Space, filtered by status, kind, source, data product and handler; beside it one
 * entry — the wrong and the right number, its receipt and replay, the error kind and root cause,
 * the fix linked to its proposal, the regression case and its CI run. An entry is fixed only with a
 * kind, a root cause and an accepted fix, and gets its regression case then; a fix that holds for
 * every customer of the data goes to the industry pack without the customer's data (BR-179).
 */
type DialogKind = 'fix' | 'dismiss' | 'pack' | null;

const STATUS: Record<
  EntryStatus,
  { label: string; tone: 'warning' | 'info' | 'success' | 'neutral' }
> = {
  open: { label: 'Mở', tone: 'warning' },
  fixed: { label: 'Đã sửa', tone: 'info' },
  regression_added: { label: 'Đã có hồi quy', tone: 'success' },
  dismissed: { label: 'Bỏ qua', tone: 'neutral' },
};
const KIND_LABEL = Object.fromEntries(ERROR_KINDS.map((k) => [k.value, k.label])) as Record<
  ErrorKind,
  string
>;
const ageDays = (iso: string) =>
  Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 86_400_000));

export default function ErrorBookPage() {
  const { routeId } = useAppState();
  const toast = useToast();
  const entries = useV1(['listErrorBookEntries'], () => listErrorBookEntries());
  const [picked, setPicked] = useState<string | undefined>(routeId || undefined);
  const [status, setStatus] = useState<EntryStatus | 'all'>('all');
  const [kind, setKind] = useState<string>('all');
  const [source, setSource] = useState<string>('all');
  const [product, setProduct] = useState<string>('all');
  const [assignee, setAssignee] = useState<string>('all');
  const [creating, setCreating] = useState(false);
  const items = entries.data?.items ?? [];
  const shown = items.filter(
    (e) =>
      (status === 'all' || e.status === status) &&
      (kind === 'all' || e.error_kind === kind) &&
      (source === 'all' || e.source_kind === source) &&
      (product === 'all' || e.data_product_name === product) &&
      (assignee === 'all' || e.assignee_name === assignee),
  );
  const id = picked ?? shown[0]?.id;
  const count = (s: EntryStatus) => items.filter((e) => e.status === s).length;
  const distinct = (values: (string | null)[]) => [...new Set(values.filter(Boolean) as string[])];

  const exportCsv = () => {
    const head = [
      'Mã',
      'Trạng thái',
      'Loại lỗi',
      'Nguồn',
      'Data product',
      'Người xử lý',
      'Tiêu đề',
    ];
    const rows = shown.map((e) => [
      e.code,
      STATUS[e.status].label,
      e.error_kind ? KIND_LABEL[e.error_kind] : '',
      SOURCE_LABEL[e.source_kind],
      e.data_product_name ?? '',
      e.assignee_name,
      e.title,
    ]);
    const csv = [head, ...rows].map((r) => r.map(csvCell).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'error-book.csv';
    a.click();
    URL.revokeObjectURL(url);
    toast({ tone: 'success', message: `Đã xuất ${rows.length} mục` });
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Error Book']}
        title="Error Book"
        description={`${count('open')} mục mở · mỗi mục sửa xong có ca hồi quy chạy trong CI (BR-179)`}
        actions={
          <>
            <Button disabled={shown.length === 0} onClick={exportCsv}>
              Xuất
            </Button>
            <Button kind="primary" onClick={() => setCreating(true)}>
              Tạo mục
            </Button>
          </>
        }
      />
      <div className="flex items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-lg max-[1279px]:flex-col">
        <div className="flex w-[540px] shrink-0 flex-col gap-da-md max-[1279px]:w-full">
          <FilterChips<EntryStatus | 'all'>
            label="Trạng thái"
            value={status}
            onChange={setStatus}
            items={[
              { value: 'all', label: `Tất cả ${items.length}` },
              { value: 'open', label: `Mở ${count('open')}` },
              { value: 'fixed', label: `Đã sửa ${count('fixed')}` },
              { value: 'regression_added', label: `Đã có hồi quy ${count('regression_added')}` },
              { value: 'dismissed', label: `Bỏ qua ${count('dismissed')}` },
            ]}
          />
          <div className="grid grid-cols-4 gap-da-sm max-[767px]:grid-cols-2">
            <Select
              size="sm"
              label="Loại lỗi"
              value={kind}
              onChange={setKind}
              options={[{ value: 'all', label: 'Loại lỗi: tất cả' }, ...ERROR_KINDS]}
            />
            <Select
              size="sm"
              label="Nguồn"
              value={source}
              onChange={setSource}
              options={[
                { value: 'all', label: 'Nguồn: tất cả' },
                ...Object.entries(SOURCE_LABEL).map(([value, label]) => ({ value, label })),
              ]}
            />
            <Select
              size="sm"
              label="Data product"
              value={product}
              onChange={setProduct}
              options={[
                { value: 'all', label: 'Data product: tất cả' },
                ...distinct(items.map((e) => e.data_product_name)).map((v) => ({
                  value: v,
                  label: v,
                })),
              ]}
            />
            <Select
              size="sm"
              label="Người xử lý"
              value={assignee}
              onChange={setAssignee}
              options={[
                { value: 'all', label: 'Người xử lý: tất cả' },
                ...distinct(items.map((e) => e.assignee_name)).map((v) => ({ value: v, label: v })),
              ]}
            />
          </div>
          <AsyncRegion state={entries} what="Error Book" skeletonRows={5}>
            {() => (
              <ul aria-label="Mục Error Book" className="flex flex-col gap-da-sm">
                {shown.map((e) => (
                  <EntryCard
                    key={e.id}
                    entry={e}
                    selected={e.id === id}
                    onSelect={() => setPicked(e.id)}
                  />
                ))}
                {shown.length === 0 && (
                  <li className="text-muted-foreground da-body-small">Không có mục khớp bộ lọc.</li>
                )}
              </ul>
            )}
          </AsyncRegion>
        </div>
        <div className="min-w-0 flex-1 max-[1279px]:w-full">
          {id && <EntryView key={id} id={id} onChanged={entries.reload} />}
        </div>
      </div>
      <CreateDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(created) => {
          setCreating(false);
          entries.reload();
          setPicked(created.id);
          toast({ tone: 'success', message: `Đã tạo ${created.code}` });
        }}
      />
    </div>
  );
}

function EntryCard({
  entry,
  selected,
  onSelect,
}: {
  entry: ErrorBookSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = STATUS[entry.status];
  return (
    <li>
      <button
        type="button"
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
        className={cn(
          'flex w-full flex-col gap-da-xs rounded-md border px-da-md py-da-sm text-left',
          selected
            ? 'border-primary bg-accent'
            : 'border-border bg-card hover:border-border-strong',
        )}
      >
        <span className="flex items-center gap-da-sm">
          <span className="text-foreground da-mono">{entry.code}</span>
          <Badge tone={status.tone}>{status.label}</Badge>
          <span className="ml-auto text-muted-foreground da-caption">
            {ageDays(entry.created_at)} ngày
          </span>
        </span>
        <span className="text-foreground da-body-small">{entry.title}</span>
        <span className="text-muted-foreground da-caption">
          {[
            entry.error_kind ? KIND_LABEL[entry.error_kind] : null,
            SOURCE_LABEL[entry.source_kind],
            entry.data_product_name,
            entry.assignee_name,
          ]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </button>
    </li>
  );
}

function EntryView({ id, onChanged }: { id: string; onChanged: () => void }) {
  const entry = useV1(['getErrorBookEntry', id], () => getErrorBookEntry(id));
  return (
    <AsyncRegion state={entry} what="mục Error Book">
      {(e) => (
        <EntryBody
          key={`${e.id}-${e.status}`}
          entry={e}
          onChanged={() => {
            entry.reload();
            onChanged();
          }}
        />
      )}
    </AsyncRegion>
  );
}

function EntryBody({ entry, onChanged }: { entry: ErrorBookEntry; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [kind, setKind] = useState<ErrorKind | null>(entry.error_kind);
  const [cause, setCause] = useState(entry.root_cause ?? '');
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [fixRef, setFixRef] = useState('');
  const [reason, setReason] = useState('');
  const [summary, setSummary] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const open = entry.status === 'open';
  const dirty = kind !== entry.error_kind || cause !== (entry.root_cause ?? '');
  const status = STATUS[entry.status];

  const act = async (key: string, call: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      setDialog(null);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-label={`Mục ${entry.code}`}
      className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-xl"
    >
      <div className="flex flex-wrap items-center gap-da-sm">
        <span className="text-foreground da-mono">{entry.code}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {entry.error_kind && <Badge tone="neutral">{KIND_LABEL[entry.error_kind]}</Badge>}
        <span className="ml-auto text-muted-foreground da-caption">
          Người xử lý: {entry.assignee_name}
        </span>
      </div>
      <h2 className="text-foreground da-heading-2">{entry.title}</h2>
      {entry.timeline.length > 0 && (
        <ol aria-label="Tiến trình" className="flex flex-wrap items-center gap-da-sm da-body-small">
          {entry.timeline.map((t, i) => (
            <li key={t.label} className="flex items-center gap-da-sm">
              {i > 0 && (
                <span aria-hidden className="text-muted-foreground">
                  →
                </span>
              )}
              <StatusDot tone="success">
                {t.label} {t.at}
              </StatusDot>
            </li>
          ))}
        </ol>
      )}
      {(entry.wrong || entry.right) && (
        <div className="grid grid-cols-2 gap-da-sm">
          {entry.wrong && (
            <div className="flex flex-col gap-[2px] rounded-md bg-subtle px-da-md py-da-sm">
              <span className="text-muted-foreground da-caption">Số trong câu trả lời</span>
              <span className="text-error da-metric">{entry.wrong.value}</span>
              <span className="text-muted-foreground da-caption">{entry.wrong.note}</span>
            </div>
          )}
          {entry.right && (
            <div className="flex flex-col gap-[2px] rounded-md bg-subtle px-da-md py-da-sm">
              <span className="text-muted-foreground da-caption">Số đúng</span>
              <span className="text-success da-metric">{entry.right.value}</span>
              <span className="text-muted-foreground da-caption">{entry.right.note}</span>
            </div>
          )}
        </div>
      )}
      {error && !dialog && <Notice tone="error">Không thực hiện được: {error}</Notice>}
      <dl className="grid grid-cols-[132px_1fr] gap-x-da-md gap-y-da-md da-body-small">
        {entry.receipt && (
          <>
            <dt className="text-muted-foreground">Biên nhận</dt>
            <dd className="text-foreground">
              <span className="text-info">{entry.receipt.code}</span> · {entry.receipt.replay_text}
            </dd>
          </>
        )}
        <dt className="text-muted-foreground">Loại lỗi</dt>
        <dd>
          {open ? (
            <div className="w-[240px]">
              <Select<ErrorKind>
                label="Loại lỗi"
                value={kind}
                placeholder="Chọn 1 trong 7 loại"
                onChange={setKind}
                options={ERROR_KINDS}
              />
            </div>
          ) : (
            <span className="text-foreground">
              {kind ? `${KIND_LABEL[kind]} · 1 trong 7 loại` : '—'}
            </span>
          )}
        </dd>
        <dt className="text-muted-foreground">Nguyên nhân gốc</dt>
        <dd>
          {open ? (
            <Textarea
              aria-label="Nguyên nhân gốc"
              value={cause}
              onChange={(e) => setCause(e.target.value)}
              rows={2}
            />
          ) : (
            <span className="text-foreground">{entry.root_cause ?? '—'}</span>
          )}
        </dd>
        <dt className="text-muted-foreground">Cách sửa</dt>
        <dd className="text-foreground">
          {entry.fix ? (
            <span className="flex flex-wrap items-center gap-da-sm">
              <span className="text-info">{entry.fix.label}</span>· {entry.fix.ref}
              {entry.fix.proposal_status === 'accepted' && (
                <Badge tone="success">Đã chấp nhận</Badge>
              )}
            </span>
          ) : (
            '—'
          )}
        </dd>
        <dt className="text-muted-foreground">Ca hồi quy</dt>
        <dd className="text-foreground">
          {entry.regression ? (
            <span className="flex flex-col gap-[2px]">
              <span>
                {entry.regression.code} · {entry.regression.text}
              </span>
              <StatusDot
                tone={
                  entry.regression.passed
                    ? 'success'
                    : entry.regression.passed === false
                      ? 'error'
                      : 'idle'
                }
              >
                {entry.regression.ci_text}
              </StatusDot>
            </span>
          ) : (
            'Tự tạo khi đánh dấu đã sửa'
          )}
        </dd>
        {entry.dismissed_reason && (
          <>
            <dt className="text-muted-foreground">Lý do bỏ qua</dt>
            <dd className="text-foreground">{entry.dismissed_reason}</dd>
          </>
        )}
        <dt className="text-muted-foreground">Lịch sử</dt>
        <dd className="text-muted-foreground da-caption">{entry.history_text}</dd>
      </dl>
      <div className="flex flex-wrap justify-end gap-da-sm">
        {open && (
          <>
            <Button
              disabled={busy !== null || !dirty || !kind}
              loading={busy === 'save'}
              onClick={() =>
                act(
                  'save',
                  () =>
                    updateErrorBookEntry(entry.id, {
                      error_kind: kind ?? undefined,
                      root_cause: cause.trim(),
                    }),
                  'Đã lưu loại lỗi và nguyên nhân',
                )
              }
            >
              Lưu
            </Button>
            <Button disabled={busy !== null} onClick={() => setDialog('dismiss')}>
              Bỏ qua
            </Button>
            <Button
              kind="primary"
              disabled={busy !== null || dirty}
              onClick={() => setDialog('fix')}
            >
              Đánh dấu đã sửa
            </Button>
          </>
        )}
        {!open && entry.status !== 'dismissed' && (
          <>
            {entry.receipt && (
              <Button onClick={() => navigate('analysis', 'RECEIPT', entry.receipt!.answer_id)}>
                Mở biên nhận
              </Button>
            )}
            {entry.pack_candidate && (
              <Button kind="primary" disabled={busy !== null} onClick={() => setDialog('pack')}>
                Đề xuất cho gói ngành
              </Button>
            )}
          </>
        )}
      </div>
      {entry.pack_candidate && !open && (
        <p className="text-muted-foreground da-caption">
          Cách sửa dùng chung cho mọi khách dùng dữ liệu này: đề xuất đã bỏ dữ liệu của khách, gửi
          người biên soạn gói ngành (UC-09-05).
        </p>
      )}
      <Dialog
        open={dialog === 'fix'}
        size="sm"
        title={`Đánh dấu ${entry.code} đã sửa`}
        description="Cần loại lỗi, nguyên nhân gốc và một cách sửa: đề xuất đã chấp nhận hoặc tham chiếu commit, model_config, dataset. Ca hồi quy được tạo ngay sau đó."
        submitLabel="Đánh dấu đã sửa"
        submitting={busy === 'fix'}
        canSubmit={fixRef.trim() !== ''}
        error={dialog === 'fix' ? error : undefined}
        onClose={() => setDialog(null)}
        onSubmit={() => {
          const ref = fixRef.trim();
          const isProposal = /^PR-\d+$/i.test(ref);
          act(
            'fix',
            () =>
              markEntryFixed(entry.id, {
                fix_kind: isProposal ? 'proposal' : 'commit',
                ...(isProposal ? { fix_proposal_id: ref.toUpperCase() } : { fix_ref: ref }),
              }),
            `${entry.code} đã sửa; ca hồi quy chờ lượt CI kế tiếp`,
          );
        }}
      >
        <label className="flex flex-col gap-da-xs">
          <span className="text-foreground da-label">Đề xuất (PR-…) hoặc tham chiếu</span>
          <Input value={fixRef} onChange={(e) => setFixRef(e.target.value)} placeholder="PR-0318" />
        </label>
      </Dialog>
      <Dialog
        open={dialog === 'dismiss'}
        size="sm"
        title={`Bỏ qua ${entry.code}?`}
        description="Không phải lỗi hoặc trùng mục khác."
        submitLabel="Bỏ qua"
        submitting={busy === 'dismiss'}
        canSubmit={reason.trim() !== ''}
        error={dialog === 'dismiss' ? error : undefined}
        onClose={() => setDialog(null)}
        onSubmit={() =>
          act(
            'dismiss',
            () => dismissEntry(entry.id, { reason: reason.trim() }),
            `Đã bỏ qua ${entry.code}`,
          )
        }
      >
        <Textarea
          aria-label="Lý do"
          placeholder="Lý do, hoặc mã mục gốc nếu trùng"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          rows={3}
        />
      </Dialog>
      <Dialog
        open={dialog === 'pack'}
        size="sm"
        title="Đề xuất cho gói ngành"
        description={
          entry.pack_candidate
            ? `Gửi ${entry.pack_candidate.pack_name}; chỉ gồm cách sửa và ca kiểm thử đã bỏ dữ liệu khách.`
            : undefined
        }
        submitLabel="Gửi đề xuất"
        submitting={busy === 'pack'}
        canSubmit={summary.trim() !== ''}
        error={dialog === 'pack' ? error : undefined}
        onClose={() => setDialog(null)}
        onSubmit={() =>
          entry.pack_candidate &&
          act(
            'pack',
            () =>
              proposeEntryToPack(entry.id, {
                pack_id: entry.pack_candidate!.pack_id,
                summary: summary.trim(),
              }),
            'Đã gửi đề xuất cho người biên soạn gói',
          )
        }
      >
        <Textarea
          aria-label="Tóm tắt cách sửa"
          placeholder="Tóm tắt cách sửa cho người biên soạn gói"
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
          rows={3}
        />
      </Dialog>
    </section>
  );
}

const SOURCES = Object.entries(SOURCE_LABEL).map(([value, label]) => ({
  value: value as SourceKind,
  label,
}));

function CreateDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (entry: ErrorBookEntry) => void;
}) {
  const [source, setSource] = useState<SourceKind | null>(null);
  const [title, setTitle] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  return (
    <Dialog
      open={open}
      size="sm"
      title="Tạo mục Error Book"
      description="Mỗi lỗi đã xác nhận tạo đúng một mục; cùng nguồn đã có mục thì mở mục có sẵn (BR-179)."
      submitLabel="Tạo mục"
      submitting={busy}
      canSubmit={source !== null && title.trim() !== ''}
      error={error}
      onClose={onClose}
      onSubmit={async () => {
        if (!source) return;
        setBusy(true);
        setError(undefined);
        try {
          onCreated(await createErrorBookEntry({ source_kind: source, title: title.trim() }));
          setTitle('');
          setSource(null);
        } catch (e) {
          setError((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="flex flex-col gap-da-md">
        <Select<SourceKind>
          label="Nguồn lỗi"
          value={source}
          placeholder="Nguồn lỗi"
          onChange={setSource}
          options={SOURCES}
        />
        <Input
          aria-label="Tiêu đề"
          placeholder="Tiêu đề: số sai và số đúng"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </div>
    </Dialog>
  );
}
