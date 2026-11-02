import { useEffect, useState } from 'react';
import { Icon, type IconName } from '../../../design-system/Icon';
import { cn } from '../../../lib/utils';
import type { StepStatus, TurnStep } from '../steps';
import { StepDetail } from './StepDetail';

/*
 * What the agent did on a turn, one line per step (docs/superpowers/specs/2026-11-02-ask-chat-
 * redesign-design.md): the header says how long it took and opens or closes the list; it stays
 * open while the turn runs. Each line is the step's verb in bold, its object, its chips, and a
 * chevron that opens what the step did (StepDetail).
 */

const MARK: Record<StepStatus, { icon: IconName; className: string; label: string }> = {
  running: { icon: 'refresh', className: 'text-info animate-spin', label: 'Đang chạy' },
  done: { icon: 'check', className: 'text-success', label: 'Xong' },
  failed: { icon: 'x', className: 'text-error', label: 'Lỗi' },
  skipped: { icon: 'clock', className: 'text-muted-foreground', label: 'Bỏ qua' },
};

export const seconds = (ms: number) =>
  (ms / 1000).toLocaleString('vi-VN', { maximumFractionDigits: ms < 10_000 ? 1 : 0 });

export function StepTimeline({
  steps,
  running,
  durationMs,
  recorded,
}: {
  steps: TurnStep[];
  running: boolean;
  durationMs?: number;
  recorded: boolean;
}) {
  const [open, setOpen] = useState(running);
  // Open while the turn runs; once it ends the answer is what matters, the steps fold away.
  useEffect(() => setOpen(running), [running]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (!recorded) {
    return <p className="m-0 text-muted-foreground da-caption">Lượt này chưa ghi chi tiết các bước.</p>;
  }
  if (!running && steps.length === 0) return null;

  const label = running
    ? `Đang làm… ${steps.length} bước`
    : durationMs !== undefined
      ? `Đã làm trong ${seconds(durationMs)} giây`
      : `Đã làm ${steps.length} bước`;

  return (
    <div className="flex flex-col gap-da-sm">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex w-fit items-center gap-da-xs text-muted-foreground da-label hover:text-foreground"
      >
        <Icon name="sparkles" size={14} className={running ? 'text-primary' : undefined} />
        {label}
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
      </button>
      {open && (
        <ol className="m-0 flex list-none flex-col gap-da-xs border-l border-border pl-da-md">
          {steps.map((step) => {
            const mark = MARK[step.status];
            const isOpen = !!expanded[step.stepId];
            return (
              <li key={step.stepId} className="flex flex-col gap-da-xs">
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
                      aria-expanded={isOpen}
                      onClick={() => setExpanded((prev) => ({ ...prev, [step.stepId]: !prev[step.stepId] }))}
                      className="rounded-sm p-[2px] text-muted-foreground hover:bg-subtle hover:text-foreground"
                    >
                      <Icon name={isOpen ? 'chevron-down' : 'chevron-right'} size={14} />
                    </button>
                  )}
                </div>
                {step.summary && step.status !== 'done' && (
                  <p className={cn('m-0 pl-[22px] da-caption', step.status === 'failed' ? 'text-error' : 'text-muted-foreground')}>
                    {step.summary}
                  </p>
                )}
                {isOpen && (
                  <div className="ml-[22px] rounded-md border border-border bg-subtle px-da-md py-da-sm">
                    <StepDetail step={step} />
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
