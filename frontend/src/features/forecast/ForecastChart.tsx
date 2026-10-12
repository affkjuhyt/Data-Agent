import type React from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { formatOne, type Forecast } from '../../lib/v1/forecast';

/*
 * "Lịch sử, dự báo và khoảng tin cậy" (2391:15387): actuals up to the cut-off, then the forecast
 * inside its 95% and 80% bands, and the scenario dashed. The forecast series is built only from
 * points that carry both intervals, so a forecast line is never drawn without its interval; the
 * bands open from the last actual so the line leaves the history without a gap.
 */
interface Row {
  label: string;
  actual?: number;
  mean?: number;
  band80?: [number, number];
  band95?: [number, number];
  scenario?: number;
}

const finite = (...values: number[]) => values.every((v) => Number.isFinite(v));

export function chartRows(forecast: Forecast): Row[] {
  const rows: Row[] = forecast.history.map((h) => ({ label: h.label, actual: h.actual }));
  const points = forecast.points.filter((p) => finite(p.mean, p.lo80, p.hi80, p.lo95, p.hi95));
  const last = rows[rows.length - 1];
  if (points.length === 0 || last?.actual === undefined) return rows;

  const anchor = last.actual;
  Object.assign(last, {
    mean: anchor,
    band80: [anchor, anchor],
    band95: [anchor, anchor],
    scenario: forecast.scenario ? anchor : undefined,
  });
  const scenario = new Map(forecast.scenario?.points.map((p) => [p.period, p.value]));
  for (const p of points) {
    rows.push({
      label: p.label,
      mean: p.mean,
      band80: [p.lo80, p.hi80],
      band95: [p.lo95, p.hi95],
      scenario: scenario.get(p.period),
    });
  }
  return rows;
}

/** "30/09" from the ISO cut-off date. */
export const cutoffShort = (iso: string) => {
  const [, month, day] = iso.split('-');
  return `${day}/${month}`;
};

const tooltipStyle: React.CSSProperties = {
  background: 'var(--card)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  boxShadow: 'var(--shadow-md)',
  fontSize: 12,
  color: 'var(--foreground)',
};

const tooltipValue = (value: unknown) =>
  Array.isArray(value)
    ? `${formatOne(Number(value[0]))} – ${formatOne(Number(value[1]))}`
    : typeof value === 'number'
      ? formatOne(value)
      : String(value ?? '');

export function ForecastChart({ forecast }: { forecast: Forecast }) {
  const rows = chartRows(forecast);
  const cutLabel = forecast.history[forecast.history.length - 1]?.label;
  const axis = 'var(--muted-foreground)';
  return (
    <figure
      className="m-0 h-[230px] w-full"
      aria-label={`Biểu đồ ${forecast.metric_name}: thực tế tới ${cutoffShort(forecast.data_cutoff)}, dự báo kèm khoảng 80% và 95%`}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: 640, height: 230 }}
      >
        <ComposedChart data={rows} margin={{ top: 16, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            stroke={axis}
            fontSize={11}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
            minTickGap={48}
          />
          <YAxis
            stroke={axis}
            fontSize={11}
            axisLine={false}
            tickLine={false}
            width={44}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => formatOne(v).replace(/,0$/, '')}
          />
          <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => tooltipValue(v)} />
          <Area
            dataKey="band95"
            name="Khoảng 95%"
            stroke="none"
            fill="var(--info)"
            fillOpacity={0.12}
            isAnimationActive={false}
          />
          <Area
            dataKey="band80"
            name="Khoảng 80%"
            stroke="none"
            fill="var(--info)"
            fillOpacity={0.24}
            isAnimationActive={false}
          />
          <Line
            dataKey="actual"
            name="Thực tế"
            stroke="var(--foreground)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          <Line
            dataKey="mean"
            name="Dự báo"
            stroke="var(--info)"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            dot={false}
            isAnimationActive={false}
          />
          {forecast.scenario && (
            <Line
              dataKey="scenario"
              name={forecast.scenario.short_label}
              stroke="var(--warning)"
              strokeWidth={1.5}
              strokeDasharray="4 3"
              dot={false}
              isAnimationActive={false}
            />
          )}
          {cutLabel && (
            <ReferenceLine
              x={cutLabel}
              stroke="var(--border-strong)"
              label={{
                value: `Cắt ${cutoffShort(forecast.data_cutoff)}`,
                position: 'top',
                fill: axis,
                fontSize: 11,
              }}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </figure>
  );
}

/** The legend row of the chart card: Figma's 14×8 swatches, coloured like the series. */
export function ForecastLegend({ scenarioLabel }: { scenarioLabel?: string }) {
  const items: { label: string; swatch: string }[] = [
    { label: 'Thực tế', swatch: 'bg-foreground' },
    { label: 'Dự báo', swatch: 'bg-info' },
    { label: 'Khoảng 80%', swatch: 'bg-info opacity-40' },
    { label: 'Khoảng 95%', swatch: 'bg-info opacity-20' },
  ];
  if (scenarioLabel) items.push({ label: scenarioLabel, swatch: 'bg-warning' });
  return (
    <ul className="flex flex-wrap items-center gap-da-xl" aria-label="Chú giải">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-da-sm">
          <span className={`h-2 w-[14px] rounded-[2px] ${item.swatch}`} aria-hidden />
          <span className="text-muted-foreground da-caption">{item.label}</span>
        </li>
      ))}
    </ul>
  );
}
