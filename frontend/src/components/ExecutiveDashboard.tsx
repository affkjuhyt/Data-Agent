import React from 'react';
import {
  TrendingUp,
  Activity,
  ShieldCheck,
  Database,
  Calendar,
  ChevronRight,
  ArrowRight,
  Zap,
  Globe,
  Plus,
  Sparkles,
  Bot,
  Layers,
  Clock,
} from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../lib/utils';
import { 
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer
} from 'recharts';
import { OperationalHeader } from './shared/OperationalHeader';
import { StatusBadge } from './shared/StatusBadge';
import { StandardMetricsGrid } from './shared/ObservabilityPanel';

const DATA = [
  { name: 'Mon', value: 400, cost: 240 },
  { name: 'Tue', value: 700, cost: 380 },
  { name: 'Wed', value: 600, cost: 310 },
  { name: 'Thu', value: 900, cost: 520 },
  { name: 'Fri', value: 1100, cost: 680 },
  { name: 'Sat', value: 1000, cost: 590 },
  { name: 'Sun', value: 1200, cost: 720 },
];

const TOP_ISSUES = [
  { label: 'Ingestion pipeline failed in EU-WEST-1', risk: 'HIGH', impact: '24 bots' },
  { label: 'Unresolved knowledge conflict in Compliance', risk: 'MEDIUM', impact: '4 users' },
  { label: 'Embedding cost spike detected', risk: 'LOW', impact: 'Finance alert' },
];

const AI_RUNTIME_METRICS = [
  { label: 'Active Agents',  value: '482',    trend: '+5',      trendUp: true,  icon: Bot      },
  { label: 'Workflow Runs',  value: '1.2M',   trend: '+18%',    trendUp: true,  icon: Layers   },
  { label: 'Avg Latency',    value: '1.4s',   trend: '−120ms',  trendUp: false, icon: Clock    },
  { label: 'Success Rate',   value: '99.98%', trend: 'OPTIMAL', trendUp: true,  icon: Activity },
];

