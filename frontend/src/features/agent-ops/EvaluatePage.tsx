import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber } from '../../lib/formatVN';
import {
  getRollout,
  holdRollout,
  listRollouts,
  rollbackRollout,
  setRolloutShare,
  type Rollout,
} from '../../lib/v1/agentOps';
import { useV1 } from '../../lib/v1/useV1';
import { AddCaseDialog } from './AddCaseDialog';
import { formatMeasure, formatThreshold, gateOf, rolloutStatus } from './format';

/*
 * "P13 · Đánh giá & triển khai" (2311:20138, UC-13-04/05): a new config version of one agent
 * against the one it may replace — first on the eval set, then side by side on live traffic —
 * and the three decisions: roll back, hold the share, or widen it. Widening needs the quality
 * gate (every eval criterion within its threshold) and goes through "Xác nhận · rollout"
 * (2094:8378); rolling back through "Xác nhận · rollback" (2094:8361). Both toasts
 * (2094:8040, 2094:8053) can undo by restoring the previous share.
 *
 * /agents/evaluate/{agent_id} opens one agent; without an id, the first agent in canary.
 */
type Dialog = 'widen' | 'rollback' | null;

export default function EvaluatePage() {
  const { routeId } = useAppState();
  const toast = useToast();
  const loaded = useV1<Rollout | null>(
    () => (routeId ? getRollout(routeId) : listRollouts().then((p) => p.items[0] ?? null)),
    [routeId],
  );
  // Actions answer with the new rollout; keep it rather than refetch, because the list behind
  // the id-less view drops an agent once it leaves canary.
  const [current, setCurrent] = useState<Rollout | null>();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [adding, setAdding] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

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

  const undoTo = (r: Rollout, pct: number | null) => () => {
    if (pct === null) return;
    void act(
      () => setRolloutShare(r.agent_id, pct),
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
              description="Khi một agent có phiên bản mới, nó chạy bộ đánh giá rồi nhận một phần lượt chạy thật trước khi thay phiên bản cũ."
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
                setDialog('rollback');
              }}
              onHold={() =>
                act(
                  () => holdRollout(r.agent_id),
                  (next) =>
                    toast({
                      tone: 'success',
                      message: `Giữ v${next.candidate_version} ở ${next.candidate_share_pct}% lượt chạy`,
                    }),
                )
              }
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
          onCancel={() => setDialog(null)}
          onWiden={(r, pct) =>
            act(
              () => setRolloutShare(r.agent_id, pct),
              (next) =>
                toast({
                  tone: 'success',
                  message: `v${next.candidate_version} đang nhận ${next.candidate_share_pct}% lượt chạy`,
                  action: { label: 'Lùi lại', onClick: undoTo(next, next.previous_share_pct) },
                }),
            )
          }
          onRollback={(r) =>
            act(
              () => rollbackRollout(r.agent_id),
              (next) =>
                toast({
                  tone: 'success',
                  message: `Đã lùi về v${next.baseline_version} · v${next.candidate_version} ngừng nhận lượt mới`,
                  action: { label: 'Hoàn tác', onClick: undoTo(next, next.previous_share_pct) },
                }),
            )
          }
          onCloseAdd={() => setAdding(false)}
          onAdded={() => {
            setAdding(false);
            const r = state.data;
            if (r)
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
  onHold,
  onAddCase,
}: {
  rollout: Rollout;
  sending: boolean;
  error?: string;
  onWiden: () => void;
  onRollback: () => void;
  onHold: () => void;
  onAddCase: () => void;
}) {
  const status = rolloutStatus(r);
  const gate = gateOf(r);
  const live = r.status === 'canary' || r.status === 'held';
  const baseShare = 100 - r.candidate_share_pct;

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
          live
            ? `${r.change_summary} · đang chạy ${r.candidate_share_pct}% lượt cùng v${r.baseline_version}`
            : r.change_summary
        }
        actions={
          r.status !== 'rolled_back' && (
            <>
              <Button kind="ghost" onClick={onRollback}>
                Quay lại v{r.baseline_version}
              </Button>
              {live && (
                <Button loading={sending} disabled={r.status === 'held'} onClick={onHold}>
                  Giữ {r.candidate_share_pct}%
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
          <div className="flex items-center gap-da-md">
            <h2 className="flex-1 text-foreground da-heading-3">
              Bộ đánh giá · {formatNumber(r.eval_set.case_count)} tình huống
            </h2>
            <Button size="sm" kind="ghost" icon="plus" onClick={onAddCase}>
              Thêm tình huống
            </Button>
          </div>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Tiêu chí</th>
                <th className="w-[100px] px-da-lg py-da-md font-normal">v{r.baseline_version}</th>
                <th className="w-[100px] px-da-lg py-da-md font-normal">v{r.candidate_version}</th>
                <th className="w-[100px] px-da-lg py-da-md font-normal">Ngưỡng</th>
              </tr>
            </thead>
            <tbody>
              {r.eval_set.criteria.map((c) => (
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

          <h2 className="text-foreground da-heading-3">Chạy thật song song</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Chỉ số {r.live.window_days} ngày</th>
                <th className="w-[120px] px-da-lg py-da-md font-normal">
                  v{r.baseline_version} · {baseShare}%
                </th>
                <th className="w-[120px] px-da-lg py-da-md font-normal">
                  v{r.candidate_version} · {r.candidate_share_pct}%
                </th>
              </tr>
            </thead>
            <tbody>
              {[
                ['Lượt chạy', r.live.baseline.runs, r.live.candidate.runs],
                [
                  'Cần người duyệt',
                  `${r.live.baseline.needs_approval_pct}%`,
                  `${r.live.candidate.needs_approval_pct}%`,
                ],
                ['Lỗi', r.live.baseline.errors, r.live.candidate.errors],
              ].map(([label, base, cand]) => (
                <tr key={label} className="border-b border-border text-foreground">
                  <td className="px-da-lg py-[9px] da-body-small">{label}</td>
                  <td className="px-da-lg py-[9px] da-mono">{base}</td>
                  <td className="px-da-lg py-[9px] da-mono">{cand}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Tự quay lui khi</h2>
          {r.auto_rollback_rules.map((rule) => (
            <p key={rule} className="text-foreground da-body-small">
              · {rule}
            </p>
          ))}
          <p className="text-muted-foreground da-body-small">
            Quay lui đưa toàn bộ lượt chạy về v{r.baseline_version} trong vài giây; lượt đang chạy
            của v{r.candidate_version} chạy nốt và được đánh dấu.
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
        v{r.candidate_version} ngừng nhận lượt mới; lượt đang chạy giữ phiên bản lúc bắt đầu. Kết
        quả bộ thử v{r.candidate_version} được giữ để xem lại.
      </ConfirmDialog>
      <AddCaseDialog
        open={adding}
        agentId={r.agent_id}
        evalSetId={r.eval_set.id}
        onClose={onCloseAdd}
        onAdded={onAdded}
      />
    </>
  );
}
