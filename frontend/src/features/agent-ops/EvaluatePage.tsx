import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber } from '../../lib/formatVN';
import {
  getRollout,
  rollbackRollout,
  setRolloutShare,
  type LiveStats,
  type Rollout,
} from '../../lib/v1/agentOps';
import { useV1 } from '../../lib/v1/useV1';
import { AddCaseDialog } from './AddCaseDialog';
import { formatMeasure, formatThreshold, gateOf, rolloutStatus } from './format';

/*
 * "P13 · Đánh giá & triển khai" (2311:20138, UC-13-04/05): a new config version of one agent
 * against the one it may replace — first on the eval set, then side by side on live traffic —
 * and the two decisions: roll back (with a reason) or widen the share. Widening needs the quality
 * gate (every eval criterion with a threshold within it) and goes through "Xác nhận · rollout"
 * (2094:8378); rolling back through "Xác nhận · rollback" (2094:8361). The widen toast (2094:8040)
 * can undo by restoring the previous share; a rollback ends the deployment and is not undone.
 * Holding the share is doing nothing: the quality gate decides the next step (UC-13-05 step 4).
 *
 * /agents/evaluate/{agent_id} opens one agent's deployment in progress; the screen is opened
 * from an agent, so without an id it says so.
 */
type Dialog = 'widen' | 'rollback' | null;

