import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatDate, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listUsers } from '../../lib/v1/api';
import {
  getLatestLintRun,
  listOpenFindings,
  resolveFinding,
  runKnowledgeLint,
  type FindingAction,
  type FindingKind,
  type KnowledgeFinding,
} from '../../lib/v1/knowledge';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Rà soát tri thức" (2405:15078, UC-07-11): what the latest lint run found across the
 * knowledge pages. Four figures of the run, the open findings by kind, the table by severity, and
 * the selected finding with its evidence and the six fixed actions. The agent never edits a page
 * until a person picks one; "Bỏ qua" needs a reason and hides the finding for 90 days (BR-166).
 */
const KIND: Record<FindingKind, string> = {
  contradiction: 'Mâu thuẫn',
  expired_claim: 'Hết hiệu lực',
  decertified_source: 'Nguồn bị thu hồi chứng nhận',
  orphan: 'Mồ côi',
  metric_without_page: 'Chỉ số chưa có trang',
  undefined_term: 'Thuật ngữ chưa định nghĩa',
  stale_condition: 'Điều kiện đã cũ',
  policy_drift: 'Quyền đọc đã đổi',
};
const KIND_ORDER: FindingKind[] = [
  'contradiction',
  'expired_claim',
  'decertified_source',
  'orphan',
  'metric_without_page',
  'undefined_term',
  'stale_condition',
  'policy_drift',
];

const SEVERITY = {
  high: { label: 'Cao', tone: 'error' },
  medium: { label: 'TB', tone: 'warning' },
  low: { label: 'Thấp', tone: 'neutral' },
} as const;

const ACTIONS: { action: FindingAction; label: string }[] = [
  { action: 'propose_edit', label: 'Đề xuất sửa' },
  { action: 'mark_stale', label: 'Đánh dấu cũ' },
  { action: 'retire_page', label: 'Gỡ trang' },
  { action: 'create_page', label: 'Tạo trang' },
  { action: 'dismiss', label: 'Bỏ qua' },
  { action: 'reassign', label: 'Giao người khác' },
];

const DONE: Record<FindingAction, string> = {
  propose_edit: 'Đã tạo bản nháp sửa trang; gửi duyệt khi xong',
  mark_stale: 'Đã đánh dấu trang cũ',
  retire_page: 'Đã gỡ trang; trích dẫn cũ vẫn mở được',
  create_page: 'Đã giao agent soạn trang mới',
  dismiss: 'Đã bỏ qua; ẩn 90 ngày',
  reassign: 'Đã giao người khác',
};

type Filter = 'all' | FindingKind;

