import React from 'react';
import { 
  Code, 
  Key, 
  Terminal, 
  Webhook, 
  Cpu, 
  Layers, 
  Activity, 
  History,
  Copy,
  Plus,
  ArrowRight,
  ExternalLink,
  ShieldCheck,
  RefreshCw,
  FileCode
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { cn } from '../../lib/utils';

export const APISection = () => {
  return (
    <div className="space-y-10 lg:space-y-12 pb-20 no-scrollbar">
      <OperationalHeader 
        title="API & Developers"
        subtitle="Manage platform API keys, webhooks, and enterprise SDK distribution."
        breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'API' }]}
        status={<StatusBadge status="ACTIVE" size="lg" />}
        actions={
          <div className="flex flex-col sm:flex-row gap-3">
             <button className="flex items-center justify-center gap-2 bg-white/5 hover:bg-white/10 text-white px-5 py-2.5 rounded-2xl text-xs font-bold border border-white/5 transition-all">
                <FileCode className="w-4 h-4" />
                API DOCS
             </button>
             <button className="flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white px-5 py-2.5 rounded-2xl text-xs font-bold transition-all shadow-lg shadow-brand-500/20 active:scale-95">
               <Plus className="w-4 h-4" />
               CREATE API KEY
             </button>
          </div>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-8 lg:space-y-10">
          {/* Active API Keys */}
          <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
             <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-6 mb-10">
                <div className="flex items-center gap-3">
                   <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                     <Key className="w-6 h-6" />
                   </div>
                   <div>
                     <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Platform Access Tokens</h3>
                     <p className="text-[10px] lg:text-xs text-slate-500 uppercase font-black tracking-widest mt-1">Enterprise Developer Keys</p>
                   </div>
                </div>
                <div className="flex bg-white/5 border border-white/10 p-1.5 rounded-2xl">
                   <button className="px-4 py-2 bg-brand-500 text-white rounded-xl text-[10px] font-black uppercase tracking-widest">Platform</button>
                   <button className="px-4 py-2 text-slate-500 hover:text-white rounded-xl text-[10px] font-black uppercase tracking-widest">Personal</button>
                </div>
             </div>

             <div className="space-y-4">
                {[
                   { name: 'FIN-INGESTION-WORKER', type: 'FULL_ACCESS', created: 'Oct 12, 2025', lastUsed: '4m ago', status: 'ACTIVE', prefix: 'na_pk_f9...' },
                   { name: 'KNOWLEDGE-BRIDGE-STAGING', type: 'READ_ONLY', created: 'Nov 02, 2025', lastUsed: '24h ago', status: 'ACTIVE', prefix: 'na_pk_a2...' },
                   { name: 'EXTERNAL-CRM-SYNC', type: 'ADMIN_PLATFORM', created: 'Dec 08, 2025', lastUsed: '32m ago', status: 'REVOKED', prefix: 'na_pk_e4...' },
                ].map((key, i) => (
                   <div key={key.name} className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between p-6 bg-white/[0.03] border border-white/5 rounded-[2rem] hover:bg-white/[0.06] transition-all group gap-6 lg:gap-0">
                      <div className="flex items-center gap-5">
                         <div className="w-12 h-12 rounded-2xl bg-slate-900 border border-white/10 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors shadow-inner">
                            <Terminal className="w-6 h-6" />
                         </div>
                         <div>
                            <h4 className="text-base lg:text-lg font-bold text-white tracking-tight italic">{key.name}</h4>
                            <div className="flex flex-wrap items-center gap-3 mt-1">
                               <span className="text-[10px] font-mono text-slate-500 tracking-tighter">{key.prefix}</span>
                               <div className="w-1 h-1 rounded-full bg-slate-800" />
                               <span className="text-[10px] font-black text-brand-500/80 uppercase tracking-widest">{key.type}</span>
                            </div>
                         </div>
                      </div>
                      
                      <div className="flex items-center gap-8 justify-between lg:justify-end border-t lg:border-t-0 border-white/5 pt-4 lg:pt-0">
                         <div className="text-right">
                            <div className="text-xs font-bold text-white tracking-tighter">{key.lastUsed}</div>
                            <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Last Activity</div>
                         </div>
                         <div className="flex gap-2">
                           <button className="p-3 bg-white/5 border border-white/10 rounded-xl text-slate-600 hover:text-white transition-all">
                              <Copy className="w-4 h-4" />
                           </button>
                           <button className="p-3 bg-white/5 border border-white/10 rounded-xl text-slate-600 hover:text-red-500 transition-all">
                              <RefreshCw className="w-4 h-4" />
                           </button>
                         </div>
                      </div>
                   </div>
                ))}
             </div>
          </section>

          {/* Webhooks & Event Streams */}
          <section className="glass-panel p-8 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
             <div className="flex items-center justify-between mb-10">
                <div className="flex items-center gap-3">
                   <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                     <Webhook className="w-6 h-6" />
                   </div>
                   <div>
                     <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Neural Event Streams</h3>
                     <p className="text-[10px] lg:text-xs text-slate-500 uppercase font-black tracking-widest mt-1">Platform-wide Webhook Control</p>
                   </div>
                </div>
                <button className="px-4 py-2 border border-brand-500/50 text-brand-400 hover:bg-brand-500 hover:text-white rounded-xl text-[9px] font-black uppercase tracking-widest transition-all">New Segment</button>
             </div>

             <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {[
                   { url: 'https://hooks.slack.com/services/...', events: ['KB_UPDATE', 'AUDIT_ALERT'], status: 'HEALTHY', latency: '42ms' },
                   { url: 'https://api.internal-ops.io/hooks', events: ['WORKFLOW_INIT', 'DEPLOY_INIT'], status: 'DEGRADED', latency: '240ms' },
                ].map(hook => (
                   <div key={hook.url} className="p-6 bg-white/[0.02] border border-white/5 rounded-3xl hover:border-brand-500/30 transition-all group">
                      <div className="flex justify-between items-start mb-6">
                         <div className="text-[10px] font-mono text-slate-500 group-hover:text-brand-400 truncate max-w-[70%]">{hook.url}</div>
                         <StatusBadge status={hook.status as any} size="sm" />
                      </div>
                      <div className="flex flex-wrap gap-2 mb-6">
                         {hook.events.map(ev => (
                            <span key={ev} className="text-[8px] font-black text-slate-600 bg-white/5 px-1.5 py-0.5 rounded border border-white/10 uppercase tracking-widest">{ev}</span>
                         ))}
                      </div>
                      <div className="pt-4 border-t border-white/5 flex items-center justify-between">
                         <div className="text-[9px] font-black text-slate-500 uppercase tracking-widest">Mean Latency: {hook.latency}</div>
                         <button className="text-[9px] font-black text-brand-400 uppercase tracking-widest hover:underline">Config Hook</button>
                      </div>
                   </div>
                ))}
             </div>
          </section>
        </div>

        <div className="space-y-8 lg:space-y-10">
           {/* API Pulse Overview */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-[#02040d]">
              <h3 className="text-lg lg:text-xl font-bold text-white tracking-tight italic mb-8 flex items-center gap-3">
                 <Activity className="w-5 h-5 text-emerald-400" />
                 Developer Pulse
              </h3>
              
              <div className="space-y-8">
                 <div className="grid grid-cols-2 gap-4">
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <Cpu className="w-6 h-6 text-brand-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">8.4m</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Reqs / 24h</div>
                    </div>
                    <div className="p-5 bg-white/5 border border-white/5 rounded-3xl text-center">
                       <Layers className="w-6 h-6 text-emerald-400 mx-auto mb-3" />
                       <div className="text-xl font-bold text-white tracking-tighter">1.2ms</div>
                       <div className="text-[8px] font-black text-slate-500 uppercase tracking-widest">Avg Response</div>
                    </div>
                 </div>

                 <div className="space-y-6">
                    <div>
                       <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-slate-600">
                          <span>Successful Reqs</span>
                          <span className="text-emerald-400">99.98%</span>
                       </div>
                       <div className="h-1.5 bg-white/5 rounded-full overflow-hidden">
                          <div className="h-full bg-emerald-500 w-[99.98%] rounded-full shadow-[0_0_10px_rgba(16,185,129,0.3)]" />
                       </div>
                    </div>
                 </div>

                 <div className="p-6 bg-brand-500/5 border border-brand-500/20 rounded-3xl">
                    <div className="flex items-center gap-3 mb-3 text-brand-400">
                       <ShieldCheck className="w-4 h-4" />
                       <span className="text-[10px] font-black uppercase tracking-widest tracking-widest">Schema Strict Mode</span>
                    </div>
                    <p className="text-[11px] text-brand-500/70 italic leading-relaxed">Neural Schema Validation active for 100% of ingress transit.</p>
                 </div>
              </div>
           </section>

           {/* SDK & Resources */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
              <h3 className="text-[11px] font-black text-slate-400 uppercase tracking-[0.3em] mb-8 lg:mb-10 flex items-center gap-3">
                <Code className="w-4 h-4 text-slate-600" />
                Enterprise SDK Distribution
              </h3>
              
              <div className="space-y-4">
                 {[
                    { language: 'TypeScript / Node.js', vsn: 'v4.1.0' },
                    { language: 'Python (Neural Engine)', vsn: 'v2.8.4' },
                    { language: 'Java / Go Runtime', vsn: 'v1.12.0' },
                 ].map(sdk => (
                    <div key={sdk.language} className="flex items-center justify-between p-4 bg-white/5 border border-white/5 rounded-[1.5rem] hover:bg-white/[0.08] transition-all cursor-pointer group">
                       <div>
                          <span className="text-[10px] font-bold text-slate-300 block mb-1 uppercase italic tracking-tighter">{sdk.language}</span>
                          <span className="text-[9px] font-mono text-slate-600">{sdk.vsn} Distributed</span>
                       </div>
                       <button className="p-2 text-slate-600 group-hover:text-brand-400"><ArrowRight size={14} /></button>
                    </div>
                 ))}
              </div>

              <div className="mt-8 flex justify-center pt-8 border-t border-white/5">
                <button className="text-[9px] font-black text-brand-400 uppercase tracking-widest hover:underline flex items-center gap-2">
                   Open Developer Hub <ExternalLink size={12} />
                </button>
              </div>
           </section>
        </div>
      </div>
    </div>
  );
};
