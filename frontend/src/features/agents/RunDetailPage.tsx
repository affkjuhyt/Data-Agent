import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { RunStep } from '../../design-system/RunStep';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { controlRun, getRun } from '../../lib/v1/api';
import type { AgentRun, AgentStep } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { PLAN_STEP, runStatus } from './runStatus';

/*
 * "P13 · Lượt chạy · Đang làm gì" (2310:19989, UC-13-06): one run told as sentences
 * ("Spec · v3.1 · Kể việc của agent") — time in mono, what happened, the step under way in
 * Body Strong with its progress, what comes next in muted — and its plan on the right.
 * "Xem kỹ thuật" shows the steps (agent_step) instead. A live run refreshes every 5 s until the
 * event stream (GET /v1/runs/{id}/events) reaches the browser. Editing the plan needs the run
 * paused and the plan editor, which is a later screen.
 */
type View = 'simple' | 'technical';
const LIVE: AgentRun['status'][] = ['queued', 'planning', 'running', 'waiting_human'];
const POLL_MS = 5000;

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

export default function RunDetailPage() {
  const { routeId } = useAppState();
  const [view, setView] = useState<View>('simple');
  const run = useV1(routeId ? () => getRun(routeId, view) : null, [routeId, view]);

  const live = run.data ? LIVE.includes(run.data.status) : false;
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(run.reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, run.reload]);

  return (
    <AsyncRegion state={run} what="lượt chạy">
      {(r) => <RunBody run={r} view={view} onView={setView} onChanged={run.reload} />}
    </AsyncRegion>
  );
}

