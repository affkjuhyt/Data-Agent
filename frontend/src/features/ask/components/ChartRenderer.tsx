/**
 * Renders one chart_spec from the Visualization node. Eight chart types, all
 * fed from real result rows — the node never invents data, so neither can this.
 */
import type React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  Pie, PieChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, RadialBar,
  RadialBarChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import type { MouseHandlerDataParam } from 'recharts';
import { formatCompact, formatNumber } from '../../../lib/formatVN';
import type { ChartSpec } from '../types';
import { chartColor } from './AnswerTheme';

// Axis ticks: compact units ("600M") so long amounts do not overflow the
// axis; tooltips keep the full number.
const tickFormatter = (v: unknown) => (typeof v === 'number' ? (Math.abs(v) >= 10_000 ? formatCompact(v) : formatNumber(v)) : String(v ?? ''));
const valueFormatter = (v: unknown) => (typeof v === 'number' ? formatNumber(v) : String(v ?? ''));

/** `onPick`: a point clicked — a bar, a point of a line or area, a slice — by its category on the
 *  x axis (the answer's "Đi sâu", UC-01-04, and the chat's point menu, E3). `selected` stays
 *  marked on the chart after the pointer leaves: a line, its dot, or the other bars dimmed. */
export function ChartRenderer({
  spec,
  onPick,
  selected,
}: {
  spec: ChartSpec;
  onPick?: (category: string) => void;
  selected?: string | null;
}) {
  const colors = Array.from({ length: 6 }, (_, i) => chartColor(i));
  const grid = 'var(--border)';
  const axis = 'var(--muted-foreground)';
  const tooltipStyle: React.CSSProperties = {
    background: 'var(--card)', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)',
    boxShadow: 'var(--shadow-md)', fontSize: 12, color: 'var(--foreground)',
  };

  if (!Array.isArray(spec?.data) || spec.data.length === 0) return null;

  // A click anywhere over a category of a cartesian chart picks it, not only on the mark: a line's
  // point is a few pixels wide.
  // By the hovered label: on the dev cluster a click's activeIndex was 0 wherever it landed.
  const pickAt = (state: MouseHandlerDataParam) => {
    if (onPick && state.activeLabel !== undefined && state.activeLabel !== null) onPick(String(state.activeLabel));
  };
  const clickable = onPick ? { onClick: pickAt, style: { cursor: 'pointer' } } : {};

  const picked = selected == null ? undefined : spec.data.find((row) => String(row[spec.x_key] ?? '') === selected);
  const dimmed = (row: Record<string, unknown>) => (picked && row !== picked ? 0.35 : 1);
  const marker = picked && (
    <>
      <ReferenceLine x={picked[spec.x_key] as string} stroke="var(--primary)" strokeDasharray="4 3" />
      {spec.y_keys.map((k, i) =>
        typeof picked[k] === 'number' ? (
          <ReferenceDot
            key={k}
            x={picked[spec.x_key] as string}
            y={picked[k] as number}
            r={5}
            fill={colors[i % colors.length]}
            stroke="var(--card)"
            strokeWidth={2}
          />
        ) : null,
      )}
    </>
  );
  // A bar chart's picked bar keeps its colour, the others fade.
  const barCells = (i: number) =>
    picked ? spec.data.map((row, j) => <Cell key={j} fill={colors[i % colors.length]} fillOpacity={dimmed(row)} />) : null;

  // Every member of a short category axis is labelled: recharts dropped the ones that would
  // overlap, so "United Kingdom" had a bar and no name. Long names lean instead.
  const fewMembers = spec.data.length <= 24;
  const longNames = fewMembers && spec.data.length > 6 && spec.data.some((row) => String(row[spec.x_key] ?? '').length > 9);
  const axes = (numericX = false) => (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
      <XAxis
        dataKey={spec.x_key}
        type={numericX ? 'number' : 'category'}
        stroke={axis}
        fontSize={12}
        axisLine={false}
        tickLine={false}
        {...(!numericX && fewMembers && { interval: 0 })}
        {...(!numericX && longNames && { angle: -25, textAnchor: 'end', height: 56 })}
      />
      <YAxis stroke={axis} fontSize={12} axisLine={false} tickLine={false} tickFormatter={tickFormatter} width={72} />
      <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => valueFormatter(v)} />
      {(spec.series?.length ?? spec.y_keys.length) > 1 && (
        <Legend iconType="square" iconSize={10} wrapperStyle={{ fontSize: 12, color: axis }} />
      )}
    </>
  );

  const body = (() => {
    switch (spec.chart_type) {
      case 'line':
        return (
          <LineChart data={spec.data} {...clickable}>
            {axes()}
            {spec.y_keys.map((k, i) => <Line key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} strokeWidth={2} dot={false} />)}
            {marker}
          </LineChart>
        );
      case 'area':
        return (
          <AreaChart data={spec.data} {...clickable}>
            {axes()}
            {spec.y_keys.map((k, i) => <Area key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} fill={colors[i % colors.length]} fillOpacity={0.2} />)}
            {marker}
          </AreaChart>
        );
      case 'pie':
        return (
          <PieChart>
            <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => valueFormatter(v)} />
            <Pie
              data={spec.data}
              dataKey={spec.y_keys[0]}
              nameKey={spec.x_key}
              outerRadius={80}
              cursor={onPick ? 'pointer' : undefined}
              onClick={onPick ? (entry: { payload?: Record<string, unknown> }) => onPick(String(entry.payload?.[spec.x_key] ?? '')) : undefined}
            >
              {spec.data.map((row, i) => <Cell key={i} fill={colors[i % colors.length]} fillOpacity={dimmed(row)} />)}
            </Pie>
          </PieChart>
        );
      case 'scatter':
        return (
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke={grid} />
            <XAxis dataKey={spec.x_key} type="number" stroke={axis} fontSize={12} axisLine={false} tickLine={false} />
            <YAxis dataKey={spec.y_keys[0]} type="number" stroke={axis} fontSize={12} axisLine={false} tickLine={false} tickFormatter={tickFormatter} width={72} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={spec.data} fill={colors[0]} />
          </ScatterChart>
        );
      case 'radar':
        return (
          <RadarChart data={spec.data}>
            <PolarGrid stroke={grid} />
            <PolarAngleAxis dataKey={spec.x_key} stroke={axis} fontSize={12} />
            <PolarRadiusAxis stroke={axis} fontSize={12} />
            <Tooltip contentStyle={tooltipStyle} />
            {spec.y_keys.map((k, i) => <Radar key={k} dataKey={k} stroke={colors[i % colors.length]} fill={colors[i % colors.length]} fillOpacity={0.25} />)}
          </RadarChart>
        );
      case 'radialBar':
        return (
          <RadialBarChart data={spec.data} innerRadius="20%" outerRadius="90%">
            <Tooltip contentStyle={tooltipStyle} />
            <RadialBar dataKey={spec.y_keys[0]} background>
              {spec.data.map((_, i) => <Cell key={i} fill={colors[i % colors.length]} />)}
            </RadialBar>
            <Legend iconSize={8} wrapperStyle={{ fontSize: 12, color: axis }} />
          </RadialBarChart>
        );
      case 'composed':
        if (spec.series) {
          return (
            <ComposedChart data={spec.data} {...clickable}>
              {axes()}
              {spec.series.map((s, i) => {
                const color = colors[i % colors.length];
                if (s.type === 'line') return <Line key={s.key} type="monotone" dataKey={s.key} stroke={color} strokeWidth={2} dot={false} />;
                if (s.type === 'area') return <Area key={s.key} type="monotone" dataKey={s.key} stroke={color} fill={color} fillOpacity={0.2} />;
                return (
                  <Bar key={s.key} dataKey={s.key} fill={color} radius={[2, 2, 0, 0]}>
                    {barCells(i)}
                  </Bar>
                );
              })}
              {marker}
            </ComposedChart>
          );
        }
        // fall through to bar when series are missing
        return (
          <BarChart data={spec.data}>
            {axes()}
            {spec.y_keys.map((k, i) => <Bar key={k} dataKey={k} fill={colors[i % colors.length]} radius={[2, 2, 0, 0]} />)}
          </BarChart>
        );
      case 'bar':
      default:
        return (
          <BarChart data={spec.data} {...clickable}>
            {axes()}
            {spec.y_keys.map((k, i) => (
              <Bar key={k} dataKey={k} fill={colors[i % colors.length]} radius={[2, 2, 0, 0]}>
                {barCells(i)}
              </Bar>
            ))}
          </BarChart>
        );
    }
  })();

  return (
    <figure className="m-0 flex flex-col gap-da-md rounded-md border border-border bg-card px-da-xl py-da-lg">
      {spec.title && <figcaption className="text-foreground da-body-strong">{spec.title}</figcaption>}
      {/* recharts 3 makes the chart focusable; a click drew the browser's focus ring around it. */}
      <div className="h-[220px] w-full [&_.recharts-surface]:outline-none [&_.recharts-wrapper]:outline-none">
        <ResponsiveContainer width="100%" height="100%">
          {body}
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
