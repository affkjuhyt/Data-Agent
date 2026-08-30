import React, { useState } from 'react';
import { 
  Zap, 
  ChevronRight, 
  Plus, 
  Database, 
  Bot, 
  Wrench, 
  ShieldCheck, 
  Network, 
  GitBranch, 
  Globe, 
  Lock, 
  Check, 
  ArrowRight,
  MoreVertical,
  X,
  Layers,
  Settings,
  Activity,
  Cpu,
  Smartphone,
  Server,
  Cloud
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';

export const ProvisioningView = () => {
  const [step, setStep] = useState(1);
  const [selectedSolution, setSelectedSolution] = useState<string | null>(null);

  const solutions = [
    { id: 'gov', name: 'Government Policy Assistant', icon: Globe, desc: 'Highly secure RAG agent for public documents.' },
    { id: 'rail', name: 'Railway Incident Agent', icon: Smartphone, desc: 'Field-ready agent for hardware incident reports.' },
    { id: 'fin', name: 'Banking Compliance Bot', icon: Lock, desc: 'Automates regulatory audit cross-checks.' },
    { id: 'med', name: 'Clinical Support Unit', icon: Activity, desc: 'Medical Grade reasoning with HIPAA guardrails.' },
    { id: 'it', name: 'IT Infrastructure Ops', icon: Server, desc: 'Autonomous monitoring and self-healing.' },
    { id: 'dev', name: 'Code Review Agent', icon: Cloud, desc: 'Deep syntax and security analysis for PRs.' }
  ];

  return (
    <div className="space-y-12 animate-in fade-in slide-in-from-bottom-8 duration-700 max-w-6xl mx-auto">
      <div className="text-center space-y-4">
        <div className="flex justify-center mb-8">
           <div className="w-20 h-20 rounded-[2.5rem] bg-brand-500/10 border border-brand-500/20 flex items-center justify-center text-brand-400">
             <Zap className="w-10 h-10 text-glow-blue" />
           </div>
        </div>
        <h1 className="text-6xl font-display font-medium tracking-tight text-white mb-2 italic">Quick Provisioning</h1>
        <p className="text-slate-500 text-xl max-w-2xl mx-auto leading-relaxed uppercase text-[10px] font-black tracking-[0.4em]">Rapid Solution Deployment Center</p>
      </div>

      <div className="grid grid-cols-12 gap-12">
         {/* Step Sidebar */}
         <div className="col-span-12 lg:col-span-3 space-y-4">
            {[
              { id: 1, label: 'Solution Template' },
              { id: 2, label: 'Target Alignment' },
              { id: 3, label: 'KB & Data Source' },
              { id: 4, label: 'Runtime Profile' },
              { id: 5, label: 'Provision' }
            ].map(s => (
               <div 
                 key={s.id}
                 className={cn(
                   "p-5 rounded-2xl border transition-all flex items-center justify-between group",
                   step === s.id ? "bg-brand-500 text-white border-brand-500 shadow-xl shadow-brand-500/20" : 
                   step > s.id ? "bg-green-500/10 text-green-500 border-green-500/20" : "bg-white/5 border-white/5 text-slate-500"
                 )}
               >
                  <span className="text-[10px] font-black uppercase tracking-widest">{s.label}</span>
                  {step > s.id && <Check className="w-4 h-4" />}
                  {step === s.id && <ChevronRight className="w-4 h-4 animate-bounce-x" />}
               </div>
            ))}
         </div>

         {/* Selection Area */}
         <div className="col-span-12 lg:col-span-9">
            <div className="glass-panel p-12 rounded-[4rem] border-white/5 bg-white/[0.01] min-h-[500px] flex flex-col">
               <AnimatePresence mode="wait">
                  {step === 1 && (
                     <motion.div 
                       key="step1"
                       initial={{ opacity: 0, scale: 0.95 }}
                       animate={{ opacity: 1, scale: 1 }}
                       exit={{ opacity: 0, scale: 1.05 }}
                       className="grid grid-cols-2 gap-6"
                     >
                        {solutions.map(solution => (
                           <button 
                             key={solution.id}
                             onClick={() => { setSelectedSolution(solution.id); setStep(2); }}
                             className="p-8 bg-black/40 border border-white/5 rounded-[2.5rem] text-left hover:bg-brand-500/5 hover:border-brand-500/30 transition-all group"
                           >
                              <solution.icon className="w-10 h-10 text-slate-500 mb-6 group-hover:text-brand-400 group-hover:scale-110 transition-all" />
                              <h4 className="text-lg font-bold text-white mb-2">{solution.name}</h4>
                              <p className="text-slate-500 text-sm leading-relaxed italic">{solution.desc}</p>
                           </button>
                        ))}
                     </motion.div>
                  )}

                  {step === 2 && (
                    <motion.div 
                      key="step2"
                      initial={{ opacity: 0, x: 20 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="space-y-8 flex-1 flex flex-col justify-center max-w-md mx-auto w-full text-center"
                    >
                       <div className="text-[10px] font-black text-brand-400 uppercase tracking-[0.4em] mb-4">Deployment Target</div>
                       <select className="w-full bg-white/5 border border-white/10 rounded-2xl p-6 text-xl font-display font-medium appearance-none text-white focus:outline-none focus:border-brand-500 transition-all">
                          <option>BlueChip Enterprise</option>
                          <option>Global Logistics Corp</option>
                          <option>Federal Systems</option>
                       </select>
                       <div className="flex gap-4">
                          {['DEV', 'UAT', 'PROD'].map(env => (
                             <button key={env} className="flex-1 p-6 bg-white/5 border border-white/10 rounded-2xl text-xs font-black uppercase tracking-widest hover:bg-brand-500 hover:text-white transition-all">
                                {env}
                             </button>
                          ))}
                       </div>
                       <button 
                         onClick={() => setStep(3)}
                         className="mt-8 px-12 py-5 bg-brand-500 text-white rounded-[2rem] text-xs font-black uppercase tracking-[0.2em] shadow-2xl shadow-brand-500/40 flex items-center justify-center gap-3"
                       >
                          Verify Alignment <ArrowRight className="w-5 h-5" />
                       </button>
                    </motion.div>
                  )}

                  {step > 2 && (
                    <div className="flex-1 flex flex-col items-center justify-center text-center opacity-50 italic">
                       <Cpu className="w-20 h-20 text-slate-700 animate-pulse mb-8" />
                       <h3 className="text-2xl font-display font-medium">Orchestrating Platform Assets...</h3>
                       <p className="text-[10px] font-black text-slate-600 uppercase tracking-widest mt-2 underline decoration-brand-500/30">Connecting to Knowledge Graphs & Tool Bindings</p>
                    </div>
                  )}
               </AnimatePresence>
            </div>
         </div>
      </div>
    </div>
  );
};