export const ExecutiveDashboard = () => {
  const mainMetrics = [
    { label: 'Knowledge Freshness', value: '98.2%', trend: '+0.5%', trendType: 'UP' as const, icon: Database, color: 'brand' as const },
    { label: 'Mean Hallucination Risk', value: '0.14%', trend: '-0.02%', trendType: 'DOWN' as const, icon: ShieldCheck, color: 'emerald' as const },
    { label: 'Calculated Cost Savings', value: '$3.4M', trend: '+$420k', trendType: 'UP' as const, icon: TrendingUp, color: 'blue' as const },
    { label: 'Platform Availability', value: '99.999%', trend: 'OPTIMAL', trendType: 'NEUTRAL' as const, icon: Activity, color: 'amber' as const },
  ];

  return (
    <div className="space-y-10 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <OperationalHeader 
        title="Executive Pulse"
        subtitle="Operational Intelligence & Human-AI Lifecycle Overview"
        breadcrumbs={[{ label: 'Executive' }, { label: 'Pulse' }]}
        status={<StatusBadge status="STABLE" size="lg" />}
        actions={
          <div className="flex gap-3">
             <button className="btn-secondary">
                <Calendar className="w-4 h-4" />
                LAST 30 DAYS
              </button>
              <button className="btn-primary">
                DOWNLOAD REPORT
              </button>
          </div>
        }
      />

      <StandardMetricsGrid metrics={mainMetrics} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main Operational Chart */}
        <div className="lg:col-span-2 p-7 bg-white border border-[#ECE7DA] rounded-3xl">
          <div className="flex items-center justify-between mb-8">
            <div>
              <h3 className="text-lg font-semibold text-[#171717] tracking-tight font-display">Processing Throughput</h3>
              <p className="text-xs text-[#8B8B8B] mt-0.5">Semantic ingestion vs token generation across fabric</p>
            </div>
            <div className="flex gap-2">
               <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#FAF1D0] rounded-full border border-[#D9B86C]/30 text-[9px] font-semibold text-[#7C6230]">
                 <div className="w-1.5 h-1.5 rounded-full bg-[#D9B86C] animate-pulse" />
                 INGESTION
               </div>
               <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#EFF4FB] rounded-full border border-[#A8C0E4]/40 text-[9px] font-semibold text-[#2D5A9C]">
                 <div className="w-1.5 h-1.5 rounded-full bg-[#6B9CD4] animate-pulse" />
                 GENERATION
               </div>
            </div>
          </div>

          <div className="h-[360px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={DATA}>
                <defs>
                  <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#D9B86C" stopOpacity={0.25}/>
                    <stop offset="95%" stopColor="#D9B86C" stopOpacity={0}/>
                  </linearGradient>
                  <linearGradient id="colorCost" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#6B9CD4" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#6B9CD4" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#F0EBE0" vertical={false} />
                <XAxis
                  dataKey="name"
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: '#8B8B8B', fontSize: 11 }}
                  dy={10}
                />
                <YAxis
                  axisLine={false}
                  tickLine={false}
                  tick={{ fill: '#8B8B8B', fontSize: 11 }}
                />
                <Tooltip
                  contentStyle={{ backgroundColor: '#FFFFFF', border: '1px solid #ECE7DA', borderRadius: '12px', boxShadow: '0 4px 16px rgba(0,0,0,0.08)', color: '#171717' }}
                  itemStyle={{ fontSize: '11px', fontWeight: '600', color: '#4A4A4A' }}
                  labelStyle={{ color: '#171717', fontWeight: '700' }}
                />
                <Area type="monotone" dataKey="value" stroke="#D9B86C" strokeWidth={2.5} fillOpacity={1} fill="url(#colorValue)" />
                <Area type="monotone" dataKey="cost"  stroke="#6B9CD4" strokeWidth={2.5} fillOpacity={1} fill="url(#colorCost)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Risks + AI Insights */}
        <div className="space-y-5">
          <div className="p-6 bg-white border border-[#ECE7DA] rounded-3xl">
            <h3 className="text-base font-semibold text-[#171717] tracking-tight font-display mb-5">Active Risks</h3>
            <div className="space-y-4">
              {TOP_ISSUES.map((issue, i) => (
                <div key={issue.label} className="group cursor-pointer">
                  <div className="flex items-start justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className={cn(
                        'w-2 h-2 rounded-full shrink-0',
                        issue.risk === 'HIGH'   ? 'bg-[#C0504A]' :
                        issue.risk === 'MEDIUM' ? 'bg-[#D9A040]' : 'bg-[#B0A99A]'
                      )} />
                      <span className="text-[9px] font-semibold uppercase tracking-widest text-[#8B8B8B]">
                        {issue.risk} RISK
                      </span>
                    </div>
                    <ChevronRight className="w-4 h-4 text-[#D4CBBA] group-hover:text-[#D9B86C] transition-colors" />
                  </div>
                  <p className="text-[13px] font-medium text-[#232323] group-hover:text-[#171717] transition-colors leading-snug">{issue.label}</p>
                  <p className="text-[10px] text-[#8B8B8B] mt-1">Impact: {issue.impact}</p>
                  {i < TOP_ISSUES.length - 1 && <div className="h-px bg-[#F0EBE0] mt-4" />}
                </div>
              ))}
            </div>
          </div>

          {/* AI Insight block */}
          <div className="p-5 bg-gradient-to-br from-[#FAF1D0] to-[#FEFCF4] border border-[#E2C57E]/60 rounded-3xl relative overflow-hidden">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-[#C4A35A]" />
              <span className="text-[10px] font-semibold text-[#7C6230] uppercase tracking-wide">AI Copilot Insight</span>
            </div>
            <p className="text-[13px] text-[#4A4A4A] leading-relaxed">
              System efficiency has improved 14% since last model promotion. EU-WEST latency spike correlates with the new embedding release.
            </p>
            <button className="mt-4 flex items-center gap-1.5 text-[10px] font-semibold text-[#A07830] hover:text-[#7C6230] transition-colors uppercase tracking-wide">
              Read Full Analysis <ArrowRight className="w-3 h-3" />
            </button>
          </div>
        </div>
      </div>

      {/* AI Runtime snapshot */}
      <div className="p-6 bg-white border border-[#ECE7DA] rounded-3xl">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2.5">
            <div className="p-2 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/50">
              <Activity className="w-4 h-4 text-[#7C5A0E]" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-[#171717] font-display">AI Runtime</h3>
              <p className="text-[11px] text-[#8B8B8B]">Live agent & workflow health</p>
            </div>
          </div>
          <button
            className="flex items-center gap-1.5 text-[11px] font-semibold text-[#8A5A00] hover:text-[#5A3A00] transition-colors"
          >
            View full runtime <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {AI_RUNTIME_METRICS.map(m => (
            <div key={m.label} className="flex items-center gap-3 p-3.5 bg-[#FAFAF5] border border-[#ECE7DA] rounded-2xl">
              <div className="p-2 bg-[#F3E2A7]/60 rounded-lg border border-[#BFA66A]/30 shrink-0">
                <m.icon className="w-4 h-4 text-[#B88719]" />
              </div>
              <div className="min-w-0">
                <div className="text-xl font-bold font-display text-[#111111] leading-tight">{m.value}</div>
                <div className="text-[10px] text-[#8B8B8B] font-medium truncate">{m.label}</div>
              </div>
              <span className={cn('ml-auto text-[10px] font-semibold shrink-0', m.trendUp ? 'text-emerald-600' : 'text-sky-600')}>
                {m.trend}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
