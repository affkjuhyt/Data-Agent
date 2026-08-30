import React from 'react';
import { 
  ShieldCheck, 
  Zap, 
  Cpu, 
  Key, 
  Database, 
  AlertCircle, 
  CheckCircle2, 
  TrendingUp,
  Globe,
  Lock,
  ArrowRight
} from 'lucide-react';
import { motion } from 'motion/react';

const STATUS_CARDS = [
  { id: 'sec', label: 'Security Score', value: '94/100', status: 'HEALTHY', icon: ShieldCheck, color: 'brand' },
  { id: 'prov', label: 'AI Providers', value: '8 Active', status: 'STABLE', icon: Cpu, color: 'emerald' },
  { id: 'env', label: 'Environments', value: '4 Active', status: 'DIVERGED', icon: Globe, color: 'amber' },
  { id: 'secr', label: 'Secrets', value: '42 Total', status: 'EXPIRED_2', icon: Key, color: 'red' },
];

import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { StandardMetricsGrid } from '../shared/ObservabilityPanel';

export const OverviewSection = () => {
  const mainMetrics = [
    { label: 'Security Score', value: '94/100', trend: 'OPTIMAL', trendType: 'NEUTRAL' as const, icon: ShieldCheck, color: 'brand' as const },
    { label: 'Active Providers', value: '8 Nodes', trend: 'STABLE', trendType: 'NEUTRAL' as const, icon: Cpu, color: 'emerald' as const },
    { label: 'Env Sync State', value: 'DIVERGED', trend: '3 NODES', trendType: 'DOWN' as const, icon: Globe, color: 'amber' as const },
    { label: 'Secret Health', value: '42 Total', trend: '2 EXPIRED', trendType: 'DOWN' as const, icon: Key, color: 'red' as const },
  ];

  return (
    <div className="space-y-12">
      <OperationalHeader 
        title="Platform Pulse"
        subtitle="Operational Health & Configuration Sovereignty"
        breadcrumbs={[{ label: 'Settings' }, { label: 'Pulse' }]}
        status={<StatusBadge status="STABLE" size="lg" />}
        actions={
          <button className="flex items-center gap-2 bg-brand-500 hover:bg-brand-600 text-white px-5 py-2.5 rounded-2xl text-xs font-bold transition-all shadow-lg shadow-brand-500/20 active:scale-95">
            <TrendingUp className="w-4 h-4" />
            ANALYZE DRIFT
          </button>
        }
      />

      <StandardMetricsGrid metrics={mainMetrics} />

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 space-y-8">
          <div className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px]">
            <div className="flex items-center justify-between mb-8">
              <h3 className="text-xl font-bold text-white tracking-tight">Active Workspaces & Projects</h3>
              <button className="text-xs font-bold text-brand-400 hover:text-brand-300">View Hierarchy</button>
            </div>
            
            <div className="space-y-4">
              {[
                { name: 'Global Finance', projects: 12, envs: ['PROD', 'STAGING', 'DEV'], health: 98 },
                { name: 'Smart City Nexus', projects: 8, envs: ['PROD', 'SIT'], health: 85 },
                { name: 'National Health OS', projects: 24, envs: ['PROD', 'UA'], health: 100 },
              ].map((ws) => (
                <div key={ws.name} className="flex items-center justify-between p-4 bg-white/5 hover:bg-white/[0.08] border border-white/5 rounded-2xl transition-all group">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-xl bg-slate-900 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors shadow-inner">
                      <Database className="w-5 h-5" />
                    </div>
                    <div>
                      <div className="text-white font-bold text-sm tracking-tight">{ws.name}</div>
                      <div className="text-[10px] text-slate-500 uppercase font-black tracking-widest">{ws.projects} Active Projects</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-8">
                    <div className="flex gap-1.5">
                      {ws.envs.map(env => (
                        <span key={env} className="text-[9px] font-black text-slate-600 bg-white/5 px-1.5 py-0.5 rounded border border-white/10">{env}</span>
                      ))}
                    </div>
                    <div className="text-right">
                       <div className={`text-xs font-bold ${ws.health === 100 ? 'text-emerald-400' : 'text-amber-400'}`}>{ws.health}%</div>
                       <div className="text-[9px] text-slate-600 uppercase font-black tracking-widest">Health</div>
                    </div>
                    <ArrowRight className="w-4 h-4 text-slate-700 opacity-0 group-hover:opacity-100 transition-all" />
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-8">
            <div className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px]">
              <h3 className="text-lg font-bold text-white mb-6">Compliance Score</h3>
              <div className="relative w-32 h-32 mx-auto">
                <svg className="w-full h-full -rotate-90">
                   <circle cx="64" cy="64" r="60" fill="none" stroke="currentColor" strokeWidth="8" className="text-white/5" />
                   <circle cx="64" cy="64" r="60" fill="none" stroke="currentColor" strokeWidth="8" className="text-brand-500" strokeDasharray="376.8" strokeDashoffset="40" />
                </svg>
                <div className="absolute inset-0 flex flex-col items-center justify-center">
                   <span className="text-2xl font-bold text-white">88%</span>
                   <span className="text-[8px] font-black text-slate-500 uppercase tracking-widest">SOC2/GDPR</span>
                </div>
              </div>
              <p className="text-[10px] text-slate-500 text-center mt-6 leading-relaxed italic">
                3 high-risk policies detected in Staging environment.
              </p>
            </div>
            <div className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px]">
               <h3 className="text-lg font-bold text-white mb-6">AI Provider Health</h3>
               <div className="space-y-4">
                 {[
                   { name: 'Azure OpenAI', status: 'HEALTHY' },
                   { name: 'Anthropic', status: 'DEGRADED' },
                   { name: 'Gemini Pro', status: 'HEALTHY' },
                 ].map(prov => (
                   <div key={prov.name} className="flex items-center justify-between">
                     <span className="text-xs font-medium text-slate-300">{prov.name}</span>
                     <div className={`w-2 h-2 rounded-full ${prov.status === 'HEALTHY' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.5)]'}`} />
                   </div>
                 ))}
               </div>
               <button className="w-full mt-10 py-3 bg-white/5 hover:bg-brand-500 text-slate-300 hover:text-white border border-white/10 hover:border-transparent rounded-2xl text-[10px] font-black uppercase tracking-widest transition-all">
                  Provider Center
               </button>
            </div>
          </div>
        </div>

        <div className="space-y-8">
           <div className="p-8 bg-gradient-to-br from-brand-500/10 to-transparent border border-brand-500/20 rounded-[40px]">
             <Zap className="w-8 h-8 text-brand-400 mb-6" />
             <h3 className="text-xl font-bold text-white mb-2 leading-tight">Runtime Optimization Available</h3>
             <p className="text-sm text-slate-400 leading-relaxed mb-8 italic">
               Detected 12 idle workers in US-EAST region. Estimated savings: $422/mo.
             </p>
             <button className="w-full py-4 bg-brand-500 hover:bg-brand-400 text-white rounded-2xl font-bold shadow-lg shadow-brand-500/20 transition-all">
               Optimize Now
             </button>
           </div>

           <div className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px]">
              <div className="flex items-center gap-2 mb-6">
                <AlertCircle className="w-5 h-5 text-red-400" />
                <h3 className="text-lg font-bold text-white italic">Critical Alerts</h3>
              </div>
              <div className="space-y-4">
                <div className="p-4 bg-red-400/5 border border-red-400/20 rounded-2xl">
                   <div className="text-[10px] font-black text-red-400 uppercase tracking-widest mb-1">SECRET_EXPIRATION</div>
                   <p className="text-xs text-slate-300 font-medium">Azure-Prod-Key expires in 2 days.</p>
                </div>
                <div className="p-4 bg-amber-400/5 border border-amber-400/20 rounded-2xl">
                   <div className="text-[10px] font-black text-amber-400 uppercase tracking-widest mb-1">QUOTA_LIMIT</div>
                   <p className="text-xs text-slate-300 font-medium">Workspace 'Health-OS' at 92% token quota.</p>
                </div>
              </div>
           </div>
        </div>
      </div>
    </div>
  );
};
