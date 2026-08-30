import React, { useState } from 'react';
import { 
  Zap, 
  Flag, 
  Save, 
  RefreshCw, 
  Bot, 
  Database, 
  ShieldCheck, 
  Lock, 
  Search, 
  Plus, 
  ChevronRight,
  MoreVertical,
  Activity,
  History,
  Cloud,
  FileCode,
  Globe,
  Settings2,
  CheckCircle2,
  AlertCircle
} from 'lucide-react';
import { motion } from 'motion/react';

export const AdvancedPlatformSection = () => {
  const [tab, setTab] = useState<'FLAGS' | 'BACKUP' | 'AI_CONFIG'>('AI_CONFIG');

  return (
    <div className="space-y-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight mb-2">Advanced Control Plane</h1>
          <p className="text-slate-500 font-medium leading-relaxed max-w-xl">
             Low-level platform orchestration. Configure feature flag rollouts, disaster recovery protocols, and global AI processing parameters.
          </p>
        </div>
        <div className="flex bg-white/5 p-1 rounded-3xl border border-white/5">
          <button 
            onClick={() => setTab('AI_CONFIG')}
            className={`px-6 py-2.5 rounded-2xl text-xs font-bold transition-all ${tab === 'AI_CONFIG' ? 'bg-brand-500 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}
          >
            AI Engine
          </button>
          <button 
            onClick={() => setTab('FLAGS')}
            className={`px-6 py-2.5 rounded-2xl text-xs font-bold transition-all ${tab === 'FLAGS' ? 'bg-brand-500 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}
          >
            Feature Registry
          </button>
          <button 
            onClick={() => setTab('BACKUP')}
            className={`px-6 py-2.5 rounded-2xl text-xs font-bold transition-all ${tab === 'BACKUP' ? 'bg-brand-500 text-white shadow-lg' : 'text-slate-500 hover:text-white'}`}
          >
            Recovery
          </button>
        </div>
      </div>

      {tab === 'AI_CONFIG' && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
           <div className="p-10 bg-white/[0.02] border border-white/5 rounded-[48px] space-y-10">
              <div className="flex items-center gap-4 mb-2">
                 <div className="p-3 bg-brand-500/10 rounded-2xl">
                    <Bot className="w-6 h-6 text-brand-400" />
                 </div>
                 <h3 className="text-xl font-bold text-white uppercase italic tracking-tight">Global Reasoning Defaults</h3>
              </div>
              
              <div className="space-y-8">
                 {[
                   { label: 'Default Temperature', val: '0.7', desc: 'Global default for standard agentic loops' },
                   { label: 'Max Context tokens', val: '128,000', desc: 'Safety cap for individual requests' },
                   { label: 'Reranking Threshold', val: '0.82', desc: 'Minimum semantic score for retrieval inclusion' },
                   { label: 'Hallucination Gate', val: 'STRICT', desc: 'Model-based validation level for extraction' },
                 ].map(cfg => (
                   <div key={cfg.label} className="flex items-center justify-between group">
                      <div className="max-w-[70%]">
                         <div className="text-sm font-bold text-slate-200 mb-1">{cfg.label}</div>
                         <div className="text-[10px] text-slate-600 font-medium leading-none italic">{cfg.desc}</div>
                      </div>
                      <div className="flex items-center gap-4">
                         <div className="px-4 py-2 bg-white/5 border border-white/10 rounded-xl text-xs font-mono text-brand-400 font-bold tracking-tight">
                           {cfg.val}
                         </div>
                         <Settings2 className="w-4 h-4 text-slate-700 group-hover:text-white cursor-pointer transition-all" />
                      </div>
                   </div>
                 ))}
              </div>
           </div>

           <div className="p-10 bg-white/[0.02] border border-white/5 rounded-[48px] space-y-10">
              <div className="flex items-center gap-4 mb-2">
                 <div className="p-3 bg-emerald-500/10 rounded-2xl">
                    <Database className="w-6 h-6 text-emerald-400" />
                 </div>
                 <h3 className="text-xl font-bold text-white uppercase italic tracking-tight">Knowledge Graph Strategy</h3>
              </div>
              
              <div className="space-y-8">
                 <div className="p-6 bg-white/[0.03] border border-white/5 rounded-3xl">
                    <div className="flex items-center justify-between mb-4">
                       <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest leading-none">Graph Extraction Model</span>
                       <span className="text-xs font-bold text-emerald-400">GPT-4o (Optimized)</span>
                    </div>
                    <div className="flex items-center justify-between py-4 border-y border-white/5">
                       <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest leading-none">Entity Relationship Limit</span>
                       <span className="text-xs font-bold text-slate-300">500 / document</span>
                    </div>
                    <div className="flex items-center justify-between mt-4">
                       <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest leading-none">Conflict Auto-Merge</span>
                       <div className="w-10 h-5 bg-emerald-500 rounded-full p-1 flex justify-end transition-all">
                          <div className="w-3 h-3 bg-white rounded-full shadow-lg" />
                       </div>
                    </div>
                 </div>
                 
                 <div className="p-8 bg-brand-500/5 border border-brand-500/10 rounded-3xl relative overflow-hidden group hover:bg-brand-500/10 transition-all cursor-pointer">
                    <Cloud className="absolute -right-4 -bottom-4 w-32 h-32 text-brand-500/5 rotate-12" />
                    <h4 className="text-base font-bold text-white mb-2">GraphRAG Infrastructure</h4>
                    <p className="text-xs text-slate-500 leading-relaxed max-w-[80%] italic">
                      Configure distributed knowledge graph traversal and recursive semantic expansion settings.
                    </p>
                    <button className="mt-8 flex items-center gap-2 text-[10px] font-black text-brand-400 uppercase tracking-widest">
                      Advanced Graph Ops <ChevronRight className="w-3 h-3" />
                    </button>
                 </div>
              </div>
           </div>
        </div>
      )}

      {tab === 'FLAGS' && (
        <div className="space-y-6">
           <div className="flex items-center justify-between mb-4">
              <h3 className="text-xl font-bold text-white italic">Feature Rollout Control</h3>
              <button className="flex items-center gap-2 px-6 py-2.5 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest shadow-lg">
                <Plus className="w-4 h-4" /> New Feature Gate
              </button>
           </div>
           
           <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              {[
                { name: 'Multimodal-Graph-Ingest', tier: 'BETA', status: 'ON', env: ['UAT', 'STAGING'], rollout: 40 },
                { name: 'Recursive-Agent-Loops', tier: 'ALPHA', status: 'ON', env: ['DEV'], rollout: 100 },
                { name: 'Streaming-RAG-Engine-v2', tier: 'RELEASE_CANDIDATE', status: 'OFF', env: [], rollout: 0 },
              ].map(flag => (
                <div key={flag.name} className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px] group hover:bg-white/[0.05] transition-all relative overflow-hidden">
                   <div className="flex items-center justify-between mb-8">
                      <div className={`text-[9px] font-black px-2 py-1 rounded border ${
                        flag.tier === 'BETA' ? 'bg-blue-400/10 border-blue-400/20 text-blue-400' :
                        flag.tier === 'ALPHA' ? 'bg-amber-400/10 border-amber-400/20 text-amber-400' :
                        'bg-emerald-400/10 border-emerald-400/20 text-emerald-400'
                      }`}>
                        {flag.tier}
                      </div>
                      <div className={`w-12 h-6 rounded-full p-1 flex transition-all ${flag.status === 'ON' ? 'bg-brand-500 justify-end' : 'bg-slate-800 justify-start'}`}>
                         <div className="w-4 h-4 bg-white rounded-full" />
                      </div>
                   </div>
                   
                   <h4 className="text-lg font-bold text-white mb-2 leading-tight uppercase tracking-tight group-hover:text-brand-400 transition-colors uppercase">{flag.name}</h4>
                   
                   <div className="space-y-4 mt-8 pt-6 border-t border-white/5">
                      <div className="flex justify-between items-center text-[10px] font-black uppercase tracking-widest">
                         <span className="text-slate-600">Active Env</span>
                         <div className="flex gap-1">
                            {flag.env.length > 0 ? flag.env.map(e => (
                              <span key={e} className="text-slate-300 bg-white/5 px-1.5 py-0.5 rounded border border-white/10">{e}</span>
                            )) : <span className="text-red-400/50">NONE</span>}
                         </div>
                      </div>
                      <div className="space-y-1.5">
                         <div className="flex justify-between text-[10px] font-black uppercase tracking-widest">
                            <span className="text-slate-600">Rollout %</span>
                            <span className="text-slate-400">{flag.rollout}%</span>
                         </div>
                         <div className="h-1 bg-white/5 rounded-full overflow-hidden">
                            <div className="h-full bg-brand-500 rounded-full" style={{ width: `${flag.rollout}%` }} />
                         </div>
                      </div>
                   </div>
                </div>
              ))}
           </div>
        </div>
      )}

      {tab === 'BACKUP' && (
        <div className="space-y-8">
           <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div className="p-10 bg-white/[0.02] border border-white/5 rounded-[48px] space-y-8">
                 <div className="flex items-center gap-4">
                    <div className="p-4 bg-brand-500/10 rounded-[24px]">
                       <RefreshCw className="w-8 h-8 text-brand-400" />
                    </div>
                    <div>
                       <h3 className="text-xl font-bold text-white uppercase italic">Retention & Recovery</h3>
                       <p className="text-[10px] text-slate-500 font-black uppercase tracking-widest italic">Global failover sync state</p>
                    </div>
                 </div>
                 
                 <div className="space-y-6">
                    <div className="p-6 bg-white/5 border border-white/5 rounded-3xl">
                       <div className="flex items-center justify-between mb-4">
                          <span className="text-sm font-bold text-white">Automated Snapshot Cycle</span>
                          <span className="text-brand-400 font-mono text-xs font-bold uppercase tracking-tight">EVERY 4 HOURS</span>
                       </div>
                       <div className="flex items-center justify-between pb-4 border-b border-white/5">
                          <span className="text-[10px] font-black text-slate-600 uppercase tracking-widest leading-none">Last Full Backup</span>
                          <span className="text-[10px] text-slate-400 font-bold uppercase tracking-tight italic">Today @ 04:00 UTC</span>
                       </div>
                       <div className="flex items-center justify-between pt-4">
                          <span className="text-[10px] font-black text-slate-600 uppercase tracking-widest leading-none">Off-site Replication</span>
                          <span className="text-[10px] text-emerald-500 font-black uppercase tracking-tight">SYNCHRONIZED</span>
                       </div>
                    </div>
                 </div>
                 
                 <button className="w-full py-5 bg-brand-500 hover:bg-brand-400 text-white rounded-[24px] font-bold shadow-xl shadow-brand-500/20 transition-all">
                    Initiate Global Snapshot
                 </button>
              </div>

              <div className="p-10 border border-white/5 rounded-[48px] bg-gradient-to-br from-red-500/5 to-transparent relative overflow-hidden group">
                 <div className="absolute top-0 right-0 p-8">
                    <AlertCircle className="w-16 h-16 text-red-500/10 group-hover:text-red-500/20 transition-colors" />
                 </div>
                 <h3 className="text-xl font-bold text-white mb-2 leading-none uppercase italic tracking-tight">Disaster Protocol</h3>
                 <p className="text-sm text-slate-500 italic mb-10 leading-relaxed max-w-sm">
                   Immediate read-only switch and regional migration. ONLY for catastrophic system failure.
                 </p>
                 
                 <div className="space-y-4">
                    <button className="w-full py-4 border border-red-500/30 hover:bg-red-500/10 text-red-400 rounded-2xl text-xs font-bold transition-all uppercase tracking-widest">
                       Trigger Failover Simulation
                    </button>
                    <button className="w-full py-8 bg-red-800/10 hover:bg-red-600 text-white border-2 border-red-500/30 hover:border-transparent rounded-3xl font-black text-base transition-all uppercase shadow-2xl shadow-red-500/10">
                       ACTIVATE EMERGENCY LOCKDOWN
                    </button>
                 </div>
              </div>
           </div>
        </div>
      )}
    </div>
  );
};
