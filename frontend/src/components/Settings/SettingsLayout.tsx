import React, { useState } from 'react';
import { 
  Settings as SettingsIcon, 
  Building2, 
  Users, 
  ShieldCheck, 
  Cpu, 
  Database, 
  Lock, 
  Bell, 
  CreditCard, 
  Code, 
  History, 
  Flag, 
  Save,
  Globe,
  Monitor,
  Zap,
  Key,
  Eye,
  Menu,
  X
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../lib/utils';

export type SettingsSection = 
  | 'OVERVIEW' 
  | 'ORGANIZATION' 
  | 'IAM' 
  | 'AUTH' 
  | 'AI_PROVIDERS' 
  | 'INFRASTRUCTURE' 
  | 'STORAGE' 
  | 'SECRETS' 
  | 'SECURITY' 
  | 'BILLING' 
  | 'API' 
  | 'ADVANCED';

interface NavItem {
  id: SettingsSection;
  label: string;
  icon: any;
  category: 'GENERAL' | 'SECURITY' | 'PLATFORM' | 'OPERATIONS';
}

const SETTINGS_NAV: NavItem[] = [
  { id: 'OVERVIEW', label: 'Overview', icon: Monitor, category: 'GENERAL' },
  { id: 'ORGANIZATION', label: 'Organization & Brand', icon: Building2, category: 'GENERAL' },
  
  { id: 'IAM', label: 'Users & Permissions', icon: Users, category: 'SECURITY' },
  { id: 'AUTH', label: 'Auth & SSO', icon: Lock, category: 'SECURITY' },
  { id: 'SECRETS', label: 'Secrets Vault', icon: Key, category: 'SECURITY' },
  { id: 'SECURITY', label: 'Security & Compliance', icon: ShieldCheck, category: 'SECURITY' },

  { id: 'AI_PROVIDERS', label: 'AI Providers & Models', icon: Cpu, category: 'PLATFORM' },
  { id: 'INFRASTRUCTURE', label: 'Runtime Infrastructure', icon: Zap, category: 'PLATFORM' },
  { id: 'STORAGE', label: 'Storage & Retention', icon: Database, category: 'PLATFORM' },
  
  { id: 'BILLING', label: 'Billing & Quotas', icon: CreditCard, category: 'OPERATIONS' },
  { id: 'API', label: 'API & Developers', icon: Code, category: 'OPERATIONS' },
  { id: 'ADVANCED', label: 'Advanced Platform', icon: SettingsIcon, category: 'OPERATIONS' },
];

export const SettingsLayout = ({ children, activeSection, onSectionChange }: { 
  children: React.ReactNode, 
  activeSection: SettingsSection, 
  onSectionChange: (section: SettingsSection) => void 
}) => {
  const [isSidebarOpen, setIsSidebarOpen] = useState(false);

  return (
    <div className="flex flex-col lg:flex-row bg-[#050505] rounded-[2rem] lg:rounded-3xl border border-white/5 h-[calc(100vh-140px)] lg:h-[calc(100vh-160px)] overflow-hidden shadow-2xl relative">
      {/* Mobile Settings Toggle */}
      <div className="lg:hidden flex items-center justify-between p-4 border-b border-white/5 bg-white/[0.02]">
        <div className="flex items-center gap-2">
           <SettingsIcon className="w-5 h-5 text-brand-400" />
           <span className="font-bold text-white uppercase text-xs tracking-tight">Settings / {SETTINGS_NAV.find(n => n.id === activeSection)?.label}</span>
        </div>
        <button 
          onClick={() => setIsSidebarOpen(!isSidebarOpen)}
          className="p-2 bg-white/5 rounded-xl border border-white/5 text-slate-400"
        >
          {isSidebarOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Settings Sidebar */}
      <aside className={cn(
        "absolute inset-0 z-40 lg:relative lg:inset-auto w-full lg:w-72 bg-[#0a0a0a] lg:bg-white/[0.02] border-r border-white/5 flex flex-col pt-6 transition-transform duration-300 lg:translate-x-0",
        isSidebarOpen ? "translate-x-0" : "-translate-x-full"
      )}>
        <div className="px-6 mb-8 flex justify-between items-center">
          <div>
            <h2 className="text-xl font-bold text-white tracking-tight uppercase bg-brand-500/10 inline-block px-3 py-1 rounded-lg border border-brand-500/20">
              Control Center
            </h2>
            <p className="text-[10px] text-slate-500 font-black mt-2 uppercase tracking-widest opacity-60">
              Platform Governance & Config
            </p>
          </div>
          <button className="lg:hidden p-2 text-slate-500" onClick={() => setIsSidebarOpen(false)}>
            <X className="w-6 h-6" />
          </button>
        </div>

        <nav className="flex-1 px-3 space-y-8 overflow-y-auto custom-scrollbar pb-10">
          {(['GENERAL', 'SECURITY', 'PLATFORM', 'OPERATIONS'] as const).map(category => (
            <div key={category} className="space-y-1">
              <h3 className="px-4 text-[9px] font-black text-slate-600 uppercase tracking-[0.2em] mb-2">{category}</h3>
              {SETTINGS_NAV.filter(item => item.category === category).map(item => (
                <button
                  key={item.id}
                  onClick={() => {
                    onSectionChange(item.id);
                    setIsSidebarOpen(false);
                  }}
                  className={cn(
                    "w-full flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-medium transition-all group",
                    activeSection === item.id 
                      ? "bg-brand-500/10 text-brand-400 border border-brand-500/20 shadow-lg" 
                      : "text-slate-400 hover:bg-white/5 hover:text-white"
                  )}
                >
                  <item.icon className={cn("w-4 h-4", activeSection === item.id ? "text-brand-400" : "text-slate-500 group-hover:text-slate-300")} />
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </nav>

        <div className="p-6 border-t border-white/5 bg-white/[0.01]">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            <span className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">Global Heartbeat</span>
          </div>
          <div className="text-[9px] text-slate-600 font-mono">
            NODE: US-EAST-PLAT-01
            <br />
            VSN: 4.12.0-STABLE
          </div>
        </div>
      </aside>

      {/* Settings Content Area */}
      <main className="flex-1 flex flex-col bg-slate-950 overflow-hidden">
        <div className="p-6 lg:p-10 h-full overflow-y-auto no-scrollbar scroll-smooth">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeSection}
              initial={{ opacity: 0, x: 10 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -10 }}
              transition={{ duration: 0.2 }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
    </div>
  );
};
