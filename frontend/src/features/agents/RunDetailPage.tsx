import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { controlRun, getRun } from '../../lib/v1/api';
import type { AgentRun } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { ChartRenderer } from '../ask/components/ChartRenderer';
import { FailedRun } from './FailedRun';
import { PlanEditDrawer } from './PlanEditDrawer';
import { SpanDetail, TraceWaterfall, defaultSpan } from './RunTrace';
import { createQualityRule } from '../../lib/v1/dataProducts';
import { resultParts, ruleSuggestions, type RuleSuggestion } from './runResults';
import { PLAN_STEP, runStatus } from './runStatus';

/*
 * "P13 · Lượt chạy · Đang làm gì" (2310:19989, UC-13-06): one run told as sentences
 * ("Spec · v3.1 · Kể việc của agent") — time in mono, what happened, the step under way in
 * Body Strong with its progress, what comes next in muted — and its plan on the right.
 * "Xem kỹ thuật" ("P13 · Lượt chạy · Kỹ thuật", 2310:20156) shows the steps (agent_step) as a
 * waterfall instead, with the selected step in place of the plan. A failed run gets its own
 * layout (FailedRun) that leads with the error and the way out. A live run refreshes every 5 s until the
 * event stream (GET /v1/runs/{id}/events) reaches the browser. "Sửa kế hoạch" opens the plan
 * editor (PlanEditDrawer) once the run has stopped at a step boundary — paused, or waiting on a
 * person. Under the story, "Kết quả" shows what the run found: each answered question of its goal
 * with its sentence and chart, or a data audit's summary and parts (runResults), so the steps are
 * read whichever way the run is viewed.
 */
type View = 'simple' | 'technical';
const LIVE: AgentRun['status'][] = ['queued', 'planning', 'running', 'waiting_human'];
const POLL_MS = 5000;

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

export default function RunDetailPage() {
  const { routeId, navigate } = useAppState();
  const [view, setView] = useState<View>('simple');
  const run = useV1(routeId ? () => getRun(routeId, 'technical') : null, [routeId]);

  const live = run.data ? LIVE.includes(run.data.status) : false;
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(run.reload, POLL_MS);
    return () => window.clearInterval(timer);
  }, [live, run.reload]);

  return (
    <AsyncRegion state={run} what="lượt chạy">
      {(r) =>
        r.status === 'failed' ? (
          <FailedRun run={r} onRetried={(id) => navigate('agents', 'RUN', id)} />
        ) : (
          <RunBody run={r} view={view} onView={setView} onChanged={run.reload} />
        )
      }
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
  const [confirmPause, setConfirmPause] = useState(false);
  const [editingPlan, setEditingPlan] = useState(false);
  const stopped = run.status === 'paused' || run.status === 'waiting_human';
  const canEditPlan = stopped && (run.plan ?? []).some((s) => s.state !== 'done');
  const [busy, setBusy] = useState<'pause' | 'resume' | 'cancel' | null>(null);
  const [picked, setPicked] = useState<number>();
  const steps = run.steps ?? [];
  const spanSeq = picked ?? defaultSpan(steps);
  const span = steps.find((s) => s.seq === spanSeq);
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
      setConfirmPause(false);
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
                <Button loading={busy === 'pause'} onClick={() => setConfirmPause(true)}>
                  Tạm dừng
                </Button>
              )}
              {canEditPlan ? (
                <Button kind="primary" onClick={() => setEditingPlan(true)}>
                  Sửa kế hoạch
                </Button>
              ) : (
                <Tooltip
                  content={
                    stopped
                      ? 'Kế hoạch không còn bước nào chưa làm.'
                      : 'Tạm dừng lượt chạy trước khi sửa kế hoạch.'
                  }
                >
                  <Button kind="primary" disabled>
                    Sửa kế hoạch
                  </Button>
                </Tooltip>
              )}
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
          {view === 'simple' ? (
            <>
              <Story run={run} />
              <Results steps={steps} />
              <Suggestions steps={steps} />
            </>
          ) : (
            <TraceWaterfall run={run} steps={steps} selected={spanSeq} onSelect={setPicked} />
          )}
        </div>
        <aside className="flex w-[380px] shrink-0 flex-col border-l border-border bg-background px-da-2xl pt-da-xl max-[1279px]:hidden">
          {view === 'technical' ? span && <SpanDetail step={span} /> : <PlanPanel run={run} />}
        </aside>
      </div>
      {editingPlan && (
        <PlanEditDrawer
          run={run}
          onClose={() => setEditingPlan(false)}
          onApplied={(version) => {
            setEditingPlan(false);
            toast({
              tone: 'success',
              message: `Đã lưu kế hoạch phiên bản ${version}; ${run.agent_name} chạy tiếp`,
            });
            onChanged();
          }}
        />
      )}
      {/* "Xác nhận · pauseAgent" (2094:8319). */}
      <ConfirmDialog
        open={confirmPause}
        title={`Tạm dừng ${run.agent_name}?`}
        confirmLabel="Tạm dừng"
        loading={busy === 'pause'}
        onCancel={() => setConfirmPause(false)}
        onConfirm={() => act('pause')}
      >
        Lượt {run.code} dừng an toàn ở bước hiện tại. Không có dữ liệu bị ghi dở. Có thể tiếp tục
        bất cứ lúc nào.
      </ConfirmDialog>
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

