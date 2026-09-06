/**
 * Renders one chart_spec from the Visualization node. Eight chart types, all
 * fed from real result rows — the node never invents data, so neither can this.
 */
import React from 'react';
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart,
  Pie, PieChart, PolarAngleAxis, PolarGrid, PolarRadiusAxis, Radar, RadarChart, RadialBar,
  RadialBarChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from 'recharts';
import { formatCompact, formatNumber } from '../../../lib/formatVN';
import type { ChartSpec } from '../types';
import { CHART_COLORS, useAnswerTheme } from './AnswerTheme';

// Axis ticks: compact units ("600M") so long amounts do not overflow the
// axis; tooltips keep the full number.
const tickFormatter = (v: unknown) => (typeof v === 'number' ? (Math.abs(v) >= 10_000 ? formatCompact(v) : formatNumber(v)) : String(v ?? ''));
const valueFormatter = (v: unknown) => (typeof v === 'number' ? formatNumber(v) : String(v ?? ''));

export function ChartRenderer({ spec }: { spec: ChartSpec }) {
  const mode = useAnswerTheme();
  const colors = CHART_COLORS[mode];
  const grid = mode === 'dark' ? 'rgba(255,255,255,0.08)' : 'rgba(17,17,17,0.08)';
  const axis = mode === 'dark' ? 'rgba(255,255,255,0.4)' : '#5F5F5F';
  const tooltipStyle: React.CSSProperties = mode === 'dark'
    ? { background: '#1a1a1a', border: '1px solid rgba(255,255,255,0.15)', borderRadius: 8, fontSize: 11, color: '#fff' }
    : { background: '#FFFFFF', border: '1px solid #D6C79F', borderRadius: 8, fontSize: 11, color: '#111111' };

  if (!Array.isArray(spec?.data) || spec.data.length === 0) return null;

  const axes = (numericX = false) => (
    <>
      <CartesianGrid strokeDasharray="3 3" stroke={grid} vertical={false} />
      <XAxis dataKey={spec.x_key} type={numericX ? 'number' : 'category'} stroke={axis} fontSize={10} axisLine={false} tickLine={false} />
      <YAxis stroke={axis} fontSize={10} axisLine={false} tickLine={false} tickFormatter={tickFormatter} width={72} />
      <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => valueFormatter(v)} />
    </>
  );

  const body = (() => {
    switch (spec.chart_type) {
      case 'line':
        return (
          <LineChart data={spec.data}>
            {axes()}
            {spec.y_keys.map((k, i) => <Line key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} strokeWidth={2} dot={false} />)}
          </LineChart>
        );
      case 'area':
        return (
          <AreaChart data={spec.data}>
            {axes()}
            {spec.y_keys.map((k, i) => <Area key={k} type="monotone" dataKey={k} stroke={colors[i % colors.length]} fill={colors[i % colors.length]} fillOpacity={0.2} />)}
          </AreaChart>
        );
      case 'pie':
        return (
          <PieChart>
            <Tooltip contentStyle={tooltipStyle} formatter={(v: unknown) => valueFormatter(v)} />
            <Pie data={spec.data} dataKey={spec.y_keys[0]} nameKey={spec.x_key} outerRadius={80}>
              {spec.data.map((_, i) => <Cell key={i} fill={colors[i % colors.length]} />)}
            </Pie>
          </PieChart>
        );
      case 'scatter':
        return (
          <ScatterChart>
            <CartesianGrid strokeDasharray="3 3" stroke={grid} />
            <XAxis dataKey={spec.x_key} type="number" stroke={axis} fontSize={10} axisLine={false} tickLine={false} />
            <YAxis dataKey={spec.y_keys[0]} type="number" stroke={axis} fontSize={10} axisLine={false} tickLine={false} tickFormatter={tickFormatter} width={72} />
            <Tooltip contentStyle={tooltipStyle} cursor={{ strokeDasharray: '3 3' }} />
            <Scatter data={spec.data} fill={colors[0]} />
          </ScatterChart>
        );
      case 'radar':
        return (
          <RadarChart data={spec.data}>
            <PolarGrid stroke={grid} />
            <PolarAngleAxis dataKey={spec.x_key} stroke={axis} fontSize={10} />
            <PolarRadiusAxis stroke={axis} fontSize={9} />
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
            <Legend iconSize={8} wrapperStyle={{ fontSize: 10, color: axis }} />
          </RadialBarChart>
        );
      case 'composed':
        if (spec.series) {
          return (
            <ComposedChart data={spec.data}>
              {axes()}
              {spec.series.map((s, i) => {
                const color = colors[i % colors.length];
                if (s.type === 'line') return <Line key={s.key} type="monotone" dataKey={s.key} stroke={color} strokeWidth={2} dot={false} />;
                if (s.type === 'area') return <Area key={s.key} type="monotone" dataKey={s.key} stroke={color} fill={color} fillOpacity={0.2} />;
                return <Bar key={s.key} dataKey={s.key} fill={color} radius={[4, 4, 0, 0]} />;
              })}
            </ComposedChart>
          );
        }
        // fall through to bar when series are missing
        return (
          <BarChart data={spec.data}>
            {axes()}
            {spec.y_keys.map((k, i) => <Bar key={k} dataKey={k} fill={colors[i % colors.length]} radius={[4, 4, 0, 0]} />)}
          </BarChart>
        );
      case 'bar':
      default:
        return (
          <BarChart data={spec.data}>
            {axes()}
            {spec.y_keys.map((k, i) => <Bar key={k} dataKey={k} fill={colors[i % colors.length]} radius={[4, 4, 0, 0]} />)}
          </BarChart>
        );
    }
  })();

  return (
    <div style={{ marginTop: 10, padding: '10px 12px 6px', background: 'var(--ab-bg)', border: '1px solid var(--ab-border)', borderRadius: 10 }}>
      {spec.title && (
        <div style={{ fontSize: 11.5, fontWeight: 600, color: 'var(--ab-fg)', marginBottom: 6 }}>{spec.title}</div>
      )}
      <div style={{ width: '100%', height: 220 }}>
        <ResponsiveContainer width="100%" height="100%">
          {body}
        </ResponsiveContainer>
      </div>
    </div>
  );
}
