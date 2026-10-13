import type React from 'react';
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  LabelList,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  formatWhole,
  formatWholeSigned,
  type ForecastLedgerRow,
  type FuelPriceForecast,
} from '../../lib/v1/forecast';
import { dayMonth, dayMonthPadded, hasInterval } from './fuelParts';

/*
 * The three charts of the fuel-price screens, drawn with token colours only: the simulated
 * distribution of the ceiling (2422:15479), the decomposition of the expected change
 * (2422:15498) and the ledger's medians and intervals against the published prices (2424:15479).
 */

const AXIS = 'var(--muted-foreground)';

const tooltipStyle: React.CSSProperties = {
  background: 'var(--card)',
  border: '1px solid var(--border)',
  borderRadius: 'var(--radius-md)',
  boxShadow: 'var(--shadow-md)',
  fontSize: 12,
  color: 'var(--foreground)',
};

/* ─── Phân phối giá trần ─────────────────────────────────────────────── */

interface DensityRow {
  price: number;
  density: number;
  in95?: number;
  in80?: number;
}

/**
 * The density with its 80% and 95% regions filled under the curve. The interval bounds are
 * inserted as samples (interpolated) so the filled regions start and end exactly on them.
 */
export function distributionRows(forecast: FuelPriceForecast): DensityRow[] {
  const d = forecast.distribution;
  if (!hasInterval(d) || d.density.length < 2) return [];
  const samples = [...d.density].sort((a, b) => a.price - b.price);
  const at = (price: number) => {
    const i = samples.findIndex((s) => s.price >= price);
    if (i <= 0) return samples[Math.max(i, 0)].density;
    const [a, b] = [samples[i - 1], samples[i]];
    return a.density + ((b.density - a.density) * (price - a.price)) / (b.price - a.price);
  };
  const prices = new Set(samples.map((s) => s.price));
  for (const bound of [d.lo95, d.lo80, d.median, d.hi80, d.hi95]) prices.add(bound);
  return [...prices]
    .sort((a, b) => a - b)
    .map((price) => {
      const density = at(price);
      return {
        price,
        density,
        in95: price >= d.lo95 && price <= d.hi95 ? density : undefined,
        in80: price >= d.lo80 && price <= d.hi80 ? density : undefined,
      };
    });
}

const ticksBetween = (from: number, to: number, step: number) => {
  const ticks: number[] = [];
  for (let t = Math.ceil(from / step) * step; t <= to; t += step) ticks.push(t);
  return ticks;
};

export function DistributionChart({ forecast }: { forecast: FuelPriceForecast }) {
  const rows = distributionRows(forecast);
  if (rows.length === 0) return null;
  const d = forecast.distribution;
  const from = rows[0].price;
  const to = rows[rows.length - 1].price;
  return (
    <figure
      className="m-0 h-[140px] w-full"
      aria-label={`Phân phối giá trần ${forecast.product_label}: trung vị ${formatWhole(d.median)}, khoảng 80% ${formatWhole(d.lo80)} – ${formatWhole(d.hi80)}, khoảng 95% ${formatWhole(d.lo95)} – ${formatWhole(d.hi95)}`}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: 728, height: 140 }}
      >
        <ComposedChart data={rows} margin={{ top: 20, right: 16, bottom: 0, left: 16 }}>
          <XAxis
            dataKey="price"
            type="number"
            domain={[from, to]}
            ticks={ticksBetween(from, to, 200)}
            tickFormatter={(v: number) => formatWhole(v)}
            stroke={AXIS}
            fontSize={11}
            tickLine={false}
            axisLine={{ stroke: 'var(--border-strong)' }}
          />
          <YAxis hide domain={[0, 'dataMax']} />
          <Area
            dataKey="in95"
            stroke="none"
            fill="var(--info)"
            fillOpacity={0.14}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Area
            dataKey="in80"
            stroke="none"
            fill="var(--info)"
            fillOpacity={0.34}
            isAnimationActive={false}
            connectNulls={false}
          />
          <Line
            dataKey="density"
            stroke="var(--info)"
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
          {forecast.previous.ceiling !== null && (
            <ReferenceLine
              x={forecast.previous.ceiling}
              stroke="var(--foreground)"
              strokeDasharray="3 3"
              label={{
                value: `Kỳ trước ${formatWhole(forecast.previous.ceiling)}`,
                position: 'insideTopRight',
                fill: 'var(--foreground)',
                fontSize: 11,
                offset: -14,
              }}
            />
          )}
          <ReferenceLine
            x={d.median}
            stroke="var(--info)"
            strokeWidth={2}
            label={{
              value: `Trung vị ${formatWhole(d.median)}`,
              position: 'insideTopLeft',
              fill: 'var(--info)',
              fontSize: 11,
              offset: -14,
            }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </figure>
  );
}

