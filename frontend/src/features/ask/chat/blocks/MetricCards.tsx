/** A metric block of the answer (answer_blocks.py): one figure, its unit and the cut it is for. */
export interface MetricBlock {
  type: 'metric';
  id: string;
  label: string;
  value: string | number | null;
  unit?: string | null;
  dimensions?: Record<string, unknown>;
}

const formatValue = (value: MetricBlock['value']) => {
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n.toLocaleString('vi-VN', { maximumFractionDigits: 2 }) : String(value ?? '—');
};

/** The headline figures of an answer as cards: the value large, its unit and cut beneath. */
export function MetricCards({ metrics }: { metrics: MetricBlock[] }) {
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-da-md">
      {metrics.map((metric) => {
        const cut = Object.values(metric.dimensions ?? {}).map(String).join(' · ');
        return (
          <div
            key={metric.id}
            role="group"
            aria-label={metric.label}
            className="flex flex-col gap-[2px] rounded-lg border border-border bg-card px-da-lg py-da-md"
          >
            <span className="text-muted-foreground da-caption">{metric.label}</span>
            <span className="text-foreground da-heading-3">
              {formatValue(metric.value)}
              {metric.unit && <span className="ml-da-xs text-muted-foreground da-body-small">{metric.unit}</span>}
            </span>
            {cut && <span className="text-muted-foreground da-caption">{cut}</span>}
          </div>
        );
      })}
    </div>
  );
}
