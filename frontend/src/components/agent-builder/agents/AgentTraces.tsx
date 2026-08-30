import React, { useState } from 'react';
import { 
  History as HistoryIcon, 
  Search, 
  Filter, 
  ChevronRight, 
  Play, 
  ArrowRight, 
  Clock, 
  BarChart3, 
  Zap, 
  ShieldCheck, 
  Database, 
  Wrench, 
  GitBranch, 
  MessageSquare, 
  Cpu, 
  AlertCircle, 
  ExternalLink,
  Bot,
  Activity,
  Layers,
  Code,
  Terminal,
  Circle,
  CheckCircle2,
  XCircle,
  MoreVertical,
  Maximize2
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { MOCK_TRACES } from '../../../constants/agentMock';
import { Trace } from '../../../types/agent';

export const TraceExplorer = () => {
  const [selectedTrace, setSelectedTrace] = useState<Trace | null>(null);

  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'SUCCESS': return <CheckCircle2 className="w-4 h-4 text-green-500" />;
      case 'FAILURE': return <XCircle className="w-4 h-4 text-red-500" />;
      default: return <Circle className="w-4 h-4 text-amber-500" />;
    }
  };

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700 h-full flex flex-col">
      <div className="flex justify-between items-end mb-4">
        <div>
          <div className="flex items-center gap-2 text-brand-400 text-xs font-black font-mono tracking-widest uppercase mb-3 text-glow-blue">
            <HistoryIcon className="w-4 h-4" />
            Execution Observability / Traces
          </div>
          <h1 className="text-5xl font-display font-medium tracking-tight text-white mb-2 italic">Neural Debugger</h1>
          <p className="text-slate-500 text-lg">Inspect reasoning chains, tool calls, and retrieval performance.</p>
        </div>
        <div className="flex gap-4">
           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white transition-all flex items-center gap-2">
             <BarChart3 className="w-4 h-4" /> Usage Dashboard
           </button>
           <button className="px-5 py-2.5 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20 flex items-center gap-2">
             <Play className="w-4 h-4" /> Replay Selection
           </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-8 flex-1 overflow-hidden">
         {/* Trace List */}
         <div className="col-span-12 lg:col-span-5 flex flex-col gap-4 overflow-y-auto custom-scrollbar pr-2">
            <div className="flex gap-2 mb-4">
               <div className="relative flex-1 group">
                  <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                  <input 
                    type="text" 
                    placeholder="Search by Trace ID, query or agent..." 
                    className="w-full bg-white/5 border border-white/10 rounded-2xl pl-12 pr-6 py-3 text-xs focus:outline-none focus:border-brand-500/50 transition-all font-mono"
                  />
               </div>
               <button className="px-4 py-3 bg-white/5 border border-white/10 rounded-2xl text-[10px] font-black uppercase text-slate-500 hover:text-white transition-all"><Filter className="w-4 h-4" /></button>
            </div>

            {MOCK_TRACES.map(trace => (
               <div 
                 key={trace.id}
                 onClick={() => setSelectedTrace(trace)}
                 className={cn(
                   "p-6 rounded-[2.5rem] border transition-all cursor-pointer group relative overflow-hidden",
                   selectedTrace?.id === trace.id 
                      ? "bg-brand-500/10 border-brand-500/40 shadow-xl shadow-brand-500/5" 
                      : "bg-white/[0.02] border-white/5 hover:border-white/20"
                 )}
               >
                  <div className="flex justify-between items-start mb-4">
                     <div className="flex items-center gap-2 text-[10px] font-black text-slate-500 uppercase tracking-widest">
                        {trace.agentName}
                        <span className="opacity-30">â€¢</span>
                        <span className="font-mono text-brand-400/70">{trace.id}</span>
                     </div>
                     {getStatusIcon(trace.status)}
                  </div>
                  <div className="text-sm font-medium text-white mb-6 line-clamp-1 italic">"{trace.query}"</div>
                  <div className="grid grid-cols-4 gap-4 pt-4 border-t border-white/5">
                     <div className="space-y-1 text-center border-r border-white/5 last:border-none">
                        <div className="text-[8px] font-black text-slate-600 uppercase">Latency</div>
                        <div className="text-[10px] font-mono font-bold text-slate-400">{trace.latency}ms</div>
                     </div>
                     <div className="space-y-1 text-center border-r border-white/5 last:border-none">
                        <div className="text-[8px] font-black text-slate-600 uppercase">Tokens</div>
                        <div className="text-[10px] font-mono font-bold text-slate-400">{trace.tokens}</div>
                     </div>
                     <div className="space-y-1 text-center border-r border-white/5 last:border-none">
                        <div className="text-[8px] font-black text-slate-600 uppercase">Tools</div>
                        <div className="text-[10px] font-mono font-bold text-brand-400">{trace.toolCalls}</div>
                     </div>
                     <div className="space-y-1 text-center border-r border-white/5 last:border-none">
                        <div className="text-[8px] font-black text-slate-600 uppercase">Hits</div>
                        <div className="text-[10px] font-mono font-bold text-purple-400">{trace.retrievalCount}</div>
                     </div>
                  </div>
                  <div className="flex justify-between items-center mt-6">
                     <div className="text-[9px] text-slate-600 font-mono tracking-widest font-bold">{trace.timestamp}</div>
                     <div className="text-[10px] font-black text-brand-400 uppercase tracking-tighter">${trace.cost.toFixed(4)}</div>
                  </div>
               </div>
            ))}
         </div>

         {/* Trace Detail View */}
         <div className="col-span-12 lg:col-span-7 flex flex-col overflow-hidden">
            <AnimatePresence mode="wait">
               {selectedTrace ? (
                  <motion.div 
                    key={selectedTrace.id}
                    initial={{ opacity: 0, x: 20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -20 }}
                    className="flex-1 flex flex-col glass-panel rounded-[3.5rem] border-white/5 bg-white/[0.01] overflow-hidden relative"
                  >
                     <div className="px-10 py-8 border-b border-white/5 flex justify-between items-center bg-white/[0.02]">
                        <div className="flex items-center gap-6">
                           <div className="w-12 h-12 rounded-2xl bg-brand-500/10 flex items-center justify-center text-brand-400">
                             <Activity className="w-6 h-6" />
                           </div>
                           <div>
                              <h3 className="text-xl font-bold text-white tracking-tight italic">Reasoning Chain Trace</h3>
                              <div className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mt-1">{selectedTrace.id}</div>
                           </div>
                        </div>
                        <button className="px-4 py-2 bg-white/5 border border-white/10 rounded-xl text-[10px] font-black uppercase text-slate-400 hover:text-white transition-all flex items-center gap-2">
                           <Maximize2 className="w-3.5 h-3.5" /> Fullscreen Graph
                        </button>
                     </div>

                     <div className="flex-1 p-10 overflow-y-auto custom-scrollbar space-y-10">
                        {/* Trace Tree Visualization */}
                        <div className="relative pl-8 space-y-12">
                           <div className="absolute left-3 top-2 bottom-2 w-px bg-white/5" />
                           
                           {/* Step 1: User Input */}
                           <div className="relative group">
                              <div className="absolute left-[-25px] top-1 w-4 h-4 rounded-full bg-brand-500 border-4 border-[#050505] z-10" />
                              <div className="p-6 bg-white/[0.03] border border-white/5 rounded-3xl group-hover:bg-white/[0.05] transition-all">
                                 <div className="flex items-center justify-between mb-4">
                                    <div className="text-[10px] font-black text-brand-400 uppercase tracking-widest flex items-center gap-2">
                                       <MessageSquare className="w-3.5 h-3.5" /> User Query Ingest
                                    </div>
                                    <div className="text-[9px] font-mono text-slate-600">0ms</div>
                                 </div>
                                 <p className="text-white text-sm italic">"{selectedTrace.query}"</p>
                              </div>
                           </div>

                           {/* Step 2: Retrieval */}
                           <div className="relative group">
                              <div className="absolute left-[-25px] top-1 w-4 h-4 rounded-full bg-purple-500 border-4 border-[#050505] z-10" />
                              <div className="p-6 bg-white/[0.03] border border-white/5 rounded-3xl group-hover:bg-white/[0.05] transition-all">
                                 <div className="flex items-center justify-between mb-4">
                                    <div className="text-[10px] font-black text-purple-400 uppercase tracking-widest flex items-center gap-2">
                                       <Database className="w-3.5 h-3.5" /> KB Retrieval Vector Search
                                    </div>
                                    <div className="text-[9px] font-mono text-slate-600">240ms</div>
                                 </div>
                                 <div className="space-y-3">
                                    {[1, 2, 3].map(chunkIdx => (
                                       <div key={`retrieval-chunk-${chunkIdx}`} className="flex items-center justify-between p-3 bg-black/40 rounded-xl border border-white/5 text-[11px]">
                                          <span className="text-slate-400 font-mono italic">CHUNK_{chunkIdx}_SEMANTIC_HASH_{Math.random().toString(16).slice(2, 6)}</span>
                                          <div className="flex items-center gap-3">
                                             <span className="text-green-500 text-[10px] font-bold">0.8{9-chunkIdx} SCORE</span>
                                             <ChevronRight className="w-3 h-3 text-slate-700" />
                                          </div>
                                       </div>
                                    ))}
                                 </div>
                              </div>
                           </div>

                           {/* Step 3: LLM Loop */}
                           <div className="relative group">
                              <div className="absolute left-[-25px] top-1 w-4 h-4 rounded-full bg-amber-500 border-4 border-[#050505] z-10 outline outline-4 outline-amber-500/10" />
                              <div className="p-8 bg-brand-500/5 border border-brand-500/20 rounded-[2.5rem] group-hover:bg-brand-500/10 transition-all">
                                 <div className="flex items-center justify-between mb-6">
                                    <div className="text-[10px] font-black text-brand-400 uppercase tracking-widest flex items-center gap-2">
                                       <Bot className="w-3.5 h-3.5" /> LLM Reasoning Core (v1.5 Pro)
                                    </div>
                                    <div className="text-[9px] font-mono text-slate-600">850ms</div>
                                 </div>
                                 <div className="space-y-4">
                                    <div className="p-4 bg-black/40 rounded-2xl border border-white/5 text-[11px] font-mono text-slate-400 leading-relaxed italic">
                                       "Ingesting retrieved chunks for subscription reset... Analyzing tool requirements..."
                                    </div>
                                    <div className="flex items-center gap-4">
                                       <div className="flex-1 h-1 rounded-full bg-white/5 overflow-hidden">
                                          <div className="h-full bg-brand-500 w-[70%]" />
                                       </div>
                                       <span className="text-[10px] font-mono text-slate-500">70% Conf.</span>
                                    </div>
                                 </div>
                              </div>
                           </div>

                           {/* Final Response */}
                           <div className={cn(
                              "relative group",
                              selectedTrace.status === 'SUCCESS' ? "opacity-100" : "opacity-50"
                           )}>
                              <div className={cn(
                                 "absolute left-[-25px] top-1 w-4 h-4 rounded-full border-4 border-[#050505] z-10",
                                 selectedTrace.status === 'SUCCESS' ? "bg-green-500" : "bg-red-500"
                              )} />
                              <div className="p-6 bg-white/[0.03] border border-white/5 rounded-3xl group-hover:bg-white/[0.05] transition-all">
                                 <div className="flex items-center justify-between mb-4">
                                    <div className={cn(
                                       "text-[10px] font-black uppercase tracking-widest flex items-center gap-2",
                                       selectedTrace.status === 'SUCCESS' ? "text-green-500" : "text-red-500"
                                    )}>
                                       <Zap className="w-3.5 h-3.5" /> Final Output Synthesis
                                    </div>
                                    <div className="text-[9px] font-mono text-slate-600">{selectedTrace.latency}ms</div>
                                 </div>
                                 <div className="p-4 bg-black/40 rounded-2xl border border-white/5 text-sm text-slate-200 leading-relaxed">
                                    {selectedTrace.status === 'SUCCESS' ? "To reset your subscription, navigate to the Billing Portal and select 'Plan Configuration'..." : "ERROR: Node failed to synthesize grounding tokens."}
                                 </div>
                              </div>
                           </div>
                        </div>
                     </div>

                     <div className="px-10 py-8 border-t border-white/5 flex justify-between items-center bg-white/[0.02]">
                        <div className="flex gap-4">
                           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-xl text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">Debug Prompt</button>
                           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-xl text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">Export JSON</button>
                        </div>
                        <button className="px-8 py-2.5 bg-brand-500 text-white rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20">
                           Open In Playground
                        </button>
                     </div>
                  </motion.div>
               ) : (
                  <div className="flex-1 flex flex-col items-center justify-center glass-panel rounded-[3.5rem] border-white/5 bg-white/[0.01] border-dashed text-slate-700">
                     <Activity className="w-20 h-20 opacity-10 mb-8" />
                     <h3 className="text-2xl font-display font-medium italic mb-2">Observability Stream Offline</h3>
                     <p className="text-[10px] font-black uppercase tracking-[0.4em]">Select a trace sequence to visualize neural path</p>
                  </div>
               )}
            </AnimatePresence>
         </div>
      </div>
    </div>
  );
};
