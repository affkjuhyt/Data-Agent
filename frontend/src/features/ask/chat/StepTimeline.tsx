import { useEffect, useState } from 'react';
import { Icon, type IconName } from '../../../design-system/Icon';
import { cn } from '../../../lib/utils';
import type { StepStatus, TurnStep } from '../steps';
import { StepDetail } from './StepDetail';

/*
 * How the agent reasoned on a turn (docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md).
 * While the turn runs it is open and moves (E1, approach A): the plan's reasoning, each step as it
 * starts and what it found once done (`said`, written by the server from the data, never by a
 * model), the steps still to come, and a clock. Once the turn ends it folds to one line under the
 * answer — how many steps, how long — which opens the same account. A step's chevron opens what
 * it did (StepDetail).
 */

const MARK: Record<StepStatus, { icon: IconName; className: string; label: string }> = {
  running: { icon: 'refresh', className: 'text-info animate-spin', label: 'Đang chạy' },
  done: { icon: 'check', className: 'text-success', label: 'Xong' },
  failed: { icon: 'x', className: 'text-error', label: 'Lỗi' },
  skipped: { icon: 'clock', className: 'text-muted-foreground', label: 'Bỏ qua' },
};

const REASONING = new Set(['plan', 'replan']);

const seconds = (ms: number) =>
  (ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: ms < 10_000 ? 1 : 0 });
const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** Seconds since the turn's first step, ticking while it runs. */
function useElapsed(running: boolean, since: string | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [running]);
  const start = since ? Date.parse(since) : NaN;
  return Number.isFinite(start) ? Math.max(0, Math.round((now - start) / 1000)) : null;
}

/** The planned steps not started yet: from the latest plan or replan, those no step ran for. */
function upcoming(steps: TurnStep[]): string[] {
  const latest = [...steps].reverse().find((s) => REASONING.has(s.action) && Array.isArray(s.detail?.steps));
  if (!latest) return [];
  const started = new Set(steps.map((s) => s.target));
  return (latest.detail!.steps as Record<string, unknown>[])
    .filter((p) => p.status === 'pending' && !started.has(str(p.title).slice(0, 160)))
    .map((p) => str(p.title))
    .filter(Boolean);
}

function Thought({ step }: { step: TurnStep }) {
  const thought = str(step.detail?.thought);
  const replanned = step.action === 'replan';
  if (!thought && !(replanned && step.target)) {
    return step.status === 'running' ? (
      <li className="da-reveal flex items-center gap-da-sm text-muted-foreground da-body-small">
        <Icon name="sparkles" size={14} className="shrink-0 text-primary" />
        {replanned ? 'Đang điều chỉnh kế hoạch…' : 'Đang lập kế hoạch…'}
      </li>
    ) : null;
  }
  return (
    <li className="da-reveal flex items-start gap-da-sm da-body-small">
      <Icon name="sparkles" size={14} className="mt-[3px] shrink-0 text-primary" />
      <p className="m-0 text-muted-foreground">
        {replanned && step.target && <span className="text-foreground">Điều chỉnh vì: {step.target}. </span>}
        <em>{thought}</em>
      </p>
    </li>
  );
}

