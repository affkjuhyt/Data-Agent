import React, { useState } from 'react';
import { 
  History as HistoryIcon, 
  Search, 
  Filter, 
  Play, 
  RefreshCw, 
  ChevronRight, 
  Bot, 
  Cpu, 
  Zap, 
  CheckCircle2, 
  XCircle, 
  Clock, 
  BarChart3, 
  Database, 
  Wrench, 
  GitPullRequest, 
  Layers, 
  ShieldCheck, 
  AlertCircle, 
  ExternalLink,
  MoreVertical,
  Activity,
  User,
  MoreHorizontal
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { MOCK_RUNS } from '../../../constants/agentMock';
import { AgentRun, AgentStatus } from '../../../types/agent';

export const RunRegistry = () => {
  const [selectedRun, setSelectedRun] = useState<AgentRun | null>(null);

  const getStatusColor = (status: AgentStatus) => {
    switch (status) {
      case 'ACTIVE': return 'bg-green-500/10 text-green-400 border-green-500/20';
      case 'BUSY': return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
      case 'FAILED': return 'bg-red-500/10 text-red-500 border-red-500/20';
      case 'WAITING_APPROVAL': return 'bg-purple-500/10 text-purple-400 border-purple-500/20';
      default: return 'bg-white/5 text-slate-500 border-white/10';
    }
  };

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700 h-full flex flex-col">
      <div className="flex justify-between items-end mb-4">
        <div>
          <div className="flex items-center gap-2 text-brand-400 text-xs font-black font-mono tracking-widest uppercase mb-3 text-glow-blue">
            <Activity className="w-4 h-4" />
            Runtime Execution Operations
          </div>
          <h1 className="text-5xl font-display font-medium tracking-tight text-white mb-2 italic">Global Run Registry</h1>
          <p className="text-slate-500 text-lg">Monitor live executions, handle manual approvals, and replay mission traces.</p>
        </div>
        <div className="flex gap-4">
           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white transition-all flex items-center gap-2">
             <RefreshCw className="w-4 h-4" /> Hard Refresh
           </button>
           <button className="px-5 py-2.5 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20 flex items-center gap-2">
             <GitPullRequest className="w-4 h-4" /> Human Approvals
             <span className="w-5 h-5 bg-white/20 rounded-full flex items-center justify-center text-[10px]">3</span>
           </button>
        </div>
      </div>

      <div className="glass-panel overflow-hidden rounded-[2.5rem] border-white/5 bg-white/[0.01]">
         <div className="px-10 py-6 border-b border-white/5 flex justify-between items-center bg-white/[0.02]">
            <div className="flex items-center gap-6">
               <div className="relative group">
                 <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                 <input 
                   type="text" 
                   placeholder="Query runs by ID, agent, or tenant..." 
                   className="bg-black/20 border border-white/5 rounded-2xl pl-12 pr-6 py-2.5 text-xs w-80 focus:outline-none focus:border-brand-500/50 transition-all font-mono"
                 />
               </div>
               <div className="flex gap-2">
                  <button className="px-4 py-2 bg-white/5 rounded-xl text-[10px] font-bold text-slate-400">All Runs</button>
                  <button className="px-4 py-2 bg-white/5 rounded-xl text-[10px] font-bold text-red-500">Failed Only</button>
                  <button className="px-4 py-2 bg-purple-500/10 rounded-xl text-[10px] font-bold text-purple-400">Wait Approval</button>
               </div>
            </div>
            <div className="text-[10px] font-black text-slate-600 uppercase tracking-widest">
               Displaying Last 1,000 Executions Across Global Compute
            </div>
         </div>
         <table className="w-full text-left">
            <thead>
               <tr className="text-[10px] font-black text-slate-500 uppercase tracking-widest border-b border-white/5">
                  <th className="px-10 py-5">Execution Identity</th>
                  <th className="px-6 py-5">Trigger</th>
                  <th className="px-6 py-5">Started At</th>
                  <th className="px-6 py-5">Duration</th>
                  <th className="px-6 py-5">Compute Accrual</th>
                  <th className="px-6 py-5">Status</th>
                  <th className="px-10 py-5 text-right">Actions</th>
               </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
               {MOCK_RUNS.map(run => (
                 <tr 
                   key={run.id} 
                   className="group hover:bg-white/[0.02] transition-colors cursor-pointer"
                   onClick={() => setSelectedRun(run)}
                 >
                    <td className="px-10 py-6">
                       <div className="flex items-center gap-4">
                          <div className={cn("w-10 h-10 rounded-xl flex items-center justify-center border", getStatusColor(run.status))}>
                             <Activity className="w-4 h-4" />
                          </div>
                          <div>
                             <div className="font-bold text-white mb-0.5">{run.agentName}</div>
                             <div className="flex items-center gap-2 text-[10px] text-slate-500 font-mono italic">
                                <span>{run.id}</span>
                                <span className="opacity-30">â€¢</span>
                                <span>{run.tenant}</span>
                             </div>
                          </div>
                       </div>
                    </td>
                    <td className="px-6 py-6 font-mono text-xs">
                       <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full bg-brand-500/40" />
                          <span className="text-slate-300 font-black">{run.trigger}</span>
                       </div>
                    </td>
                    <td className="px-6 py-6">
                       <div className="text-xs font-bold text-slate-400">{run.startedAt}</div>
                    </td>
                    <td className="px-6 py-6">
                       <div className="flex items-center gap-2">
                          <Clock className="w-3.5 h-3.5 text-slate-700" />
                          <span className="text-sm font-display font-medium text-slate-300 tracking-tight">{run.duration}</span>
                       </div>
                    </td>
                    <td className="px-6 py-6">
                       <div className="space-y-1">
                          <div className="text-sm font-bold text-white">${run.cost.toFixed(3)}</div>
                          <div className="flex gap-2">
                             <div className="text-[9px] font-black uppercase text-brand-400">{run.toolCalls} Tools</div>
                             <div className="text-[9px] font-black uppercase text-purple-400">{run.retrievalCalls} KBs</div>
                          </div>
                       </div>
                    </td>
                    <td className="px-6 py-6">
                       <div className={cn(
                          "px-3 py-1 rounded-full text-[9px] font-black uppercase tracking-widest border inline-flex items-center gap-2",
                          getStatusColor(run.status)
                       )}>
                          {run.status === 'ACTIVE' && <div className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />}
                          {run.status}
                       </div>
                    </td>
                    <td className="px-10 py-6 text-right">
                       <button className="p-2 hover:bg-white/10 rounded-xl transition-all">
                          <MoreHorizontal className="w-5 h-5 text-slate-500" />
                       </button>
                    </td>
                 </tr>
               ))}
            </tbody>
         </table>
      </div>

      <div className="grid grid-cols-3 gap-8">
         <div className="glass-panel p-8 rounded-[2rem] border-white/5 bg-white/[0.01]">
            <h4 className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-6">Execution Failure Root Causes</h4>
            <div className="space-y-6">
               {[
                 { id: 'kb-timeout', cause: 'KB Connectivity Timeout', count: 12, trend: '+4%' },
                 { id: 'tool-auth', cause: 'Tool Auth Failure', count: 8, trend: '-2%' },
                 { id: 'hallucination', cause: 'Hallucination Exceeded Guardrail', count: 5, trend: '0%' },
                 { id: 'model-tokens', cause: 'Model Input Tokens Exceeded', count: 3, trend: '+1%' },
               ].map((c) => (
                  <div key={c.id} className="flex justify-between items-center group cursor-pointer">
                     <span className="text-xs font-bold text-slate-400 group-hover:text-red-500 transition-colors">{c.cause}</span>
                     <div className="flex items-center gap-4">
                        <span className="text-sm font-mono font-bold text-white">{c.count}</span>
                        <span className={cn("text-[10px] font-mono", c.trend.startsWith('+') ? "text-red-500" : "text-green-500")}>{c.trend}</span>
                     </div>
                  </div>
               ))}
            </div>
         </div>
         <div className="glass-panel p-8 rounded-[2rem] border-white/5 bg-white/[0.01]">
            <h4 className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-6">Avg Compute Duration (7d)</h4>
            <div className="h-32 flex items-end gap-2">
               {[40, 65, 30, 80, 50, 90, 45].map((h, i) => (
                  <div 
                    key={`bar-${i}`} 
                    className="flex-1 bg-brand-500/20 hover:bg-brand-500/40 rounded-t-lg transition-all cursor-pointer relative group" 
                    style={{ height: `${h}%` }}
                  >
                     <div className="absolute top-[-25px] left-1/2 -translate-x-1/2 opacity-0 group-hover:opacity-100 bg-white text-black text-[10px] px-2 py-0.5 rounded font-black transition-all">
                        {h}s
                     </div>
                  </div>
               ))}
            </div>
         </div>
         <div className="glass-panel p-8 rounded-[2rem] border-brand-500/10 bg-brand-500/5 relative overflow-hidden group border-dashed">
            <div className="absolute top-0 right-0 p-8 rotate-12 opacity-10 group-hover:rotate-0 transition-transform duration-700">
               <Zap className="w-24 h-24 text-brand-400" />
            </div>
            <h4 className="text-xl font-display font-medium mb-4 italic">Predictive Scaling Ops</h4>
            <p className="text-slate-500 text-sm leading-relaxed mb-6">
               AI Infrastructure is anticipating a 14% spike in Railway Incident Agent usage over the next 4 hours based on historical weather patterns.
            </p>
            <button className="px-6 py-2.5 bg-brand-500 text-white rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all">Increase Warm Pool</button>
         </div>
      </div>
    </div>
  );
};