/* ─── Phân rã chênh lệch ─────────────────────────────────────────────── */

interface WaterfallRow {
  name: string;
  /** Transparent part from the cut axis to the bottom of the bar. */
  base: number;
  bar: number;
  label: string;
  fill: string;
  opacity: number;
}

export function waterfallRows(forecast: FuelPriceForecast): WaterfallRow[] {
  const { steps, expected, axis_from: axis } = forecast.decomposition;
  const previous = forecast.previous.ceiling;
  // The steps start from the previous ceiling; without it there is nothing to decompose.
  if (previous === null) return [];
  const rows: WaterfallRow[] = [
    {
      name: 'Kỳ trước',
      base: 0,
      bar: previous - axis,
      label: formatWhole(previous),
      fill: 'var(--muted-foreground)',
      opacity: 0.85,
    },
  ];
  let running = previous;
  for (const step of steps) {
    const next = running + step.value;
    rows.push({
      name: step.label,
      base: Math.min(running, next) - axis,
      bar: Math.abs(step.value),
      label: formatWholeSigned(step.value),
      // A rise in the ceiling is the warning colour, a fall success, no change a hairline.
      fill:
        step.value > 0
          ? 'var(--warning)'
          : step.value < 0
            ? 'var(--success)'
            : 'var(--border-strong)',
      opacity: 1,
    });
    running = next;
  }
  rows.push({
    name: 'Kỳ vọng',
    base: 0,
    bar: expected - axis,
    label: formatWhole(expected),
    fill: 'var(--info)',
    opacity: 0.85,
  });
  return rows;
}

export function WaterfallChart({ forecast }: { forecast: FuelPriceForecast }) {
  const rows = waterfallRows(forecast);
  return (
    <figure
      className="m-0 flex w-full flex-col gap-da-xs"
      aria-label={`Phân rã chênh lệch kỳ vọng: ${rows.map((r) => `${r.name} ${r.label}`).join(', ')}`}
    >
      <div className="h-[150px] w-full">
        <ResponsiveContainer
          width="100%"
          height="100%"
          initialDimension={{ width: 388, height: 150 }}
        >
          <BarChart
            data={rows}
            margin={{ top: 18, right: 0, bottom: 0, left: 0 }}
            barCategoryGap="30%"
          >
            <XAxis
              dataKey="name"
              stroke={AXIS}
              fontSize={11}
              tickLine={false}
              interval={0}
              axisLine={{ stroke: 'var(--border-strong)' }}
            />
            <YAxis hide domain={[0, 'dataMax']} />
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="bar" stackId="w" minPointSize={2} isAnimationActive={false}>
              {rows.map((r) => (
                <Cell key={r.name} fill={r.fill} fillOpacity={r.opacity} />
              ))}
              <LabelList dataKey="label" position="top" fontSize={11} fill="var(--foreground)" />
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <figcaption className="self-end text-[10px] leading-4 text-muted-foreground">
        trục cắt từ {formatWhole(forecast.decomposition.axis_from)}
      </figcaption>
    </figure>
  );
}

/* ─── Sổ dự báo ─────────────────────────────────────────────────────── */

interface LedgerChartRow {
  label: string;
  band95: [number, number];
  band80: [number, number];
  median: number;
  actual?: number;
  missed: boolean;
}

/** Oldest first, as the chart reads left to right; only rows that carry both intervals. */
export function ledgerChartRows(rows: ForecastLedgerRow[]): LedgerChartRow[] {
  return [...rows]
    .filter((r) => hasInterval(r))
    .sort((a, b) => a.period_date.localeCompare(b.period_date))
    .map((r) => ({
      label: dayMonthPadded(r.period_date),
      band95: [r.lo95, r.hi95],
      band80: [r.lo80, r.hi80],
      median: r.median,
      actual: r.actual ?? undefined,
      missed: r.missed,
    }));
}

const tone = (missed: boolean) => (missed ? 'var(--error)' : 'var(--info)');

