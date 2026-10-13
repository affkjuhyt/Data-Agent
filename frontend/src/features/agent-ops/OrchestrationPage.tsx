import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatMoneyShort, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  controlOrchestration,
  findOrchestration,
  getOrchestration,
  type DelegatedTask,
  type Orchestration,
} from '../../lib/v1/agentOps';
import { useV1 } from '../../lib/v1/useV1';
import { taskStatus } from './format';

/*
 * "P13 · Điều phối nhiều agent" (2311:20297, UC-13-08): one lead agent and the agents it
 * delegated to, working one problem. Each row is one agent's run: its task, what it found, its
 * status; delegated rows are indented under the lead. On the right, on --background, what the
 * whole tree has cost so far. Pausing and resuming act on the lead run; its children wait on it.
 *
 * /agents/orchestration/{lead_run_id} opens one; without an id, the latest one still running.
 */
const ACTIVE = new Set(['queued', 'planning', 'running', 'waiting_human']);

export default function OrchestrationPage() {
  const { routeId, navigate } = useAppState();
  const toast = useToast();
  const loaded = useV1<Orchestration | null>(
    () =>
      routeId
        ? getOrchestration(routeId)
        : findOrchestration().then((id) => (id ? getOrchestration(id) : null)),
    [routeId],
  );
  const [current, setCurrent] = useState<Orchestration | null>();
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => setCurrent(undefined), [routeId]);
  const state = { ...loaded, data: current !== undefined ? current : loaded.data };

  const control = async (o: Orchestration, verb: 'pause' | 'resume') => {
    setSending(true);
    setError(undefined);
    try {
      const next = await controlOrchestration(o.id, verb);
      setCurrent(next);
      toast({
        tone: 'success',
        message: verb === 'pause' ? `Đã tạm dừng cả nhóm ${o.code}` : `Cả nhóm ${o.code} chạy tiếp`,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <AsyncRegion
        state={state}
        what="lượt điều phối"
        skeletonRows={5}
        isEmpty={(o) => o === null}
        empty={
          <div className="px-da-3xl pt-da-xl">
            <EmptyState
              icon="users"
              title="Chưa có lượt điều phối nào"
              description="Khi một agent giao việc cho agent khác để giải một vấn đề, cả nhóm hiện ở đây cùng chi phí chung."
            />
          </div>
        }
      >
        {(o) =>
          o && (
            <OrchestrationView
              orchestration={o}
              sending={sending}
              error={error}
              onPause={() => control(o, 'pause')}
              onResume={() => control(o, 'resume')}
              onRuns={() => navigate('agents', 'RUNS')}
            />
          )
        }
      </AsyncRegion>
    </div>
  );
}

function OrchestrationView({
  orchestration: o,
  sending,
  error,
  onPause,
  onResume,
  onRuns,
}: {
  orchestration: Orchestration;
  sending: boolean;
  error?: string;
  onPause: () => void;
  onResume: () => void;
  onRuns: () => void;
}) {
  const status = taskStatus(o.status);
  const helpers = o.tasks.filter((t) => t.delegated_by !== null).length;
  const minutes = o.started_at
    ? Math.max(0, Math.round((Date.now() - new Date(o.started_at).getTime()) / 60_000))
    : null;

  return (
    <>
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Điều phối', o.code]}
        title={
          <span className="flex items-center gap-[10px]">
            Lượt điều phối của {o.lead_agent_name}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={`${o.lead_agent_name} dẫn dắt, giao việc cho ${helpers} agent khác${
          o.started_at ? ` · bắt đầu ${formatTime(o.started_at)}` : ''
        }`}
        actions={
          <>
            {o.status === 'paused' ? (
              <Button kind="ghost" loading={sending} onClick={onResume}>
                Tiếp tục tất cả
              </Button>
            ) : (
              ACTIVE.has(o.status) && (
                <Button kind="ghost" loading={sending} onClick={onPause}>
                  Tạm dừng tất cả
                </Button>
              )
            )}
            <Button onClick={onRuns}>Xem từng lượt</Button>
          </>
        }
      />
      {error && (
        <p role="alert" className="px-da-3xl pt-da-md text-error da-caption">
          {error}
        </p>
      )}
      <div className="flex flex-1 items-stretch">
        <ul
          aria-label="Giao việc"
          className="flex min-w-0 flex-1 flex-col pb-da-3xl pl-da-3xl pr-da-2xl pt-da-lg"
        >
          {o.tasks.map((t) => (
            <TaskRow key={t.run_id} task={t} />
          ))}
        </ul>
        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Chi phí hiện tại</h2>
          <p className="text-foreground da-body-small">
            {formatMoneyShort(o.cost)} · {o.tasks.length} lượt
            {minutes !== null && ` · ${minutes} phút`}
          </p>
        </aside>
      </div>
    </>
  );
}

function TaskRow({ task: t }: { task: DelegatedTask }) {
  const status = taskStatus(t.status);
  return (
    <li
      className={cn(
        'flex items-start gap-da-lg border-b border-border py-[14px]',
        t.delegated_by && 'pl-[28px]',
      )}
    >
      <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
        <div className="flex items-center gap-da-md">
          <span className="text-foreground da-body-strong">{t.agent_name}</span>
          {t.delegated_by && (
            <span className="text-muted-foreground da-caption">được giao bởi {t.delegated_by}</span>
          )}
        </div>
        {t.task && <p className="text-foreground da-body-small">{t.task}</p>}
        {t.result_text && <p className="text-muted-foreground da-body-small">→ {t.result_text}</p>}
      </div>
      <span className="w-[110px] shrink-0">
        <StatusDot tone={status.tone}>{status.label}</StatusDot>
      </span>
    </li>
  );
}
