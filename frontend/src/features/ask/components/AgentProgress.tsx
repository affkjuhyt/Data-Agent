/**
 * "v3.1 · Trạng thái · Agent đang chạy" (2076:5351): while an answer streams, the agent at work
 * and its steps (RunStep: done, running, not yet run), then — after the sentence streamed so far —
 * a result card still filling in, with the step behind it, skeleton bars and "Dừng". Shown only
 * while streaming; the finished answer replaces it.
 */
import { Icon } from '../../../design-system/Icon';
import { RunStep } from '../../../design-system/RunStep';
import { StatusDot } from '../../../design-system/StatusDot';
import type { AgentProgress } from '../types';

export function AgentSteps({ progress }: { progress: AgentProgress }) {
  return (
    <section aria-label={`${progress.agent} đang chạy`} className="flex flex-col gap-da-xs">
      <div className="flex items-center gap-da-md">
        <Icon name="agent" className="text-muted-foreground" />
        <div className="flex flex-col">
          <span className="text-foreground da-body-small">{progress.agent}</span>
          <StatusDot tone="success">Đang chạy</StatusDot>
        </div>
      </div>
      <ol className="m-0 flex list-none flex-col p-0">
        {progress.steps.map((step, i) => (
          <li key={`${step.title}-${i}`}>
            <RunStep
              state={step.state}
              title={step.title}
              detail={step.detail ?? (step.state === 'waiting' ? 'Chưa chạy' : undefined)}
              duration={step.duration ?? (step.state === 'waiting' ? '' : undefined)}
            />
          </li>
        ))}
      </ol>
    </section>
  );
}

const BARS = [
  { label: 'w-[96px]', bar: 'w-[240px] bg-warning' },
  { label: 'w-[96px]', bar: 'w-[96px] bg-muted-foreground' },
  { label: 'w-[96px]', bar: 'w-[120px] bg-subtle' },
  { label: 'w-[96px]', bar: 'w-[150px] bg-subtle' },
  { label: 'w-[96px]', bar: 'w-[180px] bg-subtle' },
];

export function PartialResult({
  partial,
  onStop,
}: {
  partial: NonNullable<AgentProgress['partial']>;
  onStop?: () => void;
}) {
  return (
    <section
      aria-label={`${partial.title} · đang nhận kết quả`}
      aria-busy="true"
      className="overflow-hidden rounded-md border border-border bg-card"
    >
      <header className="flex flex-col gap-[2px] border-b border-border px-da-md py-da-md">
        <span className="text-foreground da-heading-3">{partial.title}</span>
        <span className="text-muted-foreground da-caption">{partial.meta}</span>
      </header>
      <div className="flex flex-col gap-da-lg px-da-lg py-da-lg">
        <RunStep
          state="running"
          title={partial.step_title}
          detail={partial.step_detail}
          duration=""
        />
        <div aria-hidden className="flex flex-col gap-[8px]">
          {BARS.map((b, i) => (
            <div key={i} className="flex items-center gap-da-sm">
              <span className={`h-3 rounded-full bg-subtle ${b.label}`} />
              <span className={`h-3 rounded-full ${b.bar} ${i > 1 ? 'animate-pulse' : ''}`} />
            </div>
          ))}
        </div>
        <div className="flex items-center gap-da-md">
          <span className="text-muted-foreground da-caption">
            Đang nhận kết quả… {partial.received}
          </span>
          {onStop && (
            <button
              type="button"
              onClick={onStop}
              className="ml-auto inline-flex items-center gap-da-xs text-foreground da-body-small"
            >
              <Icon name="x" />
              Dừng
            </button>
          )}
        </div>
      </div>
    </section>
  );
}
