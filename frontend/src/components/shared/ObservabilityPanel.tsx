import React from 'react';
import { cn } from '../../lib/utils';
import { ArrowUpRight, ArrowDownRight } from 'lucide-react';

interface MetricCardProps {
  label: string;
  value: string | number;
  trend?: string;
  trendType?: 'UP' | 'DOWN' | 'NEUTRAL';
  icon: any;
  color?: 'brand' | 'emerald' | 'amber' | 'red' | 'blue';
  description?: string;
}

/* High-contrast warm icon tints */
const iconStyles: Record<string, { bg: string; border: string; text: string }> = {
  brand:   { bg: 'bg-[#FFF1BF]', border: 'border-[#D9A441]', text: 'text-[#7A4D00]' },
  emerald: { bg: 'bg-[#E7F2D4]', border: 'border-[#8AA63F]', text: 'text-[#3F6212]' },
  amber:   { bg: 'bg-[#FFEFCC]', border: 'border-[#D9A441]', text: 'text-[#7A4D00]' },
  red:     { bg: 'bg-[#FAD7D7]', border: 'border-[#C94A4A]', text: 'text-[#9F1D1D]' },
  blue:    { bg: 'bg-[#E2EAF8]', border: 'border-[#6B8FCB]', text: 'text-[#1E3A8A]' },
};

const trendStyles = {
  UP:      { text: 'text-[#3F6212]', icon: ArrowUpRight },
  DOWN:    { text: 'text-[#9F1D1D]', icon: ArrowDownRight },
  NEUTRAL: { text: 'text-[#5F5F5F]', icon: null as any },
};

const MetricCard = ({
  label, value, trend, trendType = 'NEUTRAL', icon: Icon, color = 'brand', description,
}: MetricCardProps) => {
  const ic    = iconStyles[color];
  const tr    = trendStyles[trendType];
  const TIcon = tr.icon;

  return (
    <div className="content-card p-5 group relative overflow-hidden">
      <div className="flex items-center justify-between mb-5">
        <div className={cn('p-2.5 rounded-xl border', ic.bg, ic.border)}>
          <Icon className={cn('w-4 h-4', ic.text)} />
        </div>
        {trend && (
          <div className={cn('flex items-center gap-0.5 text-[11px] font-bold', tr.text)}>
            {TIcon && <TIcon className="w-3 h-3" />}
            {trend}
          </div>
        )}
      </div>

      <div>
        <div className="text-2xl font-bold text-[#111111] leading-none mb-1 font-display">{value}</div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-[#5F5F5F]">{label}</div>
        {description && (
          <p className="text-[12px] text-[#3F3F3F] mt-2 leading-relaxed">{description}</p>
        )}
      </div>
    </div>
  );
};

export const StandardMetricsGrid = ({ metrics }: { metrics: any[] }) => (
  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
    {metrics.map(m => <MetricCard key={m.label} {...m} />)}
  </div>
);
