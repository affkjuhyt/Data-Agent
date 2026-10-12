import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  YAxis,
} from 'recharts';
import type { MonitorPreview } from '../../lib/v1/monitors';

/*
 * The 90-day preview of "Tạo theo dõi" (2270:16233): the value, the threshold dashed in error
 * and the early warning dotted in warning, and a dot on each day the draft would have fired.
 */
const TONE = { error: 'var(--error)', warning: 'var(--warning)' } as const;
const fmt = (n: number) => n.toFixed(1).replace('.', ',');

export function PreviewChart({ preview, unit }: { preview: MonitorPreview; unit: string }) {
  const series = preview.series ?? [];
  const threshold = preview.thresholds.find((t) => t.tone === 'error')?.value;
  return (
    <figure className="m-0 flex flex-col gap-da-md">
      <div
        className="h-[230px] w-full"
        role="img"
        aria-label={`Giá trị 90 ngày qua; theo dõi sẽ cảnh báo ${preview.fires} lần`}
      >
        <ResponsiveContainer
          width="100%"
          height="100%"
          initialDimension={{ width: 560, height: 230 }}
        >
          <LineChart data={series} margin={{ top: 16, right: 16, bottom: 8, left: 0 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <YAxis
              stroke="var(--muted-foreground)"
              fontSize={11}
              axisLine={false}
              tickLine={false}
              width={44}
              domain={['auto', 'auto']}
              tickFormatter={(v: number) => `${fmt(v)}${unit}`}
            />
            {preview.thresholds.map((t) => (
              <ReferenceLine
                key={t.label}
                y={t.value}
                stroke={TONE[t.tone]}
                strokeDasharray={t.tone === 'error' ? '6 4' : '2 4'}
              />
            ))}
            <Line
              dataKey="value"
              stroke="var(--primary)"
              strokeWidth={1.5}
              isAnimationActive={false}
              dot={(props: {
                cx?: number;
                cy?: number;
                payload?: { value: number };
                index?: number;
              }) =>
                threshold !== undefined && props.payload && props.payload.value > threshold ? (
                  <circle
                    key={props.index}
                    cx={props.cx}
                    cy={props.cy}
                    r={3.5}
                    fill="var(--error)"
                  />
                ) : (
                  <g key={props.index} />
                )
              }
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="flex flex-wrap gap-da-lg text-muted-foreground da-caption">
        <Legend color="var(--primary)">Giá trị</Legend>
        {preview.thresholds.map((t) => (
          <Legend key={t.label} color={TONE[t.tone]}>
            {t.label}
          </Legend>
        ))}
      </figcaption>
    </figure>
  );
}

function Legend({ color, children }: { color: string; children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center gap-da-xs">
      <span aria-hidden className="h-[2px] w-3" style={{ background: color }} />
      {children}
    </span>
  );
}
