import React from 'react';
import { 
  CreditCard, 
  TrendingUp, 
  TrendingDown, 
  BarChart3, 
  DollarSign, 
  Zap, 
  AlertCircle,
  Plus,
  ArrowUpRight,
  ArrowDownRight,
  History,
  MoreVertical,
  Activity,
  Layers,
  ArrowRight
} from 'lucide-react';
import { motion } from 'motion/react';

const SPEND_DATA = [
  { month: 'JAN', tokens: 1.2, storage: 0.4, compute: 0.8 },
  { month: 'FEB', tokens: 2.1, storage: 0.5, compute: 1.2 },
  { month: 'MAR', tokens: 4.8, storage: 0.8, compute: 3.1 },
  { month: 'APR', tokens: 3.2, storage: 0.9, compute: 2.4 },
];

export const BillingQuotasSection = () => {
  return (
    <div className="space-y-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight mb-2 uppercase italic">Billing & Asset Quotas</h1>
          <p className="text-slate-500 font-medium leading-relaxed max-w-xl">
             Managing global AI budgets, token quotas, and cloud infrastructure costs. Multi-tenant cost attribution and automated capping.
          </p>
        </div>
        <div className="p-8 bg-brand-500/10 border border-brand-500/20 rounded-[32px] flex items-center gap-8">
           <div className="text-right">
              <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest leading-none mb-1">Current Balance</div>
              <div className="text-2xl font-bold text-white leading-none">$12,402.50</div>
           </div>
           <button className="px-6 py-3 bg-brand-500 text-white rounded-2xl font-bold text-xs shadow-lg shadow-brand-500/20 ring-4 ring-brand-500/10">Top Up</button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
         {[
           { label: 'Token Consumption', val: '4.2B', trend: '+12%', type: 'UP', icon: Zap },
           { label: 'Avg Monthly Spend', val: '$3.8k', trend: '-2%', type: 'DOWN', icon: DollarSign },
           { label: 'Storage Footprint', val: '1.2 PB', trend: '+5%', type: 'UP', icon: BarChart3 },
           { label: 'Efficiency Score', val: '92%', trend: '+4%', type: 'UP', icon: TrendingUp },
         ].map((stat, i) => (
           <div key={i} className="p-8 bg-white/[0.02] border border-white/5 rounded-[40px] group hover:bg-white/[0.04] transition-all">
              <div className="flex items-center justify-between mb-8">
                <div className="p-3.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 group-hover:scale-110 transition-transform">
                  <stat.icon className="w-5 h-5 text-brand-400" />
                </div>
                <div className={`flex items-center gap-1 text-[10px] font-black ${stat.type === 'UP' ? 'text-red-400' : 'text-emerald-400'}`}>
                   {stat.type === 'UP' ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                   {stat.trend}
                </div>
              </div>
              <div className="text-3xl font-bold text-white mb-1 uppercase italic tracking-tighter">{stat.val}</div>
              <div className="text-[10px] text-slate-500 font-black uppercase tracking-widest">{stat.label}</div>
           </div>
         ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 p-10 bg-white/[0.02] border border-white/5 rounded-[48px]">
           <div className="flex items-center justify-between mb-12">
              <h3 className="text-xl font-bold text-white">Resource Consumption Trends</h3>
              <div className="flex gap-4">
                 {['Tokens', 'Storage', 'Compute'].map(t => (
                   <div key={t} className="flex items-center gap-2">
                      <div className={`w-2.5 h-2.5 rounded-full ${t === 'Tokens' ? 'bg-brand-500' : t === 'Storage' ? 'bg-amber-400' : 'bg-emerald-400'}`} />
                      <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">{t}</span>
                   </div>
                 ))}
              </div>
           </div>
           
           <div className="h-64 flex items-end gap-12 px-4 border-b border-white/5 pb-2">
              {SPEND_DATA.map((d, i) => (
                <div key={i} className="flex-1 flex flex-col gap-1 items-center h-full justify-end group">
                   <div className="w-full flex flex-col gap-1 items-center">
                     <div className="w-full bg-brand-500 opacity-60 group-hover:opacity-100 transition-opacity rounded-t-lg" style={{ height: `${d.tokens * 20}px` }} />
                     <div className="w-full bg-amber-400 opacity-60 group-hover:opacity-100 transition-opacity" style={{ height: `${d.storage * 20}px` }} />
                     <div className="w-full bg-emerald-400 opacity-60 group-hover:opacity-100 transition-opacity rounded-b-lg" style={{ height: `${d.compute * 20}px` }} />
                   </div>
                   <span className="text-[10px] font-black text-slate-600 mt-4">{d.month}</span>
                </div>
              ))}
           </div>
        </div>

        <div className="p-10 bg-[#0a0a0a] border border-white/5 rounded-[48px]">
           <h3 className="text-xl font-bold text-white mb-10">Active Quotas</h3>
           <div className="space-y-8">
              {[
                { name: 'Public-Sector-GPT4', limit: '100M', used: 82, color: 'brand' },
                { name: 'Financial-Llama3-Local', limit: '500M', used: 45, color: 'amber' },
                { name: 'Health-OCR-Gemini', limit: '20M', used: 12, color: 'emerald' },
              ].map(q => (
                <div key={q.name} className="space-y-3">
                   <div className="flex justify-between items-center text-[10px] font-black uppercase tracking-widest">
                      <span className="text-white">{q.name}</span>
                      <span className="text-slate-500">{q.used}% of {q.limit}</span>
                   </div>
                   <div className="h-2 bg-white/5 rounded-full overflow-hidden flex">
                      <div className={`h-full bg-${q.color}-500 rounded-full`} style={{ width: `${q.used}%` }} />
                   </div>
                   <div className="flex justify-end">
                      <button className="text-[9px] font-bold text-slate-600 hover:text-brand-400 transition-colors uppercase tracking-[0.2em]">Escalate Quota</button>
                   </div>
                </div>
              ))}
           </div>
           
           <div className="mt-12 p-6 bg-amber-500/5 border border-amber-500/20 rounded-3xl flex items-center gap-4">
              <AlertCircle className="w-6 h-6 text-amber-400 flex-shrink-0" />
              <p className="text-xs text-slate-400 leading-tight">
                 Workspace 'Fin-Core' is nearing 90% quota. Automatic scale-down will trigger in 4 hours.
              </p>
           </div>
        </div>
      </div>
      
      <div className="p-10 bg-white/[0.02] border border-white/5 rounded-[48px]">
         <div className="flex items-center justify-between mb-8">
            <h3 className="text-xl font-bold text-white italic">Transaction Ledger</h3>
            <button className="p-2.5 bg-white/5 hover:bg-white/10 rounded-2xl border border-white/10 text-slate-400 hover:text-white transition-all">
               <History className="w-5 h-5" />
            </button>
         </div>
         <div className="space-y-4">
            {[
              { id: 'TX-402', desc: 'Azure OpenAI Invoicing - US-EAST', date: 'May 14, 2026', amount: '-$412.50', status: 'PAID' },
              { id: 'TX-401', desc: 'S3 Knowledge Storage - Enterprise Gold', date: 'May 12, 2026', amount: '-$892.00', status: 'PENDING' },
              { id: 'TX-400', desc: 'Linh N. - Managed Top-up', date: 'May 10, 2026', amount: '+$5000.00', status: 'VERIFIED' },
            ].map(tx => (
              <div key={tx.id} className="group flex items-center justify-between p-6 bg-white/[0.01] hover:bg-white/[0.03] border border-white/5 rounded-3xl transition-all">
                 <div className="flex items-center gap-6">
                    <div className="w-12 h-12 rounded-2xl bg-slate-900 border border-white/10 flex items-center justify-center text-slate-600 group-hover:text-brand-400 transition-colors">
                       <CreditCard className="w-6 h-6" />
                    </div>
                    <div>
                       <div className="text-white font-bold text-sm tracking-tight mb-1">{tx.desc}</div>
                       <div className="text-[10px] text-slate-500 uppercase font-black tracking-widest">{tx.date} • {tx.id}</div>
                    </div>
                 </div>
                 <div className="flex items-center gap-12">
                     <div className={`text-lg font-mono font-bold ${tx.amount.startsWith('+') ? 'text-emerald-400' : 'text-white'}`}>
                        {tx.amount}
                     </div>
                     <div className={`text-[10px] font-black uppercase tracking-widest px-3 py-1 rounded-full border ${
                        tx.status === 'PAID' || tx.status === 'VERIFIED' ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400' : 'bg-amber-500/10 border-amber-500/20 text-amber-400'
                     }`}>
                        {tx.status}
                     </div>
                     <ArrowRight className="w-5 h-5 text-slate-700 opacity-0 group-hover:opacity-100 transition-all cursor-pointer" />
                 </div>
              </div>
            ))}
         </div>
      </div>
    </div>
  );
};
