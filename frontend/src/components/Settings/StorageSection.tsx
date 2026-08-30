import React from 'react';
import { 
  Database, 
  HardDrive, 
  Cloud, 
  ShieldCheck, 
  Activity, 
  Layers, 
  Trash2, 
  Archive,
  RefreshCw,
  Server,
  LayoutGrid,
  ArrowUpRight,
  Plus
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { cn } from '../../lib/utils';

export const StorageSection = () => {
  return (
    <div className="space-y-10 lg:space-y-12 pb-20 no-scrollbar">
      <OperationalHeader 
        title="Storage & Retention"
        subtitle="Vector databases, blob storage management, and automated data lifecycle policies."
        breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'Storage' }]}
        status={<StatusBadge status="ACTIVE" size="lg" />}
        actions={
          <button className="w-full lg:w-auto flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white px-5 py-2.5 rounded-2xl text-xs font-bold transition-all shadow-lg shadow-brand-500/20 active:scale-95">
            <Plus className="w-4 h-4" />
            PROVISION VOLUME
          </button>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-8 lg:space-y-10">
          {/* Active Storage Clusters */}
          <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
             <div className="flex items-center gap-3 mb-10">
                <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                  <Database className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Global Storage Fleet</h3>
                  <p className="text-[10px] lg:text-xs text-slate-500 uppercase font-black tracking-widest mt-1">Multi-modal Data Persistence</p>
                </div>
             </div>

             <div className="space-y-4">
                {[
                   { name: 'Vector-Prod-Alpha', type: 'VECTOR_DB', provider: 'Pinecone / Enterprise', health: 100, volume: '4.2 TB' },
                   { name: 'KB-Sovereign-S3', type: 'BLOB_STORAGE', provider: 'AWS S3 (US-EAST)', health: 99, volume: '840 TB' },
                   { name: 'Audit-History-Cold', type: 'ARCHIVE_GLACIER', provider: 'Enterprise Vault', health: 94, volume: '12 PB' },
                   { name: 'Cache-Cluster-L1', type: 'REDIS_FAST', provider: 'Cloud Redis', health: 100, volume: '128 GB' },
                ].map((vol, i) => (
                   <div key={vol.name} className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between p-6 bg-white/[0.03] border border-white/5 rounded-[2rem] hover:bg-white/[0.06] transition-all group gap-6 lg:gap-0">
                      <div className="flex items-center gap-5">
                         <div className="w-14 h-14 rounded-2xl bg-slate-900 border border-white/10 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors shadow-inner relative">
                            <Server className="w-7 h-7" />
                            <div className="absolute -top-1 -right-1 w-4 h-4 bg-emerald-500 rounded-full border-4 border-[#050505]" />
                         </div>
                         <div>
                            <h4 className="text-base lg:text-lg font-bold text-white tracking-tight">{vol.name}</h4>
                            <div className="flex flex-wrap items-center gap-3 mt-1">
                               <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">{vol.provider}</span>
                               <div className="w-1 h-1 rounded-full bg-slate-800" />
                               <span className="text-[10px] font-mono text-brand-500/80 uppercase tracking-widest">{vol.type}</span>
                            </div>
                         </div>
                      </div>
                      
                      <div className="flex items-center gap-10 justify-between lg:justify-end border-t lg:border-t-0 border-white/5 pt-4 lg:pt-0">
                         <div className="grid grid-cols-2 gap-8">
                            <div className="text-right">
                               <div className="text-sm font-bold text-white tracking-tighter">{vol.volume}</div>
                               <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Storage</div>
                            </div>
                            <div className="text-right">
                               <div className={cn(
                                  "text-sm font-bold tracking-tighter",
                                  vol.health > 95 ? "text-emerald-400" : "text-amber-400"
                               )}>{vol.health}%</div>
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

          {/* Lifecycle & Deletion Policies */}
          <section className="glass-panel p-8 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
             <h3 className="text-lg lg:text-xl font-bold text-white mb-8 italic flex items-center gap-3">
                <Archive className="w-5 h-5 text-brand-400" />
                Data Lifecycle Automation
             </h3>
             <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {[
                   { label: 'Auto-Archival (Gold Tier)', desc: 'Move documents to cold storage after 12 months of inactivity.', icon: Archive, delay: '365d' },
                   { label: 'Sovereign Retention', desc: 'Mandatory PII erasure after workspace decommission.', icon: ShieldCheck, delay: 'IMMEDIATE' },
                   { label: 'Cache Eviction Policy', desc: 'Least Recently Used (LRU) with 4 hour TTL heartbeat.', icon: RefreshCw, delay: '4h' },
                   { label: 'Manual Deletion Lock', desc: 'Require multi-admin approval for bulk volume purge.', icon: Trash2, delay: 'PROTECTED' },
                ].map(policy => (
                   <div key={policy.label} className="p-6 bg-white/[0.02] border border-white/5 rounded-3xl hover:border-brand-500/30 transition-all group">
                      <div className="flex items-center justify-between mb-4">
                         <div className="p-2 bg-white/5 rounded-xl text-slate-500 group-hover:text-brand-400 transition-colors">
                            <policy.icon className="w-5 h-5" />
                         </div>
                         <span className="text-[10px] font-mono text-brand-500 bg-brand-500/10 px-2 py-0.5 rounded border border-brand-500/20">{policy.delay}</span>
                      </div>
                      <h4 className="text-sm lg:text-base font-bold text-white tracking-tight italic mb-2 uppercase tracking-tighter">{policy.label}</h4>
                      <p className="text-[11px] text-slate-500 italic leading-relaxed">{policy.desc}</p>
                   </div>
                ))}
             </div>
          </section>
        </div>

        <div className="space-y-8 lg:space-y-10">
           {/* Aggregate Metric Dashboard */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-[#02040d]">
              <h3 className="text-lg lg:text-xl font-bold text-white tracking-tight italic mb-8 flex items-center gap-3">
                 <Activity className="w-5 h-5 text-emerald-400" />
                 Storage Ops
              </h3>
              
              <div className="space-y-8">
                 <div className="grid grid-cols-2 gap-4">
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <LayoutGrid className="w-6 h-6 text-brand-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">142m</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Total Slices</div>
                    </div>
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <Cloud className="w-6 h-6 text-emerald-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">12 PB</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Fleet Cap</div>
                    </div>
                 </div>

                 <div className="space-y-6">
                    <div>
                       <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-slate-600">
                          <span>Primary Capacity used</span>
                          <span className="text-white">68.2%</span>
                       </div>
                       <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                          <div className="h-full bg-brand-500 w-[68.2%] rounded-full shadow-[0_0_10px_rgba(59,130,246,0.3)] transition-all overflow-hidden" />
                       </div>
                    </div>
                    <div>
                       <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-slate-600">
                          <span>Archive Retention (Avg)</span>
                          <span className="text-white">4.2 Years</span>
                       </div>
                       <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                          <div className="h-full bg-emerald-500 w-[84%] rounded-full shadow-[0_0_10px_rgba(16,185,129,0.3)] transition-all overflow-hidden" />
                       </div>
                    </div>
                 </div>

                 <div className="pt-6 border-t border-white/5">
                    <button className="w-full py-4 bg-white/5 hover:bg-white/10 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">
                       Audit Data Lineage
                    </button>
                 </div>
              </div>
           </section>

           {/* Cost & Quota Pulse */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
              <h3 className="text-[11px] font-black text-slate-400 uppercase tracking-[0.3em] mb-8 lg:mb-10 flex items-center gap-3">
                <Layers className="w-4 h-4 text-slate-600" />
                Regional Allocation
              </h3>
              
              <div className="space-y-4">
                 {[
                    { realm: 'US-EAST Cluster', usage: '820 TB', health: 'OPTIMAL' },
                    { realm: 'EU-CENTRAL Vault', usage: '1.2 PB', health: 'HEALTHY' },
                    { realm: 'ASIA-SW Local-Node', usage: '14 TB', health: 'STABLE' },
                 ].map(reg => (
                    <div key={reg.realm} className="flex items-center justify-between p-4 bg-white/5 border border-white/5 rounded-3xl hover:bg-white/[0.08] transition-all cursor-pointer group">
                       <div>
                          <span className="text-[10px] font-bold text-slate-300 block mb-1">{reg.realm}</span>
                          <span className="text-[9px] font-mono text-slate-600">{reg.usage} Allocated</span>
                       </div>
                       <StatusBadge status={reg.health as any} size="sm" />
                    </div>
                 ))}
              </div>

              <div className="mt-8 flex justify-center pt-8 border-t border-white/5">
                <button className="text-[9px] font-black text-brand-400 uppercase tracking-widest hover:underline flex items-center gap-2">
                   Open Storage Hub <ArrowUpRight size={12} />
                </button>
              </div>
           </section>
        </div>
      </div>
    </div>
  );
};
