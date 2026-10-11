import { KpiCard } from '../../design-system/KpiCard';
import { StatusDot } from '../../design-system/StatusDot';
import { cn } from '../../lib/utils';
import type { MyMetric } from '../../lib/v1/home';

/*
 * "Chỉ số của bạn" of "v3.1 · Trang chủ" (2132:15338): the metrics the viewer follows, three
 * across — value, how it moved, why in one line and the last periods as small bars, coloured by
 * whether the move is good. A card opens the metric in the catalog.
 */
export function MyMetrics({
  metrics,
  onOpen,
}: {
  metrics: MyMetric[];
  onOpen: (metricId: string) => void;
}) {
  const shown = metrics.filter((m) => m.series.length > 0).slice(0, 3);
  if (shown.length === 0) return null;
  return (
    <section
      aria-label="Chỉ số của bạn"
      className="grid grid-cols-3 gap-da-lg max-[1023px]:grid-cols-1"
    >
      {shown.map((m) => (
        <KpiCard
          key={m.metric_id}
          label={m.label}
          value={m.value}
          delta={m.delta}
          status={m.certified ? <StatusDot tone="success">Chứng nhận</StatusDot> : undefined}
          onClick={() => onOpen(m.metric_id)}
        >
          {m.note && <span className="text-muted-foreground da-caption">{m.note}</span>}
          <Bars series={m.series} bad={!m.delta.good && m.delta.direction !== 'flat'} />
        </KpiCard>
      ))}
    </section>
  );
}

function Bars({ series, bad }: { series: number[]; bad: boolean }) {
  const min = Math.min(...series);
  const max = Math.max(...series);
  return (
    <span aria-hidden className="flex h-5 items-end gap-[3px]">
      {series.map((v, i) => (
        <span
          key={i}
          className={cn('w-[9px] rounded-[1px]', bad ? 'bg-warning' : 'bg-muted-foreground')}
          style={{ height: `${max === min ? 50 : 30 + ((v - min) / (max - min)) * 70}%` }}
        />
      ))}
    </span>
  );
}
