import React, { useState } from 'react';
import { 
  Plus, 
  FileCode, 
  Database, 
  Settings, 
  History as HistoryIcon, 
  Bot,
  Search, 
  Filter, 
  RefreshCw, 
  GitPullRequest, 
  Layers, 
  MoreVertical, 
  ChevronRight, 
  CheckCircle2, 
  AlertTriangle, 
  Code, 
  Trash2, 
  Copy, 
  Save, 
  GitBranch, 
  Zap, 
  Check, 
  FileText,
  Wrench,
  ShieldCheck
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { MOCK_CONFIGS } from '../../../constants/agentMock';
import { ConfigItem } from '../../../types/agent';

export const ConfigRegistry = () => {
  const [selectedConfig, setSelectedConfig] = useState<ConfigItem | null>(null);
  const [viewMode, setViewMode] = useState<'VISUAL' | 'YAML' | 'JSON'>('YAML');

  const getConfigIcon = (type: string) => {
    switch (type) {
      case 'PROMPT': return <FileText className="w-4 h-4 text-brand-400" />;
      case 'RETRIEVAL': return <Database className="w-4 h-4 text-purple-400" />;
      case 'GUARDRAIL': return <ShieldCheck className="w-4 h-4 text-amber-500" />;
      case 'TOOL': return <Wrench className="w-4 h-4 text-blue-400" />;
      default: return <Settings className="w-4 h-4 text-slate-500" />;
    }
  };

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700 h-full flex flex-col">
      <div className="flex justify-between items-end mb-4">
        <div>
          <div className="flex items-center gap-2 text-brand-400 text-xs font-black font-mono tracking-widest uppercase mb-3 text-glow-blue">
            <Layers className="w-4 h-4" />
            Config-as-Code / Registry
          </div>
          <h1 className="text-5xl font-display font-medium tracking-tight text-white mb-2 italic">Knowledge Blueprint</h1>
          <p className="text-slate-500 text-lg">Manage reusable prompts, guardrails, and orchestration parameters.</p>
        </div>
        <div className="flex gap-4">
           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white transition-all flex items-center gap-2">
             <GitPullRequest className="w-4 h-4" /> Git Sync Status
           </button>
           <button className="px-5 py-2.5 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20 flex items-center gap-2">
             <Plus className="w-4 h-4" /> Register New Config
           </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-8 flex-1 overflow-hidden">
         {/* Config List */}
         <div className="col-span-12 lg:col-span-4 flex flex-col gap-4 overflow-y-auto custom-scrollbar pr-2">
            <div className="relative mb-4">
               <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
               <input 
                 type="text" 
                 placeholder="Search registry..." 
                 className="w-full bg-white/5 border border-white/10 rounded-2xl pl-12 pr-6 py-3 text-xs focus:outline-none focus:border-brand-500/50 transition-all font-mono"
               />
            </div>
            {MOCK_CONFIGS.map(config => (
               <div 
                 key={config.id}
                 onClick={() => setSelectedConfig(config)}
                 className={cn(
                   "p-6 rounded-[2rem] border transition-all cursor-pointer group relative overflow-hidden",
                   selectedConfig?.id === config.id 
                      ? "bg-brand-500/10 border-brand-500/40 shadow-xl shadow-brand-500/5" 
                      : "bg-white/[0.02] border-white/5 hover:border-white/20"
                 )}
               >
                  <div className="flex justify-between items-start mb-4">
                     <div className="flex items-center gap-3">
                        <div className="p-2.5 bg-white/5 rounded-xl">
                           {getConfigIcon(config.type)}
                        </div>
                        <div>
                           <div className="text-sm font-bold text-white group-hover:text-brand-400 transition-colors uppercase tracking-tight">{config.name}</div>
                           <div className="text-[9px] text-slate-600 font-black uppercase tracking-widest mt-0.5">{config.type} â€¢ {config.version}</div>
                        </div>
                     </div>
                     <div className={cn(
                        "px-2 py-0.5 rounded text-[8px] font-black uppercase border",
                        config.validationStatus === 'PASS' ? "bg-green-500/10 text-green-500 border-green-500/20" : "bg-amber-500/10 text-amber-500 border-amber-500/20"
                     )}>{config.validationStatus}</div>
                  </div>
                  <div className="flex justify-between items-end mt-6">
                     <div className="flex items-center gap-2">
                        <div className="w-5 h-5 rounded-full bg-slate-800 border border-white/10 flex items-center justify-center text-[8px] text-slate-500">{config.owner.split(' ').map(n=>n[0]).join('')}</div>
                        <span className="text-[10px] text-slate-500 italic">{config.lastModified}</span>
                     </div>
                     <div className="text-[9px] font-mono text-slate-700">{config.usedBy.length} Agents Connected</div>
                  </div>
               </div>
            ))}
         </div>

         {/* Config Editor/Detail */}
         <div className="col-span-12 lg:col-span-8 flex flex-col overflow-hidden">
            <AnimatePresence mode="wait">
               {selectedConfig ? (
                  <motion.div 
                    key={selectedConfig.id}
                    initial={{ opacity: 0, x: 20 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={{ opacity: 0, x: -20 }}
                    className="flex-1 flex flex-col glass-panel rounded-[3rem] border-white/5 bg-white/[0.01] overflow-hidden"
                  >
                     <div className="px-10 py-8 border-b border-white/5 flex justify-between items-center bg-white/[0.02]">
                        <div className="flex items-center gap-6">
                           <div>
                              <h3 className="text-2xl font-display font-medium text-white italic">{selectedConfig.name}</h3>
                              <div className="flex items-center gap-2 mt-1">
                                 <span className="text-[10px] font-black text-brand-400 uppercase tracking-[0.2em]">{selectedConfig.id}</span>
                                 <span className="text-slate-700 px-2 font-mono text-[10px]">/</span>
                                 <span className="text-[10px] font-bold text-slate-500">{selectedConfig.environment}</span>
                              </div>
                           </div>
                        </div>
                        <div className="flex gap-2">
                           {['VISUAL', 'YAML', 'JSON'].map((m) => (
                              <button 
                                key={m}
                                onClick={() => setViewMode(m as any)}
                                className={cn(
                                   "px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all",
                                   viewMode === m ? "bg-brand-500 text-white" : "bg-white/5 text-slate-500 hover:text-slate-300"
                                )}
                              >
                                {m}
                              </button>
                           ))}
                        </div>
                     </div>

                     <div className="flex-1 p-10 font-mono text-sm overflow-y-auto custom-scrollbar bg-black/20">
                        {viewMode === 'YAML' ? (
                           <textarea 
                             className="w-full h-full bg-transparent border-none focus:outline-none text-slate-300 leading-relaxed resize-none"
                             value={selectedConfig.content}
                             spellCheck={false}
                           />
                        ) : viewMode === 'JSON' ? (
                          <pre className="text-brand-300">
                             {JSON.stringify(selectedConfig, null, 2)}
                          </pre>
                        ) : (
                          <div className="space-y-8 animate-in fade-in duration-500">
                             <div className="grid grid-cols-2 gap-8">
                                <div className="space-y-2">
                                   <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest">Configuration Name</label>
                                   <input className="w-full bg-white/5 border border-white/10 rounded-2xl px-6 py-4 text-white focus:outline-none" value={selectedConfig.name} />
                                </div>
                                <div className="space-y-2">
                                   <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest">Semantic Version</label>
                                   <input className="w-full bg-white/5 border border-white/10 rounded-2xl px-6 py-4 text-white focus:outline-none" value={selectedConfig.version} />
                                </div>
                             </div>
                             <div className="pt-8 border-t border-white/5">
                                <h4 className="text-xs font-black text-slate-500 uppercase tracking-widest mb-6">Execution Dependencies</h4>
                                <div className="flex gap-4">
                                   {selectedConfig.usedBy.map(id => (
                                      <div key={id} className="px-4 py-3 bg-white/5 border border-white/5 rounded-2xl flex items-center gap-3">
                                         <Bot className="w-4 h-4 text-brand-400" />
                                         <span className="text-xs font-bold text-slate-300">{id}</span>
                                      </div>
                                   ))}
                                </div>
                             </div>
                          </div>
                        )}
                     </div>

                     <div className="px-10 py-8 border-t border-white/5 flex justify-between items-center">
                        <div className="flex gap-4">
                           <button className="p-3 bg-white/5 hover:bg-red-500/10 rounded-2xl border border-white/10 text-slate-500 hover:text-red-500 transition-all"><Trash2 className="w-5 h-5" /></button>
                           <button className="p-3 bg-white/5 hover:bg-white/10 rounded-2xl border border-white/10 text-slate-500 transition-all"><Copy className="w-5 h-5" /></button>
                           <button className="p-3 bg-white/5 hover:bg-white/10 rounded-2xl border border-white/10 text-slate-500 transition-all"><HistoryIcon className="w-5 h-5" /></button>
                        </div>
                        <div className="flex gap-4">
                           <button className="px-8 py-3 bg-white/5 border border-white/10 rounded-2xl text-xs font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">Discard</button>
                           <button className="px-8 py-3 bg-brand-500 text-white rounded-2xl text-xs font-black uppercase tracking-[0.2em] shadow-2xl shadow-brand-500/20 flex items-center gap-2">
                             <Save className="w-4 h-4" /> Commit Changes
                           </button>
                        </div>
                     </div>
                  </motion.div>
               ) : (
                  <div className="flex-1 flex flex-col items-center justify-center glass-panel rounded-[3rem] border-white/5 bg-white/[0.01] border-dashed text-slate-600">
                     <FileCode className="w-16 h-16 opacity-10 mb-6" />
                     <p className="text-[10px] font-black uppercase tracking-[0.4em]">Select an entity to modify blueprint</p>
                  </div>
               )}
            </AnimatePresence>
         </div>
      </div>
    </div>
  );
};

// End of ConfigRegistry

