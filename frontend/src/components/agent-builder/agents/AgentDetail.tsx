import React, { useState } from 'react';
import { 
  Bot, 
  Cpu, 
  Settings, 
  Zap, 
  Play, 
  History as HistoryIcon, 
  ShieldAlert, 
  Activity, 
  Layers, 
  Search, 
  Plus, 
  Network, 
  Eye, 
  Database, 
  Wrench, 
  ShieldCheck, 
  MemoryStick, 
  GitBranch, 
  Repeat, 
  CheckCircle2, 
  AlertTriangle, 
  Clock, 
  ArrowUpRight, 
  MoreHorizontal, 
  ChevronRight, 
  FileCode, 
  Globe, 
  Lock, 
  User, 
  ExternalLink,
  Save,
  MessageSquare,
  BarChart3,
  Trash2,
  Copy,
  FileText,
  Terminal,
  RefreshCw,
  GitPullRequest,
  Check,
  X,
  ArrowLeft
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { Agent, AgentStatus } from '../../../types/agent';
import { 
  MOCK_AGENTS, 
  MOCK_TRACES, 
  MOCK_RUNS 
} from '../../../constants/agentMock';

interface AgentDetailProps {
  agentId: string;
  onBack: () => void;
  onRun: () => void;
  onTrace: () => void;
}

export const AgentDetailView: React.FC<AgentDetailProps> = ({ 
  agentId, 
  onBack,
  onRun,
  onTrace
}) => {
  const [activeTab, setActiveTab] = useState('OVERVIEW');
  const agent = MOCK_AGENTS.find(a => a.id === agentId)!;

  const TABS = [
    'OVERVIEW', 'CONFIGURATION', 'PROMPT', 'KB CONNECTION', 'TOOLS', 
    'MEMORY', 'GUARDRAILS', 'RUNS', 'TRACES', 'DEPLOYMENTS', 'AUDIT'
  ];

  const getStatusColor = (status: AgentStatus) => {
    switch (status) {
      case 'ACTIVE': return 'bg-green-500/10 text-green-400 border-green-500/20';
      case 'BUSY': return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      case 'FAILED': return 'bg-red-500/10 text-red-500 border-red-500/20';
      case 'IDLE': return 'bg-slate-500/10 text-slate-400 border-slate-500/20';
      default: return 'bg-white/5 text-slate-500 border-white/10';
    }
  };

  return (
    <div className="flex flex-col h-full animate-in fade-in slide-in-from-bottom-8 duration-700">
      {/* Detail Header */}
      <div className="flex justify-between items-center mb-12">
        <div className="flex items-center gap-8">
           <button 
             onClick={onBack}
             className="p-4 bg-white/5 border border-white/10 rounded-2xl text-slate-500 hover:text-white transition-all shadow-xl"
           >
             <ArrowLeft className="w-6 h-6" />
           </button>
           <div className="flex items-center gap-6">
              <div className={cn("w-20 h-20 rounded-3xl flex items-center justify-center font-bold text-3xl border shadow-2xl", getStatusColor(agent.status))}>
                {agent.name.charAt(0)}
              </div>
              <div>
                 <div className="flex items-center gap-3 text-[11px] font-black text-slate-500 uppercase tracking-[0.3em] mb-2">
                    <span className="text-brand-400">{agent.id}</span>
                    <span className="opacity-20">/</span>
                    <span>{agent.tenant}</span>
                    <div className={cn("w-2 h-2 rounded-full", agent.status === 'ACTIVE' ? "bg-green-500 animate-pulse" : "bg-slate-500")} />
                 </div>
                 <h1 className="text-5xl font-display font-medium text-white italic tracking-tight">{agent.name}</h1>
              </div>
           </div>
        </div>
        <div className="flex gap-4">
           <button className="px-6 py-3 bg-white/5 border border-white/10 rounded-2xl text-xs font-black uppercase tracking-widest text-slate-400 hover:bg-white/10 transition-all flex items-center gap-2">
             <Settings className="w-4 h-4" /> Config-as-Code
           </button>
           <button 
             onClick={onRun}
             className="px-8 py-3 bg-brand-500 text-white rounded-2xl text-xs font-black uppercase tracking-[0.2em] hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/30 flex items-center gap-3"
           >
             Initialize Execution
             <Play className="w-4 h-4" />
           </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 border-b border-white/5 mb-10 overflow-x-auto no-scrollbar pb-1">
         {TABS.map(tab => (
            <button 
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={cn(
                "px-6 py-4 text-[10px] font-black uppercase tracking-widest transition-all relative",
                activeTab === tab ? "text-brand-400" : "text-slate-600 hover:text-slate-400"
              )}
            >
              {tab}
              {activeTab === tab && <div className="absolute bottom-0 left-6 right-6 h-0.5 bg-brand-500 shadow-[0_0_10px_rgba(12,145,235,1)]" />}
            </button>
         ))}
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar pr-4 pb-20">
         {activeTab === 'OVERVIEW' && (
            <div className="grid grid-cols-12 gap-8">
               {/* Metrics & Identity */}
               <div className="col-span-12 lg:col-span-8 space-y-8">
                  <div className="glass-panel p-10 rounded-[3.5rem] border-white/5 bg-white/[0.01] relative overflow-hidden">
                     <div className="absolute top-0 right-0 p-10 opacity-5 rotate-12">
                        <Bot className="w-64 h-64 text-brand-400" />
                     </div>
                     <div className="relative">
                        <h3 className="text-[10px] font-black text-slate-600 uppercase tracking-[0.4em] mb-8">Agent Business Logic</h3>
                        <p className="text-2xl text-slate-300 leading-relaxed max-w-2xl font-display font-medium mb-12 italic">
                           "{agent.description}"
                        </p>
                        <div className="grid grid-cols-3 gap-12">
                           <div className="space-y-4">
                              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Target Objective</span>
                              <div className="text-sm font-bold text-white">{agent.businessPurpose}</div>
                           </div>
                           <div className="space-y-4">
                              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Ownership</span>
                              <div className="flex items-center gap-3">
                                 <div className="w-8 h-8 rounded-full bg-brand-500/10 flex items-center justify-center text-[10px] font-black text-brand-400 border border-brand-500/20">{agent.owner.split(' ').map(n=>n[0]).join('')}</div>
                                 <span className="text-sm font-bold text-white">{agent.owner}</span>
                              </div>
                           </div>
                           <div className="space-y-4">
                              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">Deployment Origin</span>
                              <div className="text-sm font-mono font-bold text-slate-400 italic">/{agent.project}/main</div>
                           </div>
                        </div>
                     </div>
                  </div>

                  <div className="grid grid-cols-2 gap-8">
                     <div className="glass-panel p-8 rounded-[2.5rem] border-white/5 bg-white/[0.01]">
                        <h4 className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-6 flex items-center gap-2">
                           <Activity className="w-4 h-4 text-brand-400" />
                           Platform Health Metrics
                        </h4>
                        <div className="space-y-6">
                           <div className="flex justify-between items-end">
                              <div>
                                 <div className="text-[9px] font-black text-slate-700 uppercase mb-1">Health Score</div>
                                 <div className={cn("text-3xl font-display font-medium", agent.healthScore > 80 ? "text-green-500" : "text-amber-500")}>{agent.healthScore}%</div>
                              </div>
                              <div className="text-right">
                                 <div className="text-[9px] font-black text-slate-700 uppercase mb-1">Avg Latency</div>
                                 <div className="text-3xl font-display font-medium text-white">{agent.latency}</div>
                              </div>
                           </div>
                           <div className="h-2 rounded-full bg-white/5 overflow-hidden">
                              <div className="h-full bg-brand-500" style={{ width: `${agent.healthScore}%` }} />
                           </div>
                        </div>
                     </div>
                     <div className="glass-panel p-8 rounded-[2.5rem] border-white/5 bg-white/[0.01]">
                        <h4 className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-6 flex items-center gap-2">
                           <Zap className="w-4 h-4 text-amber-500" />
                           Resource Consumption (24h)
                        </h4>
                        <div className="space-y-6">
                           <div className="flex justify-between items-end">
                              <div>
                                 <div className="text-[9px] font-black text-slate-700 uppercase mb-1">Tokens Generated</div>
                                 <div className="text-3xl font-display font-medium text-white">422.8k</div>
                              </div>
                              <div className="text-right">
                                 <div className="text-[9px] font-black text-slate-700 uppercase mb-1">Current Accrual</div>
                                 <div className="text-3xl font-display font-medium text-brand-400">${agent.cost.toFixed(2)}</div>
                              </div>
                           </div>
                           <div className="flex justify-between text-[9px] font-black uppercase text-slate-700">
                              <span>Model Usage: 94%</span>
                              <span>Bandwidth: 6%</span>
                           </div>
                        </div>
                     </div>
                  </div>
               </div>

               {/* Configuration Quick View */}
               <div className="col-span-12 lg:col-span-4 space-y-8">
                  <div className="glass-panel p-8 rounded-[2.5rem] border-white/5 bg-white/[0.01]">
                     <h4 className="text-xs font-black text-slate-500 uppercase tracking-widest mb-8">Active Runtime Bindings</h4>
                     <div className="space-y-6">
                        <div className="p-5 bg-black/40 rounded-2xl border border-white/5 flex items-center justify-between group cursor-pointer hover:border-brand-500/30 transition-all">
                           <div className="flex items-center gap-4">
                              <Database className="w-5 h-5 text-brand-400" />
                              <div>
                                 <div className="text-[10px] font-black text-slate-600 uppercase">Knowledge Base</div>
                                 <div className="text-xs font-bold text-white group-hover:text-brand-400 transition-colors uppercase italic underline">{agent.kbConnection}</div>
                              </div>
                           </div>
                           <ArrowUpRight className="w-4 h-4 text-slate-700 group-hover:text-white transition-all" />
                        </div>
                        <div className="p-5 bg-black/40 rounded-2xl border border-white/5 flex items-center justify-between group cursor-pointer hover:border-blue-500/30 transition-all">
                           <div className="flex items-center gap-4">
                              <Wrench className="w-5 h-5 text-blue-400" />
                              <div>
                                 <div className="text-[10px] font-black text-slate-600 uppercase">Tool Connections</div>
                                 <div className="text-xs font-bold text-white group-hover:text-blue-400 transition-colors">{agent.tools.length} Functions Active</div>
                              </div>
                           </div>
                           <ArrowUpRight className="w-4 h-4 text-slate-700 group-hover:text-white transition-all" />
                        </div>
                        <div className="p-5 bg-black/40 rounded-2xl border border-white/5 flex items-center justify-between group cursor-pointer hover:border-amber-500/30 transition-all">
                           <div className="flex items-center gap-4">
                              <ShieldCheck className="w-5 h-5 text-amber-500" />
                              <div>
                                 <div className="text-[10px] font-black text-slate-600 uppercase">Guardrail Policy</div>
                                 <div className="text-xs font-bold text-white group-hover:text-amber-500 transition-colors italic uppercase">Titanium-v4.2</div>
                              </div>
                           </div>
                           <ArrowUpRight className="w-4 h-4 text-slate-700 group-hover:text-white transition-all" />
                        </div>
                     </div>
                  </div>

                  <div className="glass-panel p-8 rounded-[2.5rem] border-white/5 bg-white/[0.01]">
                     <div className="flex justify-between items-center mb-6">
                        <h4 className="text-xs font-black text-slate-500 uppercase tracking-widest">Recent Traces</h4>
                        <button 
                          onClick={onTrace}
                          className="text-[10px] font-black text-brand-400 uppercase tracking-tighter hover:underline"
                        >
                          View Trace Explorer
                        </button>
                     </div>
                     <div className="divide-y divide-white/5">
                        {MOCK_TRACES.map((trace) => (
                           <div key={trace.id} className="py-4 first:pt-0 last:pb-0 group cursor-pointer">
                              <div className="flex justify-between items-start mb-2">
                                 <span className="text-[10px] font-mono text-slate-600 uppercase tracking-tighter">{trace.id}</span>
                                 <span className={cn("text-[9px] font-black uppercase tracking-widest", trace.status === 'SUCCESS' ? "text-green-500" : "text-red-500")}>{trace.status}</span>
                              </div>
                              <div className="text-xs font-bold text-slate-300 group-hover:text-brand-400 transition-colors line-clamp-1 italic">"{trace.query}"</div>
                           </div>
                        ))}
                     </div>
                  </div>
               </div>
            </div>
         )}

         {activeTab !== 'OVERVIEW' && (
            <div className="flex flex-col items-center justify-center p-32 opacity-30 italic">
               <Cpu className="w-20 h-20 text-slate-700 animate-pulse mb-8" />
               <h3 className="text-3xl font-display font-medium">Propagating Neural Settings for {activeTab}</h3>
               <p className="text-[10px] font-black text-slate-600 uppercase tracking-[0.4em] mt-4">Module Initializing across Global Data Centers</p>
            </div>
         )}
      </div>
    </div>
  );
};
