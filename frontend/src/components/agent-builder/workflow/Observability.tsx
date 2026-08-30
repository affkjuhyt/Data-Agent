import React from 'react';
import {
  Activity,
  TrendingUp,
  TrendingDown,
  Clock,
  Zap,
  CheckCircle2,
  Layers,
  Download,
  Bot,
  Database,
  Cpu,
  ArrowRight
} from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area } from 'recharts';

const PERFORMANCE_DATA = [
  { time: '09:00', success: 400, failure: 20 },
  { time: '10:00', success: 300, failure: 10 },
  { time: '11:00', success: 500, failure: 50 },
  { time: '12:00', success: 280, failure: 5 },
  { time: '13:00', success: 390, failure: 15 },
  { time: '14:00', success: 430, failure: 10 },
];

const COST_DATA = [
  { time: 'Mon', cost: 120 },
  { time: 'Tue', cost: 150 },
  { time: 'Wed', cost: 420 },
  { time: 'Thu', cost: 300 },
  { time: 'Fri', cost: 280 },
  { time: 'Sat', cost: 110 },
  { time: 'Sun', cost: 95 },
];

const WorkflowObservability = () => {
  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="bg-white border border-[#ECE7DA] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/50">
              <Activity className="w-5 h-5 text-[#7C5A0E]" />
            </div>
            <div>
              <h2 className="text-lg font-display font-bold text-[#111111]">Workflow Observability</h2>
              <p className="text-xs text-[#8A8A7A] mt-0.5">Execution volume, latency, cost, and bottleneck analysis</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button className="btn-secondary">
              <Download className="w-3.5 h-3.5" /> Export Report
            </button>
            <div className="flex p-1 bg-[#F8F5EE] border border-[#ECE7DA] rounded-xl gap-1">
              <button className="px-3 py-1.5 bg-[#B88719] text-white rounded-lg text-[10px] font-black uppercase tracking-widest shadow-sm">Last 24h</button>
              <button className="px-3 py-1.5 text-[#8A8A7A] hover:text-[#111111] rounded-lg text-[10px] font-black uppercase tracking-widest transition-all">7 days</button>
              <button className="px-3 py-1.5 text-[#8A8A7A] hover:text-[#111111] rounded-lg text-[10px] font-black uppercase tracking-widest transition-all">30 days</button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {[
            { label: 'Avg Latency', value: '42.1s', trend: '+2.4%', up: false, icon: Clock },
            { label: 'Token Consumption', value: '42.1M', trend: '+15.2%', up: true, icon: Layers },
            { label: 'Infrastructure Spend', value: '$1.24k', trend: '-4.1%', up: false, icon: Zap },
            { label: 'Success Rate', value: '99.82%', trend: '+0.1%', up: true, icon: CheckCircle2 },
          ].map((stat) => (
            <div key={stat.label} className="p-5 bg-[#FAFAF5] border border-[#ECE7DA] rounded-2xl hover:border-[#BFA66A] transition-all group">
              <div className="flex items-center justify-between mb-3">
                <div className="p-2.5 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/40 group-hover:bg-[#EDD98A] transition-all">
                  <stat.icon className="w-4 h-4 text-[#7C5A0E]" />
                </div>
                <div className={`flex items-center gap-1 text-[10px] font-bold ${stat.up ? 'text-emerald-600' : 'text-red-500'}`}>
                  {stat.up ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                  {stat.trend}
                </div>
              </div>
              <div className="text-2xl font-display font-bold text-[#111111] mb-0.5">{stat.value}</div>
              <div className="text-[10px] text-[#8A8A7A] font-semibold uppercase tracking-widest">{stat.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Success/Failure Chart */}
        <div className="p-6 bg-white border border-[#ECE7DA] rounded-2xl">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="text-base font-bold text-[#111111] font-display">Execution Volume</h3>
              <p className="text-xs text-[#8A8A7A] mt-0.5">Total workflow runs vs failure rates</p>
            </div>
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-[#B88719]" />
                <span className="text-[10px] font-semibold text-[#8A8A7A] uppercase tracking-widest">Success</span>
              </div>
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-red-400" />
                <span className="text-[10px] font-semibold text-[#8A8A7A] uppercase tracking-widest">Failure</span>
              </div>
            </div>
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={PERFORMANCE_DATA}>
                <CartesianGrid strokeDasharray="3 3" stroke="#ECE7DA" vertical={false} />
                <XAxis dataKey="time" stroke="#8A8A7A" fontSize={10} axisLine={false} tickLine={false} />
                <YAxis stroke="#8A8A7A" fontSize={10} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#FFFFFF', border: '1px solid #ECE7DA', borderRadius: '12px', fontSize: '12px', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}
                  itemStyle={{ color: '#111111', fontWeight: '600' }}
                  labelStyle={{ color: '#5A5A5A', fontWeight: '700' }}
                />
                <Bar dataKey="success" fill="#B88719" radius={[4, 4, 0, 0]} />
                <Bar dataKey="failure" fill="#EF4444" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Cost Allocation Chart */}
        <div className="p-6 bg-white border border-[#ECE7DA] rounded-2xl">
          <div className="flex items-center justify-between mb-6">
            <div>
              <h3 className="text-base font-bold text-[#111111] font-display">Cost Trends</h3>
              <p className="text-xs text-[#8A8A7A] mt-0.5">Daily infrastructure and token spend</p>
            </div>
            <div className="p-2 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/50">
              <Zap className="w-4 h-4 text-[#7C5A0E]" />
            </div>
          </div>
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={COST_DATA}>
                <defs>
                  <linearGradient id="colorCostLight" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#B88719" stopOpacity={0.2}/>
                    <stop offset="95%" stopColor="#B88719" stopOpacity={0}/>
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="#ECE7DA" vertical={false} />
                <XAxis dataKey="time" stroke="#8A8A7A" fontSize={10} axisLine={false} tickLine={false} />
                <YAxis stroke="#8A8A7A" fontSize={10} axisLine={false} tickLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#FFFFFF', border: '1px solid #ECE7DA', borderRadius: '12px', fontSize: '12px', boxShadow: '0 4px 12px rgba(0,0,0,0.08)' }}
                  itemStyle={{ color: '#111111', fontWeight: '600' }}
                  labelStyle={{ color: '#5A5A5A', fontWeight: '700' }}
                />
                <Area type="monotone" dataKey="cost" stroke="#B88719" strokeWidth={2.5} fillOpacity={1} fill="url(#colorCostLight)" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      {/* Bottleneck Detection Table */}
      <div className="bg-white border border-[#ECE7DA] rounded-2xl p-6">
        <h3 className="text-base font-bold text-[#111111] font-display mb-5">Workflow Bottleneck Analysis</h3>
        <div className="space-y-3">
          {[
            { type: 'AI_AGENT', node: 'Banking_Triage_v2.4', issue: 'Context Window Overflow', impact: 'High (Latency +4s)', icon: Bot },
            { type: 'KB_RETRIEVAL', node: 'GlobalDoc_Search', issue: 'Index Cache Misses', impact: 'Medium (+2.1s)', icon: Database },
            { type: 'INFRA', node: 'API_Gateway_West', issue: 'Throughput Throttle', impact: 'Medium (90% Cap)', icon: Cpu },
          ].map((issue, i) => (
            <div key={i} className="flex items-center justify-between p-4 bg-[#FAFAF5] hover:bg-[#F8F5EE] border border-[#ECE7DA] hover:border-[#BFA66A] rounded-2xl transition-all group">
              <div className="flex items-center gap-4">
                <div className="p-2.5 bg-white border border-[#ECE7DA] rounded-xl group-hover:border-[#BFA66A] transition-all">
                  <issue.icon className="w-4 h-4 text-[#8A8A7A] group-hover:text-[#B88719] transition-colors" />
                </div>
                <div>
                  <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors">{issue.node}</div>
                  <div className="text-[10px] text-[#8A8A7A] font-medium uppercase tracking-widest mt-0.5">{issue.issue}</div>
                </div>
              </div>
              <div className="flex items-center gap-6">
                <div className="text-right">
                  <div className="text-xs font-bold text-amber-600">{issue.impact}</div>
                  <div className="text-[9px] font-semibold text-[#8A8A7A] uppercase tracking-widest">Impact Analysis</div>
                </div>
                <button className="p-2 hover:bg-[#F3E2A7] rounded-xl transition-all text-[#8A8A7A] hover:text-[#7C5A0E]">
                  <ArrowRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

export default WorkflowObservability;