export default function KnowledgeReviewPage() {
  const run = useV1(['getLatestLintRun'], getLatestLintRun);
  const findings = useV1(['listOpenFindings'], listOpenFindings);
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>('all');
  const [selectedId, setSelectedId] = useState<string>();
  const [starting, setStarting] = useState(false);

  const latest = run.data?.items[0];
  const items = findings.data?.items ?? [];
  const shown = items.filter((f) => filter === 'all' || f.kind === filter);
  const selected =
    shown.find((f) => f.id === selectedId) ??
    shown.find((f) => f.kind === 'contradiction') ??
    shown[0];

  const lintNow = async () => {
    setStarting(true);
    try {
      await runKnowledgeLint();
      toast({ tone: 'info', message: 'Đang rà soát; phát hiện mới hiện ở đây khi xong.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy được: ${(e as Error).message}` });
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Rà soát tri thức']}
        title="Rà soát tri thức"
        description={
          latest
            ? [
                latest.finished_at &&
                  `Lượt gần nhất ${formatDate(latest.finished_at)} ${formatTime(latest.finished_at)}`,
                `${latest.page_count} trang`,
                `${items.length} phát hiện mở`,
                `${latest.skipped_source_pct}% nguồn bỏ qua nhờ mã băm`,
                latest.schedule_text,
              ]
                .filter(Boolean)
                .join(' · ')
            : undefined
        }
        actions={
          <>
            <Tooltip content="Màn đặt lịch rà soát đang được làm.">
              <Button disabled>Lịch rà soát</Button>
            </Tooltip>
            <Button kind="primary" loading={starting} onClick={lintNow}>
              Chạy rà soát ngay
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {latest && (
          <div className="flex gap-da-xl max-[1023px]:flex-wrap">
            <Stat
              label="Phát hiện mở"
              value={String(items.length)}
              note={`${items.filter((f) => f.severity === 'high').length} mức cao`}
              tone="error"
            />
            {/* PROPOSAL: the api's run does not count stale pages; the figure waits for it. */}
            {latest.stale_pages !== null && latest.page_count > 0 && (
              <Stat
                label="Trang cũ"
                value={`${((latest.stale_pages / latest.page_count) * 100).toFixed(1).replace('.', ',')}%`}
                note={`${latest.stale_pages} / ${latest.page_count} trang`}
              />
            )}
            <Stat
              label="Nguồn bỏ qua nhờ mã băm"
              value={`${latest.skipped_source_pct}%`}
              note="không đổi từ lượt trước"
              tone="success"
            />
            <Stat
              label="Thời gian lượt"
              value={latest.duration_minutes === null ? '—' : `${latest.duration_minutes} phút`}
              note={latest.limits_text}
            />
          </div>
        )}
        <div role="radiogroup" aria-label="Loại phát hiện" className="flex flex-wrap gap-[6px]">
          {(['all', ...KIND_ORDER] as Filter[]).map((k) => {
            const n = k === 'all' ? items.length : items.filter((f) => f.kind === k).length;
            return (
              <button
                key={k}
                type="button"
                role="radio"
                aria-checked={filter === k}
                onClick={() => setFilter(k)}
                className={cn(
                  'rounded-full px-da-md py-da-xs da-label',
                  filter === k ? 'bg-accent text-primary' : 'bg-subtle text-muted-foreground',
                )}
              >
                {k === 'all' ? 'Tất cả' : KIND[k]} {n}
              </button>
            );
          })}
        </div>
        <AsyncRegion
          state={findings}
          what="phát hiện"
          isEmpty={() => shown.length === 0}
          empty={<p className="text-muted-foreground da-body-small">Không có phát hiện nào mở.</p>}
        >
          {() => (
            <div className="flex items-start gap-da-xl max-[1279px]:flex-col">
              <FindingsTable findings={shown} onSelect={setSelectedId} selectedId={selected?.id} />
              {selected && (
                <FindingCard
                  key={selected.id}
                  finding={selected}
                  onDone={(action) => {
                    toast({ tone: 'success', message: DONE[action] });
                    setSelectedId(undefined);
                    findings.reload();
                  }}
                />
              )}
            </div>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}

function FindingsTable({
  findings,
  onSelect,
  selectedId,
}: {
  findings: KnowledgeFinding[];
  onSelect: (id: string) => void;
  selectedId: string | undefined;
}) {
  const view = useTableView(findings, {
    search: (row) => `${row.subject_text} ${row.detail} ${KIND[row.kind]}`,
    filters: [
      {
        id: 'severity',
        label: 'Mức',
        value: (row) => row.severity,
        text: (severity) => SEVERITY[severity as KnowledgeFinding['severity']].label,
      },
      { id: 'kind', label: 'Loại', value: (row) => row.kind, text: (kind) => KIND[kind as FindingKind] },
    ],
    sorts: [
      { id: 'severity', value: (row) => row.severity },
      { id: 'kind', value: (row) => KIND[row.kind] },
      { id: 'subject', value: (row) => row.subject_text },
      { id: 'detail', value: (row) => row.detail },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex min-w-0 flex-[1.6] flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm phát hiện…" searchLabel="Tìm phát hiện" />
      <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="severity" className="w-[80px] px-da-lg py-da-md font-normal">
              Mức
            </SortableTh>
            <SortableTh view={view} column="kind" className="w-[170px] px-da-lg py-da-md font-normal">
              Loại
            </SortableTh>
            <SortableTh view={view} column="subject" className="w-[190px] px-da-lg py-da-md font-normal">
              Trang
            </SortableTh>
            <SortableTh view={view} column="detail" className="px-da-lg py-da-md font-normal">
              Chi tiết
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((f) => (
            <tr
              key={f.id}
              aria-selected={f.id === selectedId}
              className={cn(
                'border-b border-border last:border-b-0 da-body-small',
                f.id === selectedId ? 'bg-accent' : 'hover:bg-subtle',
              )}
            >
              <td className="px-da-lg py-[10px]">
                <StatusDot tone={SEVERITY[f.severity].tone}>{SEVERITY[f.severity].label}</StatusDot>
              </td>
              <td className="px-da-lg py-[10px] text-foreground">{KIND[f.kind]}</td>
              <td className="px-da-lg py-[10px]">
                <button type="button" onClick={() => onSelect(f.id)} className="text-left text-foreground hover:underline">
                  {f.subject_text}
                </button>
              </td>
              <td className="px-da-lg py-[10px] text-foreground">{f.detail}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
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
  tone?: 'error' | 'success';
}) {
  return (
    <div className="flex min-w-[180px] flex-1 flex-col gap-[6px] rounded-md border border-border bg-card px-da-xl py-[14px]">
      <p className="text-muted-foreground da-caption">{label}</p>
      <p className="text-foreground da-metric">{value}</p>
      <p
        className={cn(
          'da-caption',
          tone === 'error' ? 'text-error' : tone === 'success' ? 'text-success' : 'text-foreground',
        )}
      >
        {note}
      </p>
    </div>
  );
}

function FindingCard({
  finding,
  onDone,
}: {
  finding: KnowledgeFinding;
  onDone: (action: FindingAction) => void;
}) {
  const [dialog, setDialog] = useState<'dismiss' | 'reassign' | null>(null);
  const [reason, setReason] = useState('');
  const [assignee, setAssignee] = useState<string | null>(null);
  const [busy, setBusy] = useState<FindingAction | null>(null);
  const [error, setError] = useState<string>();
  const users = useV1(['listUsers', dialog], dialog === 'reassign' ? listUsers : null);

  const act = async (action: FindingAction) => {
    if (action === 'dismiss' && !reason.trim()) {
      setError('Ghi lý do bỏ qua.');
      return;
    }
    if (action === 'reassign' && !assignee) {
      setError('Chọn người nhận.');
      return;
    }
    setBusy(action);
    setError(undefined);
    try {
      await resolveFinding(finding.id, {
        action,
        reason: action === 'dismiss' ? reason.trim() : undefined,
        assignee_id: action === 'reassign' ? (assignee ?? undefined) : undefined,
      });
      onDone(action);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <section
      aria-label="Phát hiện đang xem"
      className="flex w-full min-w-0 flex-1 flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl"
    >
      <h2 className="text-foreground da-heading-3">
        {KIND[finding.kind]} · {finding.subject_text}
      </h2>
      {finding.evidence.map((e) => (
        <div
          key={e.label}
          className="flex flex-col gap-[2px] rounded-sm bg-subtle px-[10px] py-da-md"
        >
          <p className="text-muted-foreground da-caption">{e.label}</p>
          <p className="text-foreground da-body-strong">{e.value}</p>
        </div>
      ))}
      {finding.evidence.length === 0 && (
        <p className="text-foreground da-body-small">{finding.detail}</p>
      )}
      {finding.suggestion && <p className="text-foreground da-caption">{finding.suggestion}</p>}
      {error && !dialog && <p className="text-error da-caption">{error}</p>}
      <div className="flex flex-wrap gap-[6px]">
        {ACTIONS.map(({ action, label }, i) => (
          <Button
            key={action}
            kind={i === 0 ? 'primary' : 'secondary'}
            loading={busy === action}
            disabled={busy !== null}
            onClick={() =>
              action === 'dismiss' || action === 'reassign' ? setDialog(action) : act(action)
            }
          >
            {label}
          </Button>
        ))}
      </div>
      <p className="text-muted-foreground da-caption">
        Sáu hành động cố định. Agent không tự sửa trang khi chưa có người chọn; Bỏ qua cần lý do và
        ẩn 90 ngày.
      </p>
      <ConfirmDialog
        open={dialog !== null}
        title={dialog === 'dismiss' ? 'Bỏ qua phát hiện?' : 'Giao phát hiện cho người khác'}
        confirmLabel={dialog === 'dismiss' ? 'Bỏ qua' : 'Giao'}
        cancelLabel="Quay lại"
        tone="primary"
        loading={busy !== null}
        error={dialog ? error : undefined}
        onCancel={() => {
          setDialog(null);
          setError(undefined);
        }}
        onConfirm={() => dialog && act(dialog)}
      >
        {dialog === 'dismiss' ? (
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Lý do (bắt buộc)</span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
            />
          </label>
        ) : (
          <Select
            label="Người nhận"
            value={assignee}
            onChange={setAssignee}
            placeholder="Chọn người…"
            options={(users.data?.items ?? [])
              .filter((u) => u.kind === 'person')
              .map((u) => ({ value: u.id, label: u.display_name }))}
          />
        )}
      </ConfirmDialog>
    </section>
  );
}