function MedianDot(props: { cx?: number; cy?: number; payload?: LedgerChartRow }) {
  const { cx, cy, payload } = props;
  if (cx === undefined || cy === undefined) return <g />;
  return (
    <circle
      cx={cx}
      cy={cy}
      r={3.5}
      fill="var(--card)"
      stroke={tone(!!payload?.missed)}
      strokeWidth={1.5}
    />
  );
}

function ActualDot(props: { cx?: number; cy?: number; payload?: LedgerChartRow }) {
  const { cx, cy, payload } = props;
  if (cx === undefined || cy === undefined || payload?.actual === undefined) return <g />;
  return (
    <circle cx={cx} cy={cy} r={3} fill={payload.missed ? 'var(--error)' : 'var(--foreground)'} />
  );
}

const ledgerTooltip = (value: unknown) =>
  Array.isArray(value)
    ? `${formatWhole(Number(value[0]))} – ${formatWhole(Number(value[1]))}`
    : typeof value === 'number'
      ? formatWhole(value)
      : String(value ?? '');

export function LedgerChart({
  rows,
  productLabel,
}: {
  rows: ForecastLedgerRow[];
  productLabel: string;
}) {
  const data = ledgerChartRows(rows);
  const first = rows[rows.length - 1];
  const last = rows[0];
  return (
    <figure
      className="m-0 h-[180px] w-full"
      aria-label={`Sổ dự báo ${productLabel}${first && last ? ` từ ${dayMonth(first.period_date)} đến ${dayMonth(last.period_date)}` : ''}: trung vị, khoảng 80% và 95% của mỗi kỳ cạnh giá công bố`}
    >
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: 1134, height: 180 }}
      >
        <ComposedChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis
            xAxisId="wide"
            dataKey="label"
            stroke={AXIS}
            fontSize={11}
            axisLine={false}
            tickLine={false}
            interval="preserveStartEnd"
            minTickGap={36}
          />
          {/* The 80% box shares the period with the 95% whisker: a second, hidden axis lets the
              two bars overlap instead of standing side by side. */}
          <XAxis xAxisId="box" dataKey="label" hide />
          <YAxis
            stroke={AXIS}
            fontSize={11}
            axisLine={false}
            tickLine={false}
            width={44}
            domain={['auto', 'auto']}
            tickFormatter={(v: number) => formatWhole(v)}
          />
          <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => ledgerTooltip(v)} />
          <Bar
            xAxisId="wide"
            dataKey="band95"
            name="Khoảng 95%"
            barSize={2}
            isAnimationActive={false}
          >
            {data.map((r) => (
              <Cell key={r.label} fill={tone(r.missed)} fillOpacity={0.5} />
            ))}
          </Bar>
          <Bar
            xAxisId="box"
            dataKey="band80"
            name="Khoảng 80%"
            barSize={12}
            isAnimationActive={false}
          >
            {data.map((r) => (
              <Cell key={r.label} fill={tone(r.missed)} fillOpacity={r.missed ? 0.45 : 0.35} />
            ))}
          </Bar>
          <Scatter
            xAxisId="wide"
            dataKey="median"
            name="Trung vị dự báo"
            shape={MedianDot}
            isAnimationActive={false}
          />
          <Scatter
            xAxisId="wide"
            dataKey="actual"
            name="Giá công bố"
            shape={ActualDot}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </figure>
  );
}

/** The legend of the ledger chart (2424:15458): whisker, box, hollow and filled dots, miss. */
export function LedgerLegend() {
  return (
    <ul className="flex flex-wrap items-center gap-da-xl" aria-label="Chú giải">
      <LegendItem label="Khoảng 95%">
        <span className="h-[10px] w-[2px] bg-info opacity-50" />
      </LegendItem>
      <LegendItem label="Khoảng 80%">
        <span className="size-[10px] bg-info opacity-35" />
      </LegendItem>
      <LegendItem label="Trung vị dự báo">
        <span className="size-[8px] rounded-full border-[1.5px] border-info bg-card" />
      </LegendItem>
      <LegendItem label="Giá công bố">
        <span className="size-[7px] rounded-full bg-foreground" />
      </LegendItem>
      <LegendItem label="Kỳ trượt">
        <span className="size-[10px] bg-error opacity-60" />
      </LegendItem>
    </ul>
  );
}

function LegendItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <li className="flex items-center gap-da-sm">
      <span className="flex h-[10px] w-[14px] items-center justify-center" aria-hidden>
        {children}
      </span>
      <span className="text-muted-foreground da-caption">{label}</span>
    </li>
  );
}
