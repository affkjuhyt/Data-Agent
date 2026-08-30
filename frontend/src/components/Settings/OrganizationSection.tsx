import React from 'react';
import { 
  Building2, 
  Palette, 
  Globe, 
  Layout, 
  Upload,
  ChevronRight,
  Shield,
  Layers,
  MapPin,
  ExternalLink,
  Zap
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { cn } from '../../lib/utils';

export const OrganizationSection = () => {
  return (
    <div className="space-y-10 lg:space-y-12 pb-20 no-scrollbar">
      <OperationalHeader 
        title="Organization & Brand"
        subtitle="Global platform identity, tenant hierarchy, and regional sovereignty."
        breadcrumbs={[{ label: 'Settings' }, { label: 'General' }, { label: 'Organization' }]}
        status={<StatusBadge status="ACTIVE" size="lg" />}
        actions={
          <button className="w-full lg:w-auto flex items-center justify-center gap-2 bg-white/5 hover:bg-white/10 text-white px-5 py-2.5 rounded-2xl text-xs font-bold border border-white/5 transition-all active:scale-95">
            <Globe className="w-4 h-4" />
            PLATFORM MANIFEST
          </button>
        }
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
        <div className="xl:col-span-2 space-y-8 lg:space-y-10">
          {/* Brand Identity */}
          <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
            <div className="flex items-center gap-3 mb-8 lg:mb-10">
              <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                <Palette className="w-6 h-6" />
              </div>
              <div>
                <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Platform Branding</h3>
                <p className="text-xs lg:text-sm text-slate-500 uppercase font-black tracking-widest mt-1">White-label & Visual Soul</p>
              </div>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-8 lg:gap-12">
               <div className="space-y-6">
                  <div>
                    <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest block mb-4">Platform Name</label>
                    <input 
                      type="text" 
                      defaultValue="NEURAL ARCH - G7 ENTERPRISE" 
                      className="w-full bg-white/5 border border-white/10 rounded-2xl px-5 py-4 text-sm font-bold text-white focus:outline-none focus:border-brand-500/50 uppercase italic"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] font-black text-slate-600 uppercase tracking-widest block mb-4">System Domain</label>
                    <div className="flex">
                      <div className="bg-white/5 border border-white/10 border-r-0 rounded-l-2xl px-5 py-4 text-xs font-mono text-slate-500 flex items-center">https://</div>
                      <input 
                        type="text" 
                        defaultValue="platform.neuralarch.io" 
                        className="flex-1 bg-white/5 border border-white/10 rounded-r-2xl px-5 py-4 text-sm font-bold text-white focus:outline-none focus:border-brand-500/50"
                      />
                    </div>
                  </div>
               </div>

               <div className="space-y-8">
                  <div className="flex items-center gap-6">
                    <div className="w-20 h-20 rounded-3xl bg-brand-500/10 border-2 border-dashed border-brand-500/30 flex items-center justify-center group cursor-pointer hover:bg-brand-500/20 transition-all">
                       <Upload className="w-6 h-6 text-brand-400 group-hover:scale-110 transition-transform" />
                    </div>
                    <div>
                       <h4 className="text-sm font-bold text-white mb-1">Corporate Logo</h4>
                       <p className="text-[10px] text-slate-500 uppercase font-black tracking-tighter">SVG / PNG (512x512)</p>
                       <button className="text-[10px] font-black text-brand-400 mt-2 hover:underline uppercase tracking-widest">Update Asset</button>
                    </div>
                  </div>
                  <div className="flex items-center gap-4">
                     <div className="w-8 h-8 rounded-full bg-brand-500 shadow-lg shadow-brand-500/20" />
                     <div className="w-8 h-8 rounded-full bg-slate-900 border border-white/10" />
                     <div className="w-8 h-8 rounded-full bg-indigo-500" />
                     <div className="w-8 h-8 rounded-full bg-white/5 border-2 border-dashed border-white/20 flex items-center justify-center text-slate-500">
                        <Plus size={12} />
                     </div>
                     <span className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-2">Theme Palette</span>
                  </div>
               </div>
            </div>
          </section>

          {/* Workspace Hierarchy */}
          <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
            <div className="flex items-center justify-between mb-8 lg:mb-10">
              <div className="flex items-center gap-3">
                <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-400">
                  <Layers className="w-6 h-6" />
                </div>
                <div>
                  <h3 className="text-xl lg:text-2xl font-bold text-white tracking-tight italic">Tenant Workspace Hierarchy</h3>
                  <p className="text-[10px] lg:text-xs text-slate-500 uppercase font-black tracking-widest mt-1">Multi-tenant Isolation Matrix</p>
                </div>
              </div>
              <button className="px-4 py-2 bg-brand-500 text-white rounded-xl text-[10px] font-black uppercase tracking-widest active:scale-95 transition-all">New Workspace</button>
            </div>

            <div className="space-y-4">
               {[
                  { name: 'Core Infrastructure', id: 'WS-001', projects: '24', users: '128', icon: Shield, color: 'text-brand-400' },
                  { name: 'Financial Services', id: 'WS-002', projects: '12', users: '45', icon: Building2, color: 'text-emerald-400' },
                  { name: 'R&D Lab / Future Ops', id: 'WS-042', projects: '8', users: '12', icon: Zap, color: 'text-amber-400' },
               ].map((ws, i) => (
                  <div key={ws.id} className="flex items-center justify-between p-5 lg:p-6 bg-white/[0.03] border border-white/5 rounded-3xl hover:bg-white/[0.06] transition-all group cursor-pointer">
                    <div className="flex items-center gap-5">
                       <div className={cn("p-3 rounded-2xl bg-white/5 border border-white/5", ws.color)}>
                          <ws.icon className="w-6 h-6" />
                       </div>
                       <div>
                          <h4 className="text-base lg:text-lg font-bold text-white tracking-tight">{ws.name}</h4>
                          <div className="flex items-center gap-3 mt-1">
                             <span className="text-[10px] font-mono text-slate-600 tracking-tighter">ID: {ws.id}</span>
                             <div className="w-1 h-1 rounded-full bg-slate-800" />
                             <span className="text-[10px] font-black text-slate-500 uppercase tracking-[0.1em]">{ws.projects} Projects</span>
                             <div className="w-1 h-1 rounded-full bg-slate-800" />
                             <span className="text-[10px] font-black text-slate-500 uppercase tracking-[0.1em]">{ws.users} Members</span>
                          </div>
                       </div>
                    </div>
                    <div className="flex items-center gap-4">
                       <button className="hidden sm:block p-2 text-slate-600 hover:text-white transition-colors"><ExternalLink className="w-4 h-4" /></button>
                       <ChevronRight className="w-5 h-5 text-slate-800 group-hover:text-brand-400 group-hover:translate-x-1 transition-all" />
                    </div>
                  </div>
               ))}
            </div>
          </section>
        </div>

        <div className="space-y-8 lg:space-y-10">
           {/* Regional Sovereignty */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-[#02040d]">
              <div className="flex items-center gap-3 mb-8 lg:mb-10">
                <div className="p-2 bg-emerald-500/10 rounded-xl border border-emerald-500/20 text-emerald-400">
                  <MapPin className="w-5 h-5" />
                </div>
                <h3 className="text-lg lg:text-xl font-bold text-white tracking-tight italic">Platform Locality</h3>
              </div>

              <div className="space-y-6">
                 <div>
                    <label className="text-[9px] font-black text-slate-600 uppercase tracking-widest block mb-3">Primary Host Region</label>
                    <div className="flex items-center justify-between p-4 bg-white/5 border border-white/10 rounded-2xl">
                       <div className="flex items-center gap-3">
                          <Globe className="w-4 h-4 text-brand-400 opacity-60" />
                          <span className="text-xs font-bold text-white">US-EAST (Virginia)</span>
                       </div>
                       <StatusBadge status="ACTIVE" />
                    </div>
                 </div>

                 <div>
                    <label className="text-[9px] font-black text-slate-600 uppercase tracking-widest block mb-3">Timezone Context</label>
                    <select className="w-full bg-white/5 border border-white/10 rounded-2xl px-5 py-3.5 text-xs font-bold text-white focus:outline-none appearance-none">
                       <option>UTC (Coordinated Universal Time)</option>
                       <option>EST (Eastern Standard Time)</option>
                       <option>PST (Pacific Standard Time)</option>
                    </select>
                 </div>

                 <div className="pt-6 border-t border-white/5 space-y-4">
                    <div className="flex items-center justify-between">
                       <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">Failover Backup</span>
                       <StatusBadge status="STABLE" />
                    </div>
                    <div className="flex items-center justify-between">
                       <span className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">DR Capability</span>
                       <span className="text-[10px] font-mono text-emerald-500">OPTIMAL</span>
                    </div>
                 </div>
              </div>
           </section>

           {/* Legal & Compliance Meta */}
           <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem] border-white/5 bg-white/[0.01]">
              <h3 className="text-[11px] font-black text-slate-400 uppercase tracking-[0.3em] mb-8 lg:mb-10 flex items-center gap-3">
                <Shield className="w-4 h-4 text-brand-400" />
                Governance Core
              </h3>
              
              <div className="space-y-6">
                 {[
                    { label: 'Privacy Policy URI', icon: Layout },
                    { label: 'Terms of Service URI', icon: Layout },
                    { label: 'AI Ethics Framework', icon: Shield },
                 ].map(item => (
                    <div key={item.label}>
                       <label className="text-[9px] font-black text-slate-600 uppercase tracking-widest block mb-3">{item.label}</label>
                       <div className="flex gap-2">
                          <input 
                            type="text" 
                            placeholder="https://..." 
                            className="flex-1 bg-white/5 border border-white/10 rounded-xl px-4 py-2.5 text-[10px] font-bold text-slate-300 focus:outline-none"
                          />
                          <button className="p-2.5 bg-white/5 border border-white/10 rounded-xl hover:text-brand-400 transition-colors">
                             <ExternalLink className="w-4 h-4" />
                          </button>
                       </div>
                    </div>
                 ))}
              </div>

              <button className="w-full mt-10 py-4 bg-white/5 border border-white/10 hover:border-brand-500/50 rounded-2xl text-[10px] font-black uppercase tracking-widest text-slate-400 hover:text-white transition-all">
                 Apply Platform Manifest
              </button>
           </section>
        </div>
      </div>
    </div>
  );
};

const Plus = ({ size = 24 }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="12" y1="5" x2="12" y2="19"></line>
    <line x1="5" y1="12" x2="19" y2="12"></line>
  </svg>
);
