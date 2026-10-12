import type { AgentRun, AgentStep } from '../../lib/v1/types';
import { cn } from '../../lib/utils';

/*
 * "P13 · Lượt chạy · Kỹ thuật" (2310:20156, UC-13-06): the run's steps as a waterfall — the
 * step's mono name and what ran it in a 300px column (indented 16px per nesting level), its bar
 * on the run's time axis, and one figure on the right — and the selected step in the side panel
 * (start, whose permissions, what it read, the query). Colours follow the chart tokens:
 * model calls slate, queries and tools teal, skills blue, the step under way amber, a waiting
 * checkpoint a 3px mark.
 */
const BAR: Record<AgentStep['kind'], string> = {
  plan: 'bg-chart-6',
  llm_call: 'bg-chart-6',
  message: 'bg-chart-6',
  query: 'bg-chart-1',
  tool_call: 'bg-chart-1',
  skill: 'bg-chart-2',
  agent_call: 'bg-chart-2',
  checkpoint: 'bg-border-strong',
};
const MIN_BAR_PX = 3;

const clock = (iso: string, seconds = false) =>
  new Date(iso).toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    second: seconds ? '2-digit' : undefined,
  });

/** "45 giây", "2 phút 20 giây": span lengths are short, so seconds matter. */
function spanLength(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000));
  if (total < 60) return `${total} giây`;
  const rest = total % 60;
  return `${Math.floor(total / 60)} phút${rest ? ` ${rest} giây` : ''}`;
}

type SpanState = 'done' | 'running' | 'waiting' | 'error';

function stateOf(step: AgentStep): SpanState {
  if (step.status === 'error') return 'error';
  if (step.duration_ms !== null) return 'done';
  return step.started_at === null || step.kind === 'checkpoint' ? 'waiting' : 'running';
}

/** The step to open first: the one under way, else the one that failed, else the last. */
export function defaultSpan(steps: AgentStep[]): number | undefined {
  return (
    steps.find((s) => stateOf(s) === 'running') ??
    steps.find((s) => stateOf(s) === 'error') ??
    steps[steps.length - 1]
  )?.seq;
}

function depthOf(step: AgentStep, bySeq: Map<number, AgentStep>): number {
  let depth = 0;
  let parent = step.parent_seq;
  // The chain is bounded by the step count, so a cycle in bad data cannot hang the page.
  while (parent !== null && depth < bySeq.size) {
    depth += 1;
    parent = bySeq.get(parent)?.parent_seq ?? null;
  }
  return depth;
}

export function TraceWaterfall({
  run,
  steps,
  selected,
  onSelect,
  now = Date.now(),
}: {
  run: AgentRun;
  steps: AgentStep[];
  selected: number | undefined;
  onSelect: (seq: number) => void;
  now?: number;
}) {
  if (steps.length === 0)
    return (
      <p className="py-da-lg text-muted-foreground da-body-small">Chưa có bước kỹ thuật nào.</p>
    );

  const bySeq = new Map(steps.map((s) => [s.seq, s]));
  const start = new Date(run.started_at ?? steps[0].started_at ?? now).getTime();
  const end = run.finished_at ? new Date(run.finished_at).getTime() : now;
  const total = Math.max(1, end - start);
  const pct = (ms: number) => `${Math.min(100, Math.max(0, (ms / total) * 100))}%`;

  return (
    <div className="flex flex-col">
      <div className="flex items-start gap-da-lg border-b border-border px-da-md py-da-sm text-muted-foreground">
        <span className="w-[300px] shrink-0 da-caption">Bước</span>
        <span className="min-w-0 flex-1 da-mono">
          thời gian từ {clock(new Date(start).toISOString())} → {clock(new Date(end).toISOString())}
        </span>
      </div>
      <ol className="flex flex-col">
        {steps.map((step) => {
          const state = stateOf(step);
          const offset = step.started_at ? new Date(step.started_at).getTime() - start : total;
          const length = state === 'running' ? end - start - offset : (step.duration_ms ?? 0);
          return (
            <li key={step.seq}>
              <button
                type="button"
                aria-pressed={selected === step.seq}
                onClick={() => onSelect(step.seq)}
                className={cn(
                  'flex w-full items-center gap-da-lg border-b border-border px-da-md py-[7px] text-left transition-colors duration-[120ms] ease-out hover:bg-subtle',
                  selected === step.seq && 'bg-subtle',
                )}
              >
                <span
                  className="flex w-[300px] shrink-0 items-center gap-da-sm overflow-hidden whitespace-nowrap"
                  style={{ paddingLeft: depthOf(step, bySeq) * 16 }}
                >
                  <span className="truncate text-foreground da-mono">{step.name}</span>
                  <span className="shrink-0 text-muted-foreground da-caption">{step.actor}</span>
                </span>
                <span className="relative h-3.5 min-w-0 flex-1 overflow-hidden" aria-hidden>
                  <span
                    className={cn(
                      'absolute top-0.5 h-2.5 rounded-[2px]',
                      state === 'running'
                        ? 'bg-chart-3'
                        : state === 'error'
                          ? 'bg-error'
                          : BAR[step.kind],
                    )}
                    style={{
                      // A bar at the very end would start past the track; keep its mark inside.
                      left: `min(${pct(Math.min(offset, total))}, calc(100% - ${MIN_BAR_PX}px))`,
                      width: `max(${MIN_BAR_PX}px, ${pct(length)})`,
                    }}
                  />
                </span>
                <span className="w-20 shrink-0 text-muted-foreground da-caption">
                  {state === 'running'
                    ? 'đang chạy'
                    : state === 'waiting'
                      ? '—'
                      : state === 'error'
                        ? 'lỗi'
                        : (step.metric_text ?? '')}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function SpanDetail({ step, now = Date.now() }: { step: AgentStep; now?: number }) {
  const state = stateOf(step);
  const started = step.started_at
    ? `${clock(step.started_at, true)} · ${
        state === 'running'
          ? `đang chạy ${spanLength(now - new Date(step.started_at).getTime())}`
          : step.duration_ms !== null
            ? `chạy ${spanLength(step.duration_ms)}`
            : 'chưa xong'
      }`
    : 'Chưa bắt đầu';
  const rows: [string, string | null][] = [
    ['Bắt đầu', started],
    ['Chạy theo quyền', step.run_as_text],
    ['Dòng đọc', step.rows_text],
    ['Kết quả', step.summary],
  ];

  return (
    <div className="flex flex-col gap-[10px]">
      <h2 className="text-foreground da-heading-3">{step.name}</h2>
      <dl className="flex flex-col gap-[10px]">
        {rows
          .filter((row): row is [string, string] => Boolean(row[1]))
          .map(([label, value]) => (
            <div key={label} className="flex flex-col gap-[2px] pb-da-xs">
              <dt className="text-muted-foreground da-caption">{label}</dt>
              <dd className="text-foreground da-body-small">{value}</dd>
            </div>
          ))}
      </dl>
      {step.sql && (
        <pre className="whitespace-pre-wrap rounded-sm border border-border bg-card px-da-lg py-[10px] text-foreground da-mono">
          {step.sql}
        </pre>
      )}
      <p className="text-muted-foreground da-body-small">
        Trace đầy đủ, prompt và token lưu ở Langfuse 30 ngày, đã che dữ liệu cá nhân.
      </p>
    </div>
  );
}
