import React, { useState } from 'react';
import { 
  Rocket, 
  ChevronRight, 
  RefreshCw, 
  Check, 
  X, 
  ArrowRight, 
  AlertTriangle, 
  Zap, 
  Database, 
  Bot, 
  Wrench, 
  Layers, 
  FileCode, 
  ShieldCheck, 
  Brain, 
  Clock, 
  Activity, 
  Plus,
  Minus,
  Search,
  Filter,
  Lock
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';

export const DriftDetection = () => {
  const [sourceEnv, setSourceEnv] = useState('UAT');
  const [targetEnv, setTargetEnv] = useState('PROD');

  const drifts = [
    { type: 'AGENT', name: 'Customer Support Bot', source: 'v4.2.1', target: 'v4.2.0', severity: 'HIGH' },
    { type: 'KB', name: 'Policy Knowledge Base', source: 'kb-corp-v5', target: 'kb-corp-v4', severity: 'CRITICAL' },
    { type: 'PROMPT', name: 'System Context v2', source: 'revision-882', target: 'revision-754', severity: 'HIGH' },
    { type: 'CONFIG', name: 'LLM Temperature', source: '0.8', target: '0.7', severity: 'LOW' },
    { type: 'TOOL', name: 'Salesforce Connector', source: 'v2.1', target: 'v2.1', severity: 'NONE' },
    { type: 'SECRET', name: 'API_GATEWAY_TOKEN', source: 'verified', target: 'mismatch', severity: 'CRITICAL' },
  ];

  const getSeverityColor = (sev: string) => {
    switch (sev) {
      case 'CRITICAL': return 'text-red-500';
      case 'HIGH': return 'text-amber-500';
      case 'LOW': return 'text-blue-500';
      default: return 'text-slate-600';
    }
  };

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700 h-full flex flex-col">
      <div className="flex justify-between items-end mb-4">
        <div>
          <div className="flex items-center gap-2 text-brand-400 text-xs font-black font-mono tracking-widest uppercase mb-3 text-glow-blue">
            <RefreshCw className="w-4 h-4" />
            Control Plane / Drift Analysis
          </div>
          <h1 className="text-5xl font-display font-medium tracking-tight text-white mb-2 italic">Semantic Drift Detector</h1>
          <p className="text-slate-500 text-lg">Compare neural assets across environments to ensure bit-perfect production parity.</p>
        </div>
        <div className="flex gap-4">
           <button className="px-8 py-3 bg-brand-500 text-white rounded-2xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all shadow-2xl shadow-brand-500/20 flex items-center gap-2">
             <Zap className="w-4 h-4" /> Synchronize Environments
           </button>
        </div>
      </div>

      <div className="glass-panel p-10 rounded-[3rem] border-white/5 bg-white/[0.01]">
         <div className="flex items-center justify-between mb-12">
            <div className="flex items-center gap-8 flex-1">
               <div className="flex flex-col gap-2 flex-1">
                  <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest ml-1">Source Environment</label>
                  <select 
                    value={sourceEnv}
                    onChange={e => setSourceEnv(e.target.value)}
                    className="w-full bg-white/5 border border-white/10 rounded-2xl px-6 py-4 text-white text-xl font-display font-medium appearance-none focus:border-brand-500 transition-all"
                  >
                     <option>DEV</option>
                     <option>UAT</option>
                  </select>
               </div>
               <div className="flex items-center justify-center p-4 rounded-full bg-white/5 border border-white/10 mt-8">
                  <ArrowRight className="w-6 h-6 text-brand-400" />
               </div>
               <div className="flex flex-col gap-2 flex-1">
                  <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest ml-1">Target Comparison</label>
                  <select 
                    value={targetEnv}
                    onChange={e => setTargetEnv(e.target.value)}
                    className="w-full bg-white/5 border border-white/10 rounded-2xl px-6 py-4 text-white text-xl font-display font-medium appearance-none focus:border-brand-500 transition-all"
                  >
                     <option>PROD</option>
                     <option>STAGING</option>
                  </select>
               </div>
            </div>
            <div className="ml-12 border-l border-white/10 pl-12">
               <div className="text-[10px] font-black text-slate-600 uppercase tracking-widest mb-2">Platform Confidence</div>
               <div className="text-4xl font-display font-medium text-amber-500">82.4%</div>
               <div className="text-[9px] text-slate-500 mt-2 font-black tracking-widest uppercase">SYMMETRY SCORE</div>
            </div>
         </div>

         <div className="space-y-4">
            <div className="grid grid-cols-12 px-8 py-4 bg-white/[0.02] border-b border-white/5 text-[10px] font-black text-slate-600 uppercase tracking-widest">
               <div className="col-span-4">Canonical Asset</div>
               <div className="col-span-3">Source: {sourceEnv}</div>
               <div className="col-span-3">Target: {targetEnv}</div>
               <div className="col-span-2 text-right">Severity</div>
            </div>
            <div className="space-y-1">
               {drifts.map((d) => (
                  <div key={`${d.type}-${d.name}`} className="grid grid-cols-12 px-8 py-6 items-center hover:bg-white/[0.02] transition-colors group cursor-pointer border-b border-white/[0.01] last:border-none">
                     <div className="col-span-4 flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-white/5 flex items-center justify-center text-slate-500 group-hover:text-brand-400 transition-colors">
                           {d.type === 'AGENT' && <Bot className="w-5 h-5" />}
                           {d.type === 'KB' && <Database className="w-5 h-5" />}
                           {d.type === 'TOOL' && <Wrench className="w-5 h-5" />}
                           {d.type === 'CONFIG' && <FileCode className="w-5 h-5" />}
                           {d.type === 'SECRET' && <Lock className="w-5 h-5" />}
                           {d.type === 'PROMPT' && <Brain className="w-5 h-5" />}
                        </div>
                        <div>
                           <div className="text-sm font-bold text-white group-hover:text-brand-400 transition-colors tracking-tight italic uppercase">{d.name}</div>
                           <div className="text-[9px] text-slate-600 font-black uppercase tracking-widest">{d.type} IDENTITY</div>
                        </div>
                     </div>
                     <div className="col-span-3">
                        <div className="text-[11px] font-mono text-slate-400">{d.source}</div>
                     </div>
                     <div className="col-span-3 flex items-center gap-3">
                        <div className={cn(
                           "text-[11px] font-mono",
                           d.source !== d.target ? "text-red-400 font-bold" : "text-slate-600"
                        )}>{d.target}</div>
                        {d.source !== d.target && <AlertTriangle className="w-4 h-4 text-amber-500" />}
                     </div>
                     <div className="col-span-2 text-right">
                        <span className={cn(
                           "px-3 py-1 bg-white/5 border border-white/5 rounded-full text-[9px] font-black uppercase tracking-widest",
                           getSeverityColor(d.severity)
                        )}>
                           {d.severity}
                        </span>
                     </div>
                  </div>
               ))}
            </div>
         </div>
      </div>

      <div className="grid grid-cols-2 gap-8">
         <div className="glass-panel p-8 rounded-[2.5rem] border-white/5 bg-white/[0.01]">
            <h4 className="text-xs font-black text-slate-600 uppercase tracking-widest mb-8">Drift Reconciliation Log</h4>
            <div className="space-y-6">
               {[
                 { action: 'Config Overridden', env: 'PROD', time: '2d ago', user: 'SYSTEM' },
                 { action: 'Manual Hotfix Applied', env: 'PROD', time: '5d ago', user: 'SARAH CONNOR' },
                 { action: 'Rollback Executed', env: 'UAT', time: '1w ago', user: 'ELENA RIGBY' },
               ].map((log) => (
                  <div key={`${log.time}-${log.action}`} className="flex justify-between items-center text-xs">
                     <div>
                        <div className="font-bold text-slate-300">{log.action}</div>
                        <div className="text-[9px] text-slate-600 font-black uppercase mt-1 tracking-widest">{log.env} • {log.user}</div>
                     </div>
                     <span className="text-[10px] font-mono text-slate-700">{log.time}</span>
                  </div>
               ))}
            </div>
         </div>
         <div className="glass-panel p-8 rounded-[2.5rem] border-brand-500/10 bg-brand-500/5 relative overflow-hidden group border-dashed">
            <h4 className="text-xl font-display font-medium text-brand-400 mb-4 italic">Automated Sync Profile</h4>
            <p className="text-slate-500 text-sm leading-relaxed mb-6 italic">
              "Environment symmetry is currently maintained manually. Automated CI/CD triggers are disabled for PROD to enforce governance gates."
            </p>
            <button className="px-6 py-3 bg-brand-500 text-white rounded-xl text-[10px] font-black uppercase tracking-widest hover:bg-brand-600 transition-all">
               Modify Cluster Policy
            </button>
         </div>
      </div>
    </div>
  );
};