export default function EvaluatePage() {
  const { routeId } = useAppState();
  const toast = useToast();
  const loaded = useV1<Rollout | null>(
    () => (routeId ? getRollout(routeId) : Promise.resolve(null)),
    [routeId],
  );
  // Actions answer with the new deployment; keep it rather than refetch, because the agent
  // drops its active deployment once it completes or is rolled back.
  const [current, setCurrent] = useState<Rollout | null>();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [adding, setAdding] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [reason, setReason] = useState('');

  useEffect(() => setCurrent(undefined), [routeId]);

  const state = { ...loaded, data: current !== undefined ? current : loaded.data };

  const act = async (run: () => Promise<Rollout>, done: (next: Rollout) => void) => {
    setSending(true);
    setError(undefined);
    try {
      const next = await run();
      setCurrent(next);
      setDialog(null);
      done(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const undoTo = (r: Rollout, pct: number) => () => {
    void act(
      () => setRolloutShare(r, pct),
      () => {},
    );
  };

  return (
    <div className="flex min-h-full flex-col">
      <AsyncRegion
        state={state}
        what="phiên bản đang thử"
        skeletonRows={6}
        isEmpty={(r) => r === null}
        empty={
          <div className="px-da-3xl pt-da-xl">
            <EmptyState
              icon="layers"
              title="Chưa có phiên bản nào đang thử"
              description={
                routeId
                  ? 'Agent này không có phiên bản nào đang triển khai. Khi có phiên bản mới, nó chạy bộ đánh giá rồi nhận một phần lượt chạy thật trước khi thay phiên bản cũ.'
                  : 'Mở Đánh giá & triển khai từ một agent trong Đội agent để xem phiên bản đang thử của nó.'
              }
            />
          </div>
        }
      >
        {(r) =>
          r && (
            <RolloutView
              rollout={r}
              sending={sending}
              onWiden={() => {
                setError(undefined);
                setDialog('widen');
              }}
              onRollback={() => {
                setError(undefined);
                setReason('');
                setDialog('rollback');
              }}
              onAddCase={() => setAdding(true)}
              error={dialog === null ? error : undefined}
            />
          )
        }
      </AsyncRegion>

      {state.data && (
        <Dialogs
          rollout={state.data}
          dialog={dialog}
          sending={sending}
          error={error}
          adding={adding}
          reason={reason}
          onReason={setReason}
          onCancel={() => setDialog(null)}
          onWiden={(r, pct) =>
            act(
              () => setRolloutShare(r, pct),
              (next) =>
                toast({
                  tone: 'success',
                  message: `v${next.candidate_version} đang nhận ${next.candidate_share_pct}% lượt chạy`,
                  // A completed deployment takes no traffic change, so 100% has no undo.
                  action:
                    next.status === 'rolling'
                      ? { label: 'Lùi lại', onClick: undoTo(next, r.candidate_share_pct) }
                      : undefined,
                }),
            )
          }
          onRollback={(r) => {
            if (!reason.trim()) {
              setError('Ghi lý do quay lui.');
              return;
            }
            void act(
              () => rollbackRollout(r, reason.trim()),
              (next) =>
                toast({
                  tone: 'success',
                  message:
                    next.baseline_version === null
                      ? `v${next.candidate_version} ngừng nhận lượt mới`
                      : `Đã lùi về v${next.baseline_version} · v${next.candidate_version} ngừng nhận lượt mới`,
                }),
            );
          }}
          onCloseAdd={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            const r = state.data;
            if (r?.eval_set)
              setCurrent({
                ...r,
                eval_set: { ...r.eval_set, case_count: r.eval_set.case_count + 1 },
              });
            toast({ tone: 'success', message: 'Đã thêm tình huống vào bộ thử' });
          }}
        />
      )}
    </div>
  );
}

function RolloutView({
  rollout: r,
  sending,
  error,
  onWiden,
  onRollback,
  onAddCase,
}: {
  rollout: Rollout;
  sending: boolean;
  error?: string;
  onWiden: () => void;
  onRollback: () => void;
  onAddCase: () => void;
}) {
  const status = rolloutStatus(r);
  const gate = gateOf(r);
  const live = r.status === 'rolling';
  const baseShare = 100 - r.candidate_share_pct;
  const baseline = r.baseline_version === null ? 'bản cũ' : `v${r.baseline_version}`;

  const widen = r.next_share_pct !== null && (
    <Button kind="primary" disabled={!gate.open} onClick={onWiden}>
      Tăng lên {r.next_share_pct}%
    </Button>
  );

  return (
    <>
      <PageHeader
        bordered
        breadcrumb={['Agents', r.agent_name, 'Phiên bản']}
        title={
          <span className="flex items-center gap-[10px]">
            {r.agent_name} · v{r.candidate_version}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={
          [r.change_summary, live && `đang chạy ${r.candidate_share_pct}% lượt cùng ${baseline}`]
            .filter(Boolean)
            .join(' · ') || undefined
        }
        actions={
          live && (
            <>
              {r.baseline_version !== null && (
                <Button kind="ghost" loading={sending} onClick={onRollback}>
                  Quay lại v{r.baseline_version}
                </Button>
              )}
              {widen &&
                (gate.open ? (
                  widen
                ) : (
                  <Tooltip
                    content={`Cổng chất lượng mới đạt ${gate.passed}/${gate.total}; chưa thể tăng.`}
                  >
                    {widen}
                  </Tooltip>
                ))}
            </>
          )
        }
      />
      {error && (
        <p role="alert" className="px-da-3xl pt-da-md text-error da-caption">
          {error}
        </p>
      )}
      <div className="flex flex-1 items-stretch">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] pb-da-3xl pl-da-3xl pr-da-2xl pt-da-xl">
          {r.eval_set ? (
            <EvalTable rollout={r} evalSet={r.eval_set} onAddCase={onAddCase} />
          ) : (
            <p className="text-muted-foreground da-body-small">
              v{r.candidate_version} chưa có kết quả bộ đánh giá.
            </p>
          )}

          <h2 className="text-foreground da-heading-3">Chạy thật song song</h2>
          {r.live ? (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">
                    Chỉ số{r.live.window_days !== null && ` ${r.live.window_days} ngày`}
                  </th>
                  <th className="w-[120px] px-da-lg py-da-md font-normal">
                    {baseline} · {baseShare}%
                  </th>
                  <th className="w-[120px] px-da-lg py-da-md font-normal">
                    v{r.candidate_version} · {r.candidate_share_pct}%
                  </th>
                </tr>
              </thead>
              <tbody>
                {LIVE_ROWS.map(([label, read]) => (
                  <tr key={label} className="border-b border-border text-foreground">
                    <td className="px-da-lg py-[9px] da-body-small">{label}</td>
                    <td className="px-da-lg py-[9px] da-mono">{read(r.live!.baseline)}</td>
                    <td className="px-da-lg py-[9px] da-mono">{read(r.live!.candidate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted-foreground da-body-small">
              Chưa có số đo lượt chạy thật trong cửa sổ quan sát.
            </p>
          )}
        </div>

        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Tự quay lui khi</h2>
          {r.auto_rollback_rules.map((rule) => (
            <p key={rule} className="text-foreground da-body-small">
              · {rule}
            </p>
          ))}
          <p className="text-muted-foreground da-body-small">
            Quay lui đưa toàn bộ lượt chạy về {baseline} trong vài giây; lượt đang chạy của v
            {r.candidate_version} chạy nốt và được đánh dấu.
          </p>
        </aside>
      </div>
    </>
  );
}

function Dialogs({
  rollout: r,
  dialog,
  sending,
  error,
  adding,
  reason,
  onReason,
  onCancel,
  onWiden,
  onRollback,
  onCloseAdd,
  onAdded,
}: {
  rollout: Rollout;
  dialog: Dialog;
  sending: boolean;
  error?: string;
  adding: boolean;
  reason: string;
  onReason: (reason: string) => void;
  onCancel: () => void;
  onWiden: (r: Rollout, pct: number) => void;
  onRollback: (r: Rollout) => void;
  onCloseAdd: () => void;
  onAdded: () => void;
}) {
  const gate = gateOf(r);
  const next = r.next_share_pct;
  return (
    <>
      <ConfirmDialog
        open={dialog === 'widen' && next !== null}
        tone="primary"
        title={`Tăng v${r.candidate_version} lên ${next}% lượt chạy?`}
        confirmLabel={`Tăng lên ${next}%`}
        loading={sending}
        error={error}
        onCancel={onCancel}
        onConfirm={() => next !== null && onWiden(r, next)}
      >
        Cổng chất lượng đã đạt {gate.passed}/{gate.total}. Điều kiện tự lùi vẫn áp dụng.
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === 'rollback'}
        title={`Lùi về v${r.baseline_version}?`}
        confirmLabel={`Lùi về v${r.baseline_version}`}
        loading={sending}
        error={error}
        onCancel={onCancel}
        onConfirm={() => onRollback(r)}
      >
        <span className="flex flex-col gap-da-sm">
          v{r.candidate_version} ngừng nhận lượt mới; lượt đang chạy giữ phiên bản lúc bắt đầu. Kết
          quả bộ thử v{r.candidate_version} được giữ để xem lại.
          <Textarea
            aria-label="Lý do quay lui"
            placeholder="Lý do quay lui (bắt buộc)"
            value={reason}
            onChange={(e) => onReason(e.target.value)}
            rows={2}
          />
        </span>
      </ConfirmDialog>
      {r.eval_set && (
        <AddCaseDialog
          open={adding}
          agentId={r.agent_id}
          evalSetId={r.eval_set.id}
          onClose={onCloseAdd}
          onAdded={onAdded}
        />
      )}
    </>
  );
}

const dash = (v: number | null, suffix = '') => (v === null ? '—' : `${formatNumber(v)}${suffix}`);

const LIVE_ROWS: [string, (s: LiveStats) => string][] = [
  ['Lượt chạy', (s) => dash(s.runs)],
  ['Cần người duyệt', (s) => dash(s.needs_approval_pct, '%')],
  ['Lỗi', (s) => dash(s.errors)],
];

function EvalTable({
  rollout: r,
  evalSet,
  onAddCase,
}: {
  rollout: Rollout;
  evalSet: NonNullable<Rollout['eval_set']>;
  onAddCase: () => void;
}) {
  return (
    <>
      <div className="flex items-center gap-da-md">
        <h2 className="flex-1 text-foreground da-heading-3">
          Bộ đánh giá · {formatNumber(evalSet.case_count)} tình huống
        </h2>
        <Button size="sm" kind="ghost" icon="plus" onClick={onAddCase}>
          Thêm tình huống
        </Button>
      </div>
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <th className="px-da-lg py-da-md font-normal">Tiêu chí</th>
            <th className="w-[100px] px-da-lg py-da-md font-normal">
              {r.baseline_version === null ? 'Bản cũ' : `v${r.baseline_version}`}
            </th>
            <th className="w-[100px] px-da-lg py-da-md font-normal">v{r.candidate_version}</th>
            <th className="w-[100px] px-da-lg py-da-md font-normal">Ngưỡng</th>
          </tr>
        </thead>
        <tbody>
          {evalSet.criteria.map((c) => (
            <tr key={c.key} className="border-b border-border">
              <td className="px-da-lg py-[9px] text-foreground da-body-small">{c.label}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                {formatMeasure(c.baseline, c.unit)}
              </td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">
                {formatMeasure(c.candidate, c.unit)}
              </td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                {formatThreshold(c)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
