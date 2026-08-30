import React, { useState } from 'react';
import { 
  Cpu, 
  Layers, 
  Settings2, 
  Zap, 
  Activity, 
  Search, 
  Plus, 
  RefreshCcw, 
  Settings, 
  MoreVertical,
  CheckCircle2,
  AlertCircle,
  Database,
  Globe,
  Lock,
  ArrowRight
} from 'lucide-react';
import { motion } from 'motion/react';

const PROVIDERS = [
  { id: 'p1', name: 'Azure OpenAI', region: 'eastus', status: 'HEALTHY', latency: '42ms', usage: '82%', cost: '$412.50' },
  { id: 'p2', name: 'Anthropic Claude', region: 'us-east-1', status: 'DEGRADED', latency: '1.2s', usage: '12%', cost: '$8.20' },
  { id: 'p3', name: 'Gemini Enterprise', region: 'asia-southeast1', status: 'HEALTHY', latency: '12ms', usage: '45%', cost: '$124.00' },
];

export const AIInfrastructureSection = () => {
  const [tab, setTab] = useState<'PROVIDERS' | 'MODEL_REGISTRY' | 'EMBEDDINGS'>('PROVIDERS');

  return (
    <div className="space-y-12">
      <div>
        <h1 className="text-3xl font-bold text-white tracking-tight mb-2">AI Infrastructure Control</h1>
        <p className="text-slate-500 font-medium leading-relaxed max-w-2xl">
          Centralized governance for AI providers, foundational models, and retrieval infrastructure. Configure failover, quotas, and regional routing.
        </p>
      </div>

      <div className="flex bg-white/5 p-1 rounded-2xl border border-white/5 w-fit">
        {[
          { id: 'PROVIDERS', label: 'Cloud Providers', icon: Globe },
          { id: 'MODEL_REGISTRY', label: 'Model Registry', icon: Cpu },
          { id: 'EMBEDDINGS', label: 'Embedding Infrastructure', icon: Database },
        ].map(item => (
          <button
            key={item.id}
            onClick={() => setTab(item.id as any)}
            className={`flex items-center gap-2 px-6 py-2 rounded-xl text-xs font-bold transition-all ${
              tab === item.id ? 'bg-brand-500 text-white shadow-lg' : 'text-slate-400 hover:text-white hover:bg-white/5'
            }`}
          >
            <item.icon className="w-4 h-4" />
            {item.label}
          </button>
        ))}
      </div>

      {tab === 'PROVIDERS' && (
        <div className="space-y-8">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            {PROVIDERS.map((prov) => (
              <div key={prov.id} className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px] group hover:bg-white/[0.04] transition-all">
                <div className="flex items-center justify-between mb-8">
                  <div className="flex items-center gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-slate-900 border border-white/10 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors">
                      <Globe className="w-6 h-6" />
                    </div>
                    <div>
                      <h3 className="text-white font-bold tracking-tight">{prov.name}</h3>
                      <span className="text-[10px] text-slate-600 font-black uppercase tracking-widest leading-none">{prov.region}</span>
                    </div>
                  </div>
                  <div className={`p-1 rounded-full ${prov.status === 'HEALTHY' ? 'bg-emerald-500/20 shadow-[0_0_10px_rgba(16,185,129,0.3)]' : 'bg-red-500/20 animate-pulse'}`}>
                    <div className={`w-2 h-2 rounded-full ${prov.status === 'HEALTHY' ? 'bg-emerald-400' : 'bg-red-400'}`} />
                  </div>
                </div>

                <div className="grid grid-cols-2 gap-4 mb-8">
                   <div className="bg-white/5 p-4 rounded-2xl border border-white/5">
                      <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest mb-1">Latency</div>
                      <div className="text-sm font-bold text-white font-mono">{prov.latency}</div>
                   </div>
                   <div className="bg-white/5 p-4 rounded-2xl border border-white/5">
                      <div className="text-[9px] font-black text-slate-600 uppercase tracking-widest mb-1">Month Spend</div>
                      <div className="text-sm font-bold text-white font-mono">{prov.cost}</div>
                   </div>
                </div>

                <div className="space-y-2">
                   <div className="flex justify-between items-center text-[10px] font-black text-slate-600 uppercase tracking-widest">
                     <span>Quota Usage</span>
                     <span className="text-slate-400">{prov.usage}</span>
                   </div>
                   <div className="h-1 bg-white/5 rounded-full overflow-hidden">
                      <div className="h-full bg-brand-500 rounded-full" style={{ width: prov.usage }} />
                   </div>
                </div>

                <button className="w-full mt-8 py-3 bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white border border-white/5 rounded-2xl text-[10px] font-black uppercase tracking-[0.2em] transition-all">
                  Configure Routing
                </button>
              </div>
            ))}
          </div>

          <div className="p-10 bg-slate-900/50 border border-white/5 rounded-[40px]">
             <div className="flex items-center justify-between mb-10">
               <div>
                  <h3 className="text-xl font-bold text-white mb-1">Failover Polices</h3>
                  <p className="text-xs text-slate-500 uppercase font-black tracking-widest italic leading-none">Automated business continuity protocols</p>
               </div>
               <Plus className="w-6 h-6 text-brand-400 cursor-pointer" />
             </div>
             
             <div className="space-y-4">
                {[
                  { name: 'Critical-Support-Priority', trigger: 'Latency > 2s', target: 'Azure -> Google', status: 'ACTIVE' },
                  { name: 'Financial-Transaction-Quorum', trigger: 'Error-Rate > 1%', target: 'Anthropic -> Local-Llama', status: 'DORMANT' },
                ].map(policy => (
                  <div key={policy.name} className="flex items-center justify-between p-6 bg-white/5 hover:bg-white/[0.08] border border-white/5 rounded-3xl transition-all group">
                     <div className="flex items-center gap-6">
                        <div className={`p-4 rounded-2xl bg-slate-900 border border-white/10 ${policy.status === 'ACTIVE' ? 'text-brand-400' : 'text-slate-700'}`}>
                           <RefreshCcw className={`w-5 h-5 ${policy.status === 'ACTIVE' ? 'animate-spin-slow' : ''}`} style={{ animationDuration: '10s' }} />
                        </div>
                        <div>
                           <div className="text-white font-bold text-sm tracking-tight mb-1">{policy.name}</div>
                           <div className="flex items-center gap-2">
                              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">TRIGGER:</span>
                              <span className="text-[10px] text-red-400 font-bold">{policy.trigger}</span>
                              <ArrowRight className="w-3 h-3 text-slate-700" />
                              <span className="text-[10px] text-brand-400 font-bold">{policy.target}</span>
                           </div>
                        </div>
                     </div>
                     <Settings className="w-4 h-4 text-slate-700 group-hover:text-white transition-all cursor-pointer" />
                  </div>
                ))}
             </div>
          </div>
        </div>
      )}

      {tab === 'MODEL_REGISTRY' && (
        <div className="space-y-6">
          <div className="flex items-center justify-between">
            <div className="relative flex-1 max-w-xl group">
               <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500 group-focus-within:text-brand-400 transition-colors" />
               <input 
                 type="text" 
                 placeholder="Search global models catalog..." 
                 className="w-full bg-white/5 border border-white/10 rounded-2xl py-3 pl-12 pr-4 text-sm text-white focus:outline-none focus:ring-1 focus:ring-brand-500/50"
               />
            </div>
            <div className="flex gap-2">
               <button className="px-5 py-3 bg-white/10 text-white rounded-2xl text-[10px] font-black tracking-widest uppercase">Benchmarking</button>
               <button className="px-5 py-3 bg-brand-500 text-white rounded-2xl text-[10px] font-black tracking-widest uppercase shadow-lg shadow-brand-500/20">Register Model</button>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
             {[
               { name: 'GPT-4o', type: 'GENERAL', ctx: '128k', dev: true, prod: true, cost: 'High' },
               { name: 'Claude 3.5 Sonnet', type: 'REASONING', ctx: '200k', dev: true, prod: true, cost: 'Med' },
               { name: 'Gemini 1.5 Flash', type: 'LATENCY_PRIORITY', ctx: '1M', dev: true, prod: true, cost: 'Low' },
               { name: 'Llama 3-70B', type: 'LOCAL_SECURE', ctx: '32k', dev: true, prod: false, cost: 'N/A' },
             ].map((model) => (
               <div key={model.name} className="p-6 bg-white/[0.02] border border-white/5 rounded-[32px] group hover:border-brand-500/30 transition-all flex flex-col">
                  <div className="flex items-center justify-between mb-4">
                     <div className="p-2.5 bg-brand-500/10 rounded-xl">
                        <Cpu className="w-5 h-5 text-brand-400" />
                     </div>
                     <div className={`text-[9px] font-black px-2 py-0.5 rounded border ${
                       model.cost === 'High' ? 'bg-red-400/10 border-red-400/20 text-red-400' :
                       model.cost === 'Low' ? 'bg-emerald-400/10 border-emerald-400/20 text-emerald-400' :
                       'bg-slate-400/10 border-slate-400/20 text-slate-400'
                     }`}>
                       {model.cost}
                     </div>
                  </div>
                  <h4 className="text-base font-bold text-white mb-1 group-hover:text-brand-400 transition-colors uppercase tracking-tight">{model.name}</h4>
                  <div className="text-[10px] text-slate-500 font-black uppercase tracking-[0.1em] mb-6">{model.type}</div>
                  
                  <div className="space-y-4 flex-1">
                     <div className="flex justify-between items-center text-[10px] font-bold">
                        <span className="text-slate-600">Context Window</span>
                        <span className="text-slate-300 font-mono">{model.ctx}</span>
                     </div>
                     <div className="flex justify-between items-center text-[10px] font-bold">
                        <span className="text-slate-600">State Prod</span>
                        <div className={`w-3 h-3 rounded-full ${model.prod ? 'bg-emerald-500' : 'bg-slate-800'}`} />
                     </div>
                  </div>
                  
                  <button className="w-full mt-8 py-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-[9px] font-black uppercase tracking-widest text-slate-500 hover:text-white transition-all">
                     View Governance
                  </button>
               </div>
             ))}
          </div>
        </div>
      )}
    </div>
  );
};
import { Bot } from 'lucide-react';
