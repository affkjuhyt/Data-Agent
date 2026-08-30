import React from 'react';
import { 
  Zap, 
  Server, 
  Cpu, 
  Globe, 
  Activity, 
  Layers, 
  Box, 
  Database,
  ArrowUpRight,
  ShieldCheck,
  RefreshCw,
  Terminal,
  ExternalLink,
  Plus
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { cn } from '../../lib/utils';

export const InfrastructureSection = () => {
  return (
    <div className="space-y-10 lg:space-y-12 pb-20 no-scrollbar">
      <OperationalHeader 
        title="Runtime Infrastructure"
        subtitle="Compute fleet, Kubernetes cluster topology, and worker distribution."
        breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'Infrastructure' }]}
        status={<StatusBadge status="OPTIMAL" size="lg" />}
        actions={
          <div className="flex flex-col sm:flex-row gap-3">
             <button className="flex items-center justify-center gap-2 bg-white/5 hover:bg-white/10 text-white px-5 py-2.5 rounded-2xl text-xs font-bold border border-white/5 transition-all">
                <Terminal className="w-4 h-4" />
                SHELL ACCESS
             </button>
             <button className="flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white px-5 py-2.5 rounded-2xl text-xs font-bold transition-all shadow-lg shadow-brand-500/20 active:scale-95">
               <Plus className="w-4 h-4" />
               SCALE CLUSTER
             </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-8 lg:space-y-10">
          {/* Cluster Status */}
          <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
             <div className="flex flex-col md:flex-row md:items-center justify-between gap-6 mb-10">
                <div className="flex items-center gap-3">
                   <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                     <Layers className="w-6 h-6" />
                   </div>
                   <div>
                     <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Global Worker Fleet</h3>
                     <p className="text-[10px] lg:text-xs text-slate-500 uppercase font-black tracking-widest mt-1">Multi-region Provisioning Matrix</p>
                   </div>
                </div>
                <div className="flex items-center gap-4 bg-white/5 border border-white/5 px-4 py-2 rounded-2xl">
                   <div className="text-right">
                      <div className="text-xs font-bold text-white">4.12.0-LTS</div>
                      <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Runtime VSN</div>
                   </div>
                   <div className="w-px h-8 bg-white/10" />
                   <div className="text-right">
                      <div className="text-xs font-bold text-brand-400">920 Nodes</div>
                      <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Active Pool</div>
                   </div>
                </div>
             </div>

             <div className="space-y-4">
                {[
                   { name: 'K8S-P-EAST-01', region: 'US-EAST-1', type: 'COMPUTE_OPTIMIZED', health: 99, workers: 420 },
                   { name: 'K8S-P-WEST-02', region: 'US-WEST-2', type: 'GPU_NVIDIA_H100', health: 98, workers: 84 },
                   { name: 'K8S-P-EURO-01', region: 'EU-CENTRAL-1', type: 'GENERAL_PURPOSE', health: 94, workers: 320 },
                   { name: 'K8S-D-COAST-01', region: 'US-EAST-2', type: 'DEV_SANDBOX', health: 100, workers: 12 },
                ].map((cluster, i) => (
                   <div key={cluster.name} className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between p-6 bg-white/[0.03] border border-white/5 rounded-[2rem] hover:bg-white/[0.06] transition-all group gap-6 lg:gap-0">
                      <div className="flex items-center gap-5">
                         <div className="w-14 h-14 rounded-2xl bg-slate-900 border border-white/10 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors shadow-inner relative">
                            <Box className="w-7 h-7" />
                            <div className="absolute -top-1 -right-1 w-4 h-4 bg-emerald-500 rounded-full border-4 border-[#050505]" />
                         </div>
                         <div>
                            <h4 className="text-base lg:text-lg font-bold text-white tracking-tight">{cluster.name}</h4>
                            <div className="flex flex-wrap items-center gap-3 mt-1">
                               <span className="flex items-center gap-1.5 text-[9px] font-black text-slate-500 uppercase tracking-widest">
                                  <Globe className="w-3 h-3" /> {cluster.region}
                               </span>
                               <div className="w-1 h-1 rounded-full bg-slate-800" />
                               <span className="text-[10px] font-mono text-brand-500/80 uppercase tracking-widest">{cluster.type}</span>
                            </div>
                         </div>
                      </div>
                      
                      <div className="flex items-center gap-10 justify-between lg:justify-end border-t lg:border-t-0 border-white/5 pt-4 lg:pt-0">
                         <div className="grid grid-cols-2 gap-8">
                            <div className="text-right">
                               <div className="text-sm font-bold text-white tracking-tighter">{cluster.workers}</div>
                               <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Nodes</div>
                            </div>
                            <div className="text-right">
                               <div className={cn(
                                  "text-sm font-bold tracking-tighter",
                                  cluster.health > 95 ? "text-emerald-400" : "text-amber-400"
                               )}>{cluster.health}%</div>
                               <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Health</div>
                            </div>
                         </div>
                         <button className="p-3 bg-white/5 border border-white/10 rounded-xl text-slate-600 hover:text-white transition-all transform group-hover:scale-110 active:scale-95">
                            <ArrowUpRight className="w-5 h-5" />
                         </button>
                      </div>
                   </div>
                ))}
             </div>
          </section>

          {/* Provisioning Control */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-10">
             <section className="glass-panel p-8 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
                <h3 className="text-lg lg:text-xl font-bold text-white mb-8 italic flex items-center gap-3">
                   <Activity className="w-5 h-5 text-brand-400" />
                   Scaling Policy
                </h3>
                <div className="space-y-8">
                   <div className="p-6 bg-white/5 rounded-3xl border border-white/5">
                      <div className="flex justify-between items-center mb-4">
                         <span className="text-xs font-bold text-white uppercase italic tracking-tight">Auto-Scalar V2</span>
                         <StatusBadge status="ACTIVE" />
                      </div>
                      <p className="text-[11px] text-slate-500 italic leading-relaxed">Dynamic pod distribution based on neural load and queue pressure.</p>
                   </div>
                   <div className="space-y-4">
                      <div className="flex justify-between items-center text-[10px] uppercase font-black tracking-widest">
                         <span className="text-slate-600">Reserved Instances</span>
                         <span className="text-white">82%</span>
                      </div>
                      <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                         <div className="h-full bg-brand-500 rounded-full w-[82%]" />
                      </div>
                   </div>
                </div>
             </section>

             <section className="glass-panel p-8 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
                <h3 className="text-lg lg:text-xl font-bold text-white mb-8 italic flex items-center gap-3">
                   <ShieldCheck className="w-5 h-5 text-emerald-400" />
                   Fleet Security
                </h3>
                <div className="space-y-6">
                   {[
                      { label: 'mTLS Enforcement', status: true },
                      { label: 'Egress Filtering', status: true },
                      { label: 'Privileged Mode', status: false },
                   ].map(sec => (
                      <div key={sec.label} className="flex items-center justify-between p-4 bg-white/[0.02] border border-white/10 rounded-2xl">
                         <span className="text-xs font-bold text-slate-300">{sec.label}</span>
                         <div className={cn(
                            "px-2 py-0.5 rounded text-[8px] font-black uppercase tracking-widest",
                            sec.status ? "bg-emerald-500 text-black" : "bg-red-500/20 text-red-500"
                         )}>
                            {sec.status ? 'ENABLED' : 'DISABLED'}
                         </div>
                      </div>
                   ))}
                </div>
             </section>
          </div>
        </div>

        <div className="space-y-8 lg:space-y-10">
           {/* Real-time Fleet Telemetry */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-[#02040d]">
              <div className="flex items-center justify-between mb-8">
                 <h3 className="text-lg lg:text-xl font-bold text-white tracking-tight italic">Fleet Engine</h3>
                 <RefreshCw className="w-4 h-4 text-slate-700 animate-spin-slow" />
              </div>
              
              <div className="space-y-8">
                 <div className="grid grid-cols-2 gap-4">
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <Cpu className="w-6 h-6 text-brand-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">14k</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Total Cores</div>
                    </div>
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <Database className="w-6 h-6 text-emerald-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">4.2 PB</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Agg RAM</div>
                    </div>
                 </div>

                 <div className="space-y-6">
                    <div>
                       <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-slate-600">
                          <span>Aggregate CPU Load</span>
                          <span className="text-white">42.4%</span>
                       </div>
                       <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                          <div className="h-full bg-brand-500 w-[42.4%] rounded-full shadow-[0_0_10px_rgba(59,130,246,0.3)] transition-all" />
                       </div>
                    </div>
                    <div>
                       <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-slate-600">
                          <span>Network Throughput</span>
                          <span className="text-white">8.4 Gb/s</span>
                       </div>
                       <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                          <div className="h-full bg-emerald-500 w-[68%] rounded-full shadow-[0_0_10px_rgba(16,185,129,0.3)] transition-all" />
                       </div>
                    </div>
                 </div>

                 <div className="pt-6 border-t border-white/5">
                    <button className="w-full flex items-center justify-center gap-3 py-4 bg-white/5 hover:bg-white/10 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">
                       <Zap className="w-4 h-4 text-brand-400" />
                       Infrastructure Trace
                    </button>
                 </div>
              </div>
           </section>

           {/* Cloud Health Meta */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
              <h3 className="text-[11px] font-black text-slate-400 uppercase tracking-[0.3em] mb-8 lg:mb-10 flex items-center gap-3">
                <Globe className="w-4 h-4 text-slate-600" />
                Cloud Provider Integrations
              </h3>
              
              <div className="space-y-4">
                 {[
                    { label: 'AWS Primary (US-EAST-1)', status: 'HEALTHY' },
                    { label: 'GCP Backup (ASIA-SOUTHEAST-1)', status: 'HEALTHY' },
                    { label: 'On-Prem Core (G7-DC)', status: 'OFFLINE' },
                 ].map(site => (
                    <div key={site.label} className="flex items-center justify-between p-4 bg-white/5 border border-white/5 rounded-2xl hover:bg-white/[0.08] transition-all cursor-pointer">
                       <span className="text-[10px] font-bold text-slate-300">{site.label}</span>
                       <StatusBadge status={site.status as any} size="sm" />
                    </div>
                 ))}
              </div>

              <div className="mt-8 flex justify-center">
                 <button className="text-[9px] font-black text-brand-400 uppercase tracking-widest hover:underline flex items-center gap-2">
                    Manage Cloud Hub <ExternalLink size={12} />
                 </button>
              </div>
           </section>
        </div>
      </div>
    </div>
  );
};