export function StepTimeline({
  steps,
  running,
  durationMs,
  recorded,
  operatorWhy,
}: {
  steps: TurnStep[];
  running: boolean;
  durationMs?: number;
  recorded: boolean;
  /** OP-8: one-line steward view (fail / replan / handover) from plan.steps.operator. */
  operatorWhy?: string;
}) {
  // null follows the turn: open while it runs, folded once it ends; a click decides from then on.
  const [open, setOpen] = useState<boolean | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (!running) setOpen(null);
  }, [running]);
  const elapsed = useElapsed(running, steps[0]?.startedAt);

  if (!recorded) {
    return <p className="m-0 text-muted-foreground da-caption">Lượt này chưa ghi chi tiết các bước.</p>;
  }
  if (!running && steps.length === 0 && !operatorWhy) return null;

  const isOpen = open ?? running;
  const work = steps.filter((s) => !REASONING.has(s.action));
  const failed = work.filter((s) => s.status === 'failed').length;
  const label = running
    ? ['Đang suy luận', elapsed !== null ? `${elapsed} giây` : null].filter(Boolean).join(' · ')
    : [
        `Đã thực hiện ${steps.length} bước`,
        durationMs !== undefined ? `${seconds(durationMs)} giây` : null,
        failed ? `${failed} bước lỗi` : null,
      ]
        .filter(Boolean)
        .join(' · ');
  const next = running ? upcoming(steps) : [];

  return (
    <div className="flex flex-col gap-da-sm">
      <button
        type="button"
        aria-expanded={isOpen}
        onClick={() => setOpen(!isOpen)}
        className="inline-flex max-w-full items-center gap-da-xs text-muted-foreground da-label hover:text-foreground"
      >
        <Icon name={running ? 'refresh' : 'sparkles'} size={14} className={running ? 'shrink-0 animate-spin text-primary' : 'shrink-0'} />
        <span className="truncate">{label}</span>
        <Icon name={isOpen ? 'chevron-down' : 'chevron-right'} size={14} className="shrink-0" />
      </button>
      {!running && operatorWhy && (
        <p className="m-0 text-muted-foreground da-caption" data-testid="operator-why">
          {operatorWhy}
        </p>
      )}
      {isOpen && (
        <ol aria-label="Các bước suy luận" className="m-0 flex list-none flex-col gap-da-xs border-l border-border pl-da-md">
          {steps.map((step) => {
            if (REASONING.has(step.action)) return <Thought key={step.stepId} step={step} />;
            const mark = MARK[step.status];
            const isExpanded = !!expanded[step.stepId];
            const said = str(step.detail?.said);
            const note = step.status === 'done' ? said : said || step.summary;
            return (
              <li key={step.stepId} className="da-reveal flex flex-col gap-[2px]">
                <div className="flex items-start gap-da-sm">
                  <span aria-label={mark.label} role="img" className="mt-[2px] inline-flex">
                    <Icon name={mark.icon} size={14} className={mark.className} />
                  </span>
                  <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-da-xs gap-y-[2px] da-body-small">
                    <strong className="text-foreground">{step.title}</strong>
                    {step.target && <span className="text-muted-foreground">{step.target}</span>}
                    {step.chips
                      .filter((chip) => !step.target.includes(chip.label))
                      .map((chip) => (
                        <span
                          key={`${chip.kind}-${chip.label}`}
                          className="inline-flex items-center gap-[4px] rounded-sm bg-accent px-[6px] py-[1px] text-primary da-caption"
                        >
                          <Icon name={chip.kind === 'source' ? 'database' : 'chart'} size={12} />
                          {chip.label}
                        </span>
                      ))}
                    {step.status !== 'running' && step.durationMs !== null && (
                      <span className="text-muted-foreground da-caption">{seconds(step.durationMs)} s</span>
                    )}
                  </div>
                  {step.status !== 'running' && (
                    <button
                      type="button"
                      aria-label={`Xem chi tiết: ${step.title}`}
                      aria-expanded={isExpanded}
                      onClick={() => setExpanded((prev) => ({ ...prev, [step.stepId]: !prev[step.stepId] }))}
                      className="rounded-sm p-[2px] text-muted-foreground hover:bg-subtle hover:text-foreground"
                    >
                      <Icon name={isExpanded ? 'chevron-down' : 'chevron-right'} size={14} />
                    </button>
                  )}
                </div>
                {note && step.status !== 'running' && (
                  <p
                    className={cn(
                      'da-reveal m-0 pl-[22px] da-caption',
                      step.status === 'failed' ? 'text-error' : 'text-muted-foreground',
                    )}
                  >
                    → {note}
                  </p>
                )}
                {isExpanded && (
                  <div className="ml-[22px] rounded-md border border-border bg-subtle px-da-md py-da-sm">
                    <StepDetail step={step} />
                  </div>
                )}
              </li>
            );
          })}
          {next.map((title) => (
            <li key={title} className="flex items-start gap-da-sm text-muted-foreground da-body-small">
              <span aria-label="Sắp làm" role="img" className="mt-[2px] inline-flex">
                <Icon name="clock" size={14} />
              </span>
              <span>{title}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