/** What the run found: one part per answered question, its sentence and, when there is
 *  something to compare, its chart. */
function Results({ steps }: { steps: NonNullable<AgentRun['steps']> }) {
  const parts = resultParts(steps);
  if (parts.length === 0) return null;
  const title = parts.length === 1 ? 'Kết quả' : `Kết quả · ${parts.length} phần`;
  return (
    <section aria-label={title} className="flex flex-col gap-da-lg py-da-xl">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {parts.map((p) => (
        <article key={p.key} className="flex flex-col gap-da-sm">
          {p.question && <h3 className="text-foreground da-body-strong">{p.question}</h3>}
          <p className="text-foreground da-body">{p.summary}</p>
          {p.chart && <ChartRenderer spec={p.chart} />}
        </article>
      ))}
    </section>
  );
}

/** The quality rules a data audit suggests (AC-06-07-02): none exists until the person takes
 *  one here, which creates it (POST /v1/quality-rules) and has the worker run it at once. */
function Suggestions({ steps }: { steps: NonNullable<AgentRun['steps']> }) {
  const toast = useToast();
  const [state, setState] = useState<Record<string, 'busy' | 'done' | string>>({});
  const rules = ruleSuggestions(steps);
  if (rules.length === 0) return null;
  const take = async (r: RuleSuggestion) => {
    setState((s) => ({ ...s, [r.key]: 'busy' }));
    try {
      await createQualityRule({
        dataset_id: r.datasetId,
        kind: r.kind,
        column_name: r.column,
        params: r.params,
        severity: r.severity,
      });
      setState((s) => ({ ...s, [r.key]: 'done' }));
      toast({ tone: 'success', message: `Đã tạo quy tắc: ${r.text}` });
    } catch (e) {
      setState((s) => ({ ...s, [r.key]: (e as Error).message }));
    }
  };
  return (
    <section aria-label="Gợi ý quy tắc chất lượng" className="flex flex-col gap-da-md pb-da-xl">
      <h2 className="text-foreground da-heading-3">Gợi ý quy tắc chất lượng</h2>
      <ul className="flex flex-col">
        {rules.map((r) => {
          const st = state[r.key];
          return (
            <li key={r.key} className="flex items-center gap-da-md border-b border-border py-[9px]">
              <span className="min-w-0 flex-1 text-foreground da-body-small">{r.text}</span>
              <StatusDot tone={r.severity === 'block' ? 'error' : 'warning'}>
                {r.severity === 'block' ? 'Chặn báo cáo' : 'Cảnh báo'}
              </StatusDot>
              {st === 'done' ? (
                <span className="text-muted-foreground da-body-small">Đã tạo</span>
              ) : (
                <Button size="sm" loading={st === 'busy'} onClick={() => take(r)}>
                  Tạo quy tắc
                </Button>
              )}
              {st && st !== 'busy' && st !== 'done' && (
                <span className="text-error da-body-small">{st}</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** The plan beside the story, and what may be changed in it. */
function PlanPanel({ run }: { run: AgentRun }) {
  return (
    <>
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
                  <span className="min-w-0 flex-1 text-foreground da-body-small">{step.title}</span>
                  <StatusDot tone={look.tone}>{look.label}</StatusDot>
                </li>
              );
            })}
          </ol>
        </>
      )}
      <div className="flex flex-col gap-da-sm pt-[14px] text-muted-foreground">
        <p className="da-body-small">
          Bạn có thể sửa kế hoạch khi agent đang chạy: thêm bước, bỏ bước chưa làm, hoặc đổi điều
          kiện dừng. Bước đã xong không bị chạy lại.
        </p>
      </div>
    </>
  );
}