function RunBody({
  run,
  view,
  onView,
  onChanged,
}: {
  run: AgentRun;
  view: View;
  onView: (v: View) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState<'pause' | 'resume' | 'cancel' | null>(null);
  const [error, setError] = useState<string>();
  const status = runStatus(run);
  const open = LIVE.includes(run.status) || run.status === 'paused';
  const minutes = run.started_at
    ? Math.max(
        1,
        Math.round(
          ((run.finished_at ? new Date(run.finished_at).getTime() : Date.now()) -
            new Date(run.started_at).getTime()) /
            60000,
        ),
      )
    : null;

  const act = async (verb: 'pause' | 'resume' | 'cancel') => {
    setBusy(verb);
    setError(undefined);
    try {
      await controlRun(run.id, verb, verb === 'cancel' ? 'Người dùng huỷ' : undefined);
      toast({
        tone: 'success',
        message: {
          pause: `Đã tạm dừng ${run.code}`,
          resume: `${run.agent_name} chạy tiếp`,
          cancel: `Đã huỷ ${run.code}`,
        }[verb],
      });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      setConfirmCancel(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Lượt chạy', run.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {run.title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={[
          `${run.agent_name} v${run.config_version}`,
          run.started_at ? `bắt đầu ${hhmm(run.started_at)} ${run.trigger_text}` : null,
          minutes ? `${minutes} phút` : null,
          `${formatNumber(Number(run.cost))} đ`,
        ]
          .filter(Boolean)
          .join(' · ')}
        actions={
          open && (
            <>
              <Button kind="ghost" onClick={() => setConfirmCancel(true)}>
                Huỷ lượt chạy
              </Button>
              {run.status === 'paused' ? (
                <Button loading={busy === 'resume'} onClick={() => act('resume')}>
                  Chạy tiếp
                </Button>
              ) : (
                <Button loading={busy === 'pause'} onClick={() => act('pause')}>
                  Tạm dừng
                </Button>
              )}
              <Tooltip
                content={
                  run.status === 'paused'
                    ? 'Trình sửa kế hoạch đang được làm.'
                    : 'Tạm dừng lượt chạy trước khi sửa kế hoạch.'
                }
              >
                <Button kind="primary" disabled>
                  Sửa kế hoạch
                </Button>
              </Tooltip>
            </>
          )
        }
      />
      <div className="pl-da-3xl pt-[10px]">
        <Segmented<View>
          variant="outline"
          label="Cách xem"
          value={view}
          onChange={onView}
          items={[
            { value: 'simple', label: 'Xem đơn giản' },
            { value: 'technical', label: 'Xem kỹ thuật' },
          ]}
        />
      </div>
      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {error}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl pt-da-lg">
          {view === 'simple' ? <Story run={run} /> : <Steps steps={run.steps ?? []} />}
        </div>
        <aside className="flex w-[380px] shrink-0 flex-col border-l border-border bg-background px-da-2xl pt-da-xl max-[1279px]:hidden">
          {run.plan && run.plan.length > 0 && (
            <>
              <h2 className="text-foreground da-heading-3">Kế hoạch · {run.plan.length} bước</h2>
              <ol className="flex flex-col">
                {run.plan.map((step, i) => {
                  const look = PLAN_STEP[step.state];
                  return (
                    <li
                      key={step.title}
                      className="flex items-center gap-[10px] border-b border-border py-[9px]"
                    >
                      <span className="w-4 shrink-0 text-muted-foreground da-mono">{i + 1}</span>
                      <span className="min-w-0 flex-1 text-foreground da-body-small">
                        {step.title}
                      </span>
                      <StatusDot tone={look.tone}>{look.label}</StatusDot>
                    </li>
                  );
                })}
              </ol>
            </>
          )}
          <div className="flex flex-col gap-da-sm pt-[14px] text-muted-foreground">
            <p className="da-body-small">
              Bạn có thể sửa kế hoạch khi agent đang chạy: thêm bước, bỏ bước chưa làm, hoặc đổi
              điều kiện dừng. Bước đã xong không bị chạy lại.
            </p>
          </div>
        </aside>
      </div>
      <ConfirmDialog
        open={confirmCancel}
        title={`Huỷ lượt chạy ${run.code}?`}
        confirmLabel="Huỷ lượt chạy"
        cancelLabel="Không huỷ"
        loading={busy === 'cancel'}
        onCancel={() => setConfirmCancel(false)}
        onConfirm={() => act('cancel')}
      >
        {run.agent_name} dừng ở ranh giới bước hiện tại và không làm gì thêm. Kết quả trung gian
        được giữ 30 ngày.
      </ConfirmDialog>
    </div>
  );
}

function Story({ run }: { run: AgentRun }) {
  if (run.narrative.length === 0) {
    return (
      <p className="py-da-lg text-muted-foreground da-body-small">
        Lượt chạy này chưa có diễn biến để kể.
      </p>
    );
  }
  return (
    <ol className="flex flex-col">
      {run.narrative.map((event, i) => (
        <li
          key={`${event.at}-${i}`}
          className="flex items-start gap-da-xl border-b border-border py-da-lg"
        >
          <span
            className={cn(
              'w-[70px] shrink-0 da-mono',
              event.state === 'now' ? 'text-primary' : 'text-muted-foreground',
            )}
          >
            {event.state === 'now'
              ? 'bây giờ'
              : event.state === 'next'
                ? 'tiếp theo'
                : hhmm(event.at)}
          </span>
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <p
              className={cn(
                event.state === 'now' ? 'text-foreground da-body-strong' : 'da-body',
                event.state === 'next' ? 'text-muted-foreground' : 'text-foreground',
              )}
            >
              {event.text}
            </p>
            {event.state === 'now' && event.progress !== undefined && (
              <span
                role="progressbar"
                aria-label="Tiến độ bước hiện tại"
                aria-valuenow={Math.round(event.progress * 100)}
                aria-valuemin={0}
                aria-valuemax={100}
                className="block h-1 w-[360px] max-w-full overflow-hidden rounded-[2px] bg-subtle"
              >
                <span
                  className="block h-full rounded-[2px] bg-primary"
                  style={{ width: `${event.progress * 100}%` }}
                />
              </span>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}

const STEP_STATE = {
  ok: 'done',
  error: 'error',
  retried: 'done',
  skipped: 'waiting',
  simulated: 'done',
} as const;

function Steps({ steps }: { steps: AgentStep[] }) {
  if (steps.length === 0)
    return (
      <p className="py-da-lg text-muted-foreground da-body-small">Chưa có bước kỹ thuật nào.</p>
    );
  return (
    <div className="flex max-w-[720px] flex-col">
      {steps.map((step) => (
        <RunStep
          key={step.seq}
          state={step.duration_ms === null ? 'running' : STEP_STATE[step.status]}
          title={`${step.seq}. ${step.name}`}
          detail={[step.kind, step.summary].filter(Boolean).join(' · ')}
          duration={
            step.duration_ms === null
              ? undefined
              : `${formatNumber(step.duration_ms / 1000, { maxFractionDigits: 1 })} s`
          }
        />
      ))}
    </div>
  );
}
