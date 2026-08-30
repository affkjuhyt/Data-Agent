import React, { useState } from 'react';
import { 
  Terminal, 
  Copy, 
  Check, 
  ExternalLink, 
  Code, 
  Lock, 
  Globe, 
  Cpu, 
  ChevronRight, 
  GitPullRequest, 
  History as HistoryIcon, 
  Zap, 
  Search,
  ArrowRight
} from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../../lib/utils';

export const CLIScreen = () => {
  const [copied, setCopied] = useState<string | null>(null);

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text);
    setCopied(text);
    setTimeout(() => setCopied(null), 2000);
  };

  const commands = [
    { cmd: 'agent login', desc: 'Authenticate with the platform control plane.' },
    { cmd: 'agent init', desc: 'Initialize a new agent project locally.' },
    { cmd: 'agent deploy support-agent --env uat', desc: 'Deploy locally developed unit to UAT environment.' },
    { cmd: 'agent run --id agt-001 --query "status check"', desc: 'Execute a test run directly from the terminal.' },
    { cmd: 'agent config pull agt-001', desc: 'Pull latest canonical config to disk.' },
    { cmd: 'agent logs --tail', desc: 'Stream real-time execution logs.' }
  ];

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700">
      <div className="flex justify-between items-end mb-8">
        <div>
          <div className="flex items-center gap-2 text-brand-400 text-xs font-black font-mono tracking-widest uppercase mb-3 text-glow-blue">
            <Terminal className="w-4 h-4" />
            Developer / SDK Interface
          </div>
          <h1 className="text-5xl font-display font-medium tracking-tight text-white italic">Agentic CLI</h1>
        </div>
        <div className="flex gap-4">
           <button className="px-5 py-2.5 bg-white/5 border border-white/10 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-300 hover:text-white transition-all flex items-center gap-2">
             <ExternalLink className="w-4 h-4" /> Documentation
           </button>
           <button className="px-5 py-2.5 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20 flex items-center gap-2">
             <Zap className="w-4 h-4" /> Generate API Token
           </button>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-8">
         <div className="col-span-12 lg:col-span-7 space-y-8">
            <div className="glass-panel p-10 rounded-[3rem] border-white/5 bg-black/40 relative overflow-hidden">
               <div className="absolute top-0 right-0 p-8">
                  <Terminal className="w-24 h-24 text-white/[0.02]" />
               </div>
               <h3 className="text-xl font-bold mb-8 flex items-center gap-3">
                 <Cpu className="w-5 h-5 text-brand-400" />
                 Global SDK Installation
               </h3>
               <div className="space-y-6 relative">
                  <div>
                    <div className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-3 ml-1">Node.js / NPM</div>
                    <div className="flex items-center gap-3 p-5 bg-black rounded-2xl border border-white/10 group">
                       <code className="text-sm font-mono text-brand-300 flex-1">npm install @company/agent-cli -g</code>
                       <button 
                         onClick={() => copyToClipboard('npm install @company/agent-cli -g')}
                         className="p-2 hover:bg-white/5 rounded-xl transition-all"
                       >
                         {copied === 'npm install @company/agent-cli -g' ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4 text-slate-500" />}
                       </button>
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-3 ml-1">Python / PIP</div>
                    <div className="flex items-center gap-3 p-5 bg-black rounded-2xl border border-white/10 group">
                       <code className="text-sm font-mono text-purple-300 flex-1">pip install company-agent-cli</code>
                       <button 
                         onClick={() => copyToClipboard('pip install company-agent-cli')}
                         className="p-2 hover:bg-white/5 rounded-xl transition-all"
                       >
                         {copied === 'pip install company-agent-cli' ? <Check className="w-4 h-4 text-green-500" /> : <Copy className="w-4 h-4 text-slate-500" />}
                       </button>
                    </div>
                  </div>
               </div>
            </div>

            <div className="glass-panel p-10 rounded-[3rem] border-white/5 bg-white/[0.01]">
               <h3 className="text-xl font-bold mb-8 flex items-center gap-3">
                 <Code className="w-5 h-5 text-amber-500" />
                 Common Commands
               </h3>
               <div className="space-y-4">
                  {commands.map((c, idx) => (
                    <div key={c.cmd} className="flex items-center gap-4 p-4 hover:bg-white/[0.02] rounded-2xl transition-all group border border-transparent hover:border-white/5">
                       <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center text-slate-500 font-mono text-xs italic">{idx+1}</div>
                       <div className="flex-1">
                          <div className="text-sm font-mono text-slate-200">{c.cmd}</div>
                          <div className="text-[10px] text-slate-600 mt-0.5">{c.desc}</div>
                       </div>
                       <button onClick={() => copyToClipboard(c.cmd)} className="p-2 opacity-0 group-hover:opacity-100 transition-all">
                          <Copy className="w-4 h-4 text-slate-500" />
                       </button>
                    </div>
                  ))}
               </div>
            </div>
         </div>

         <div className="col-span-12 lg:col-span-5 space-y-8">
            <div className="glass-panel p-10 rounded-[3rem] border-white/5 bg-white/[0.01]">
               <h3 className="text-xl font-bold mb-8 flex items-center gap-3">
                 <HistoryIcon className="w-5 h-5 text-brand-400" />
                 Local Interaction Log
               </h3>
               <div className="space-y-6">
                  {[
                    { node: 'MacBook-Pro.local', op: 'agent login', status: 'SUCCESS', time: '2m ago' },
                    { node: 'MacBook-Pro.local', op: 'agent deploy ops-unit', status: 'SUCCESS', time: '15m ago' },
                    { node: 'Jenkins-CI-Worker', op: 'agent test --all', status: 'FAILED', time: '1h ago' },
                    { node: 'MacBook-Pro.local', op: 'agent config pull', status: 'SUCCESS', time: '2h ago' },
                  ].map((log) => (
                    <div key={`${log.time}-${log.op}`} className="flex justify-between items-start">
                       <div>
                          <div className="text-[11px] font-bold text-white mb-0.5 italic">{log.op}</div>
                          <div className="text-[9px] font-mono text-slate-600">{log.node}</div>
                       </div>
                       <div className="text-right">
                          <div className={cn("text-[9px] font-black uppercase tracking-widest", log.status === 'SUCCESS' ? "text-green-500" : "text-red-500")}>{log.status}</div>
                          <div className="text-[9px] font-mono text-slate-700 mt-0.5">{log.time}</div>
                       </div>
                    </div>
                  ))}
               </div>
            </div>

            <div className="glass-panel p-10 rounded-[3rem] border-white/5 bg-gradient-to-br from-brand-500/10 to-transparent">
               <div className="text-center space-y-6">
                  <div className="w-16 h-16 rounded-3xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center mx-auto mb-4">
                    <HistoryIcon className="w-8 h-8 text-brand-400" />
                  </div>
                  <h4 className="text-xl font-display font-medium">CLI Deployment Center</h4>
                  <p className="text-slate-500 text-sm leading-relaxed px-4">
                    Orchestrate deployments directly from your existing CI/CD pipelines with our hardened binary interface.
                  </p>
                  <button className="w-full py-4 bg-white text-black font-black rounded-2xl text-[10px] tracking-widest uppercase hover:bg-slate-200 transition-all shadow-xl shadow-white/5">
                    View Full SDK Specs
                  </button>
               </div>
            </div>
         </div>
      </div>
    </div>
  );
};
