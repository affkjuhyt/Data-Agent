import { cn } from '../../lib/utils';
import type { WaterfallBar } from '../../lib/v1/skillAuthoring';

/*
 * The waterfall of "P03 · Chạy kỹ năng · Phân rã biến động" (2293:18322): the two totals in
 * chart-6, increases in chart-3, decreases in chart-2, each step floating from the running total.
 * As in the frame, the value axis starts below the smaller total (a change of 0,22 on 1,62 would
 * be invisible from zero); the totals carry their values as labels so nothing is read off a scale.
 * Values and labels stay in text colours; the bar colour only says up/down/total.
 */
const PLOT = 200;

export function Waterfall({
  start,
  steps,
  end,
}: {
  start: WaterfallBar;
  steps: WaterfallBar[];
  end: WaterfallBar;
}) {
  let running = start.value;
  const floating = steps.map((s) => {
    const from = running;
    running += s.value;
    return { ...s, lo: Math.min(from, running), hi: Math.max(from, running) };
  });
  const lowTotal = Math.min(start.value, end.value);
  const top = Math.max(start.value, end.value, ...floating.map((f) => f.hi));
  const span = Math.max(top - lowTotal, 0.0001);
  // The smaller total keeps a visible body: the axis starts about half the change below it.
  const base = lowTotal - span * 0.55;
  const y = (v: number) => ((top - v) / (top - base)) * PLOT;

  const bars = [
    { ...start, lo: base, hi: start.value, tone: 'total' as const },
    ...floating.map((f) => ({ ...f, tone: f.value < 0 ? ('down' as const) : ('up' as const) })),
    { ...end, lo: base, hi: end.value, tone: 'total' as const },
  ];

  return (
    <figure
      className="flex w-full max-w-[820px] gap-[30px] overflow-x-auto pt-[20px]"
      aria-label={`Thác đóng góp từ ${start.label} ${start.value_text} đến ${end.label} ${end.value_text}`}
    >
      {bars.map((b) => {
        const barTop = y(b.hi);
        const height = Math.max(y(b.lo) - barTop, 2);
        return (
          <div key={b.label} className="flex w-[60px] shrink-0 flex-col items-center gap-da-md">
            <div className="relative w-full" style={{ height: PLOT }}>
              <span
                className="absolute left-0 whitespace-nowrap text-foreground da-mono"
                style={{ bottom: PLOT - barTop + 2 }}
              >
                {b.value_text}
              </span>
              <span
                title={`${b.label}: ${b.value_text}`}
                className={cn(
                  'absolute inset-x-0 rounded-[2px]',
                  b.tone === 'total' ? 'bg-chart-6' : b.tone === 'up' ? 'bg-chart-3' : 'bg-chart-2',
                )}
                style={{ top: barTop, height }}
              />
            </div>
            <span className="w-[80px] text-center text-muted-foreground da-caption">{b.label}</span>
          </div>
        );
      })}
    </figure>
  );
}
