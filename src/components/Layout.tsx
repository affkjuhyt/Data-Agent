import React, { useState, useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { useAppState } from '../AppStateContext';
import { useAuth } from '../lib/AuthProvider';
import { NAV_ITEMS, ROLES, ENVIRONMENTS, MODULE_SUB_ITEMS, SubNavItem } from '../constants';
import {
  Search, Bell, LogOut, Cpu, LayoutGrid, ChevronDown, Terminal,
  Building2, Menu, X, Settings, Sparkles, ArrowRight,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { Role, Environment } from '../types';

/* ─── Command Palette ─────────────────────────────────────────────── */
const PALETTE_GROUPS = [
  { category: 'Modules', items: [
    { label: 'Executive Pulse',     module: 'dashboard' },
    { label: 'Knowledge Base',      module: 'knowledge-ops' },
    { label: 'AI Runtime',          module: 'ai-runtime' },
    { label: 'Operations Center',   module: 'operations-center' },
    { label: 'Deployment Center',   module: 'release-management' },
    { label: 'Governance & Audit',  module: 'governance' },
    { label: 'Settings',            module: 'settings' },
  ]},
  { category: 'Knowledge Base', items: [
    { label: 'Fleet Overview',      module: 'knowledge-ops', sub: 'FLEET' },
    { label: 'Register Connectors', module: 'knowledge-ops', sub: 'CONNECTORS' },
    { label: 'Knowledge Graph',     module: 'knowledge-ops', sub: 'GRAPH' },
    { label: 'Playground',          module: 'knowledge-ops', sub: 'PLAYGROUND' },
    { label: 'Conflict Manager',    module: 'knowledge-ops', sub: 'CONFLICTS' },
  ]},
  { category: 'Agents', items: [
    { label: 'Agent Registry',      module: 'ai-runtime', sub: 'AGENTS' },
    { label: 'CLI',                 module: 'ai-runtime', sub: 'CLI' },
    { label: 'Recent Traces',       module: 'ai-runtime', sub: 'TRACES' },
    { label: 'Workflow Engine',     module: 'ai-runtime', sub: 'WORKFLOWS' },
  ]},
  { category: 'Deployment', items: [
    { label: 'Deployments',    module: 'release-management', sub: 'DEPLOYMENTS' },
    { label: 'Release History', module: 'release-management', sub: 'HISTORY'     },
    { label: 'Rollback Center', module: 'release-management', sub: 'ROLLBACK'    },
  ]},
];

const CommandPalette = ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => {
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const { navigate } = useAppState();

  useEffect(() => {
    if (isOpen) {
      setTimeout(() => inputRef.current?.focus(), 40);
      setQuery('');
    }
  }, [isOpen]);

  const filtered = query.trim()
    ? PALETTE_GROUPS.map(g => ({
        ...g,
        items: g.items.filter(i => i.label.toLowerCase().includes(query.toLowerCase())),
      })).filter(g => g.items.length > 0)
    : PALETTE_GROUPS;

  const go = (item: { module: string; sub?: string }) => {
    navigate(item.module, item.sub);
    onClose();
  };

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            className="fixed inset-0 bg-black/30 backdrop-blur-[2px] z-[200]"
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.97, y: -10 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.97, y: -10 }}
            transition={{ duration: 0.14, ease: [0.4, 0, 0.2, 1] }}
            className="fixed top-[14%] left-1/2 -translate-x-1/2 w-full max-w-[600px] px-4 z-[201]"
          >
            <div className="bg-white rounded-2xl border border-[#BFA66A] shadow-[0_12px_48px_rgba(0,0,0,0.18)] overflow-hidden">
              <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[#D8CBAA]">
                <Search className="w-4 h-4 text-[#555555] shrink-0" />
                <input
                  ref={inputRef}
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  type="text"
                  placeholder="Search modules, agents, traces, workflows, knowledge..."
                  className="flex-1 bg-transparent text-[14px] text-[#111111] placeholder:text-[#777777] outline-none"
                />
                {query && (
                  <button onClick={() => setQuery('')} className="text-[#777777] hover:text-[#111111]">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
                <kbd className="px-1.5 py-0.5 text-[10px] bg-[#F4E8C3] border border-[#BFA66A] rounded-md text-[#3F3F3F] font-mono">ESC</kbd>
              </div>

              <div className="max-h-[380px] overflow-y-auto py-2">
                {filtered.length === 0 ? (
                  <div className="py-10 text-center text-sm text-[#5F5F5F]">No results for "{query}"</div>
                ) : (
                  filtered.map(group => (
                    <div key={group.category} className="mb-1">
                      <div className="px-4 py-1.5 text-[10px] font-bold text-[#5F5F5F] uppercase tracking-widest">
                        {group.category}
                      </div>
                      {group.items.map(item => (
                        <button
                          key={item.label}
                          onClick={() => go(item)}
                          className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-[#F8EBC4] text-left transition-colors group"
                        >
                          <ArrowRight className="w-3.5 h-3.5 text-[#777777] group-hover:text-[#B88719] transition-colors shrink-0" />
                          <span className="text-[13px] text-[#111111]">{item.label}</span>
                        </button>
                      ))}
                    </div>
                  ))
                )}
              </div>

              <div className="px-4 py-2.5 border-t border-[#D8CBAA] flex items-center justify-between bg-[#FCFBF7]">
                <div className="flex items-center gap-4 text-[10px] text-[#5F5F5F]">
                  <span><kbd className="font-mono mr-1">↑↓</kbd>navigate</span>
                  <span><kbd className="font-mono mr-1">↵</kbd>open</span>
                </div>
                <span className="text-[10px] text-[#777777]">AeroFlow Search</span>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

/* ─── Left Icon Rail ──────────────────────────────────────────────── */
const LeftRail = () => {
  const { role, industry, activeModule, navigate } = useAppState();

  const filtered = NAV_ITEMS.filter(item => {
    const roleMatch = item.roles.includes(role);
    const industryMatch = !item.industry || item.industry === industry;
    return roleMatch && industryMatch;
  });
  const mainItems = filtered.filter(i => i.id !== 'settings');
  const settingsItem = filtered.find(i => i.id === 'settings');

  const RailButton = ({ item }: { item: typeof NAV_ITEMS[number] }) => {
    const isActive = activeModule === item.id;
    return (
      <button
        onClick={() => navigate(item.id)}
        aria-label={item.label}
        aria-current={isActive ? 'page' : undefined}
        className={cn(
          'relative w-10 h-10 rounded-xl flex items-center justify-center transition-all group',
          isActive
            ? 'bg-[#F3E2A7] text-[#111111]'
            : 'text-[#3F3F3F] hover:bg-[#F8EBC4] hover:text-[#111111]',
        )}
      >
        <item.icon className="w-[18px] h-[18px]" />
        {isActive && (
          <motion.div
            layoutId="rail-active"
            className="absolute right-0 w-[3px] h-5 bg-[#B88719] rounded-l-full"
          />
        )}
        <span className="pointer-events-none absolute left-full ml-3 whitespace-nowrap rounded-lg bg-[#111111] px-2.5 py-1.5 text-[11px] font-medium text-white opacity-0 shadow-lg transition-opacity group-hover:opacity-100 z-50">
          {item.label}
        </span>
      </button>
    );
  };

  return (
    <div className="hidden lg:flex flex-col w-[60px] h-full bg-white border-r border-[#D6C79F] shrink-0 z-30">
      <div className="h-14 flex items-center justify-center border-b border-[#D6C79F] shrink-0">
        <div className="w-8 h-8 bg-[#111111] rounded-xl flex items-center justify-center shadow-sm">
          <Cpu className="w-4 h-4 text-[#D9B86C]" />
        </div>
      </div>

      <div className="flex-1 flex flex-col items-center gap-1 py-3 overflow-y-auto no-scrollbar">
        {mainItems.map(item => (
          <React.Fragment key={item.id}>
            <RailButton item={item} />
          </React.Fragment>
        ))}
      </div>

      {settingsItem && (
        <div className="flex flex-col items-center gap-1 py-3 border-t border-[#D6C79F] shrink-0">
          <RailButton item={settingsItem} />
        </div>
      )}
    </div>
  );
};

/* ─── Contextual hint text per module ────────────────────────────── */
const CONTEXT_HINTS: Record<string, string> = {
  'knowledge-ops':      '12 conflicts pending · Fleet healthy',
  'ai-runtime':         '482 agents active · 1.4s avg latency',
  'operations-center':  '99.9% health · 2 active alerts',
  'release-management': '3 deployments in progress',
  'governance':         '100% policy compliance · A+ score',
  'settings':           'Last updated 2 hours ago',
};

/* ─── Secondary Contextual Nav ────────────────────────────────────── */
const SecondaryNav = () => {
  const { activeModule, subTab, setSubTab, setPendingAction } = useAppState();
  const currentModule = NAV_ITEMS.find(i => i.id === activeModule);
  const subItems = MODULE_SUB_ITEMS[activeModule] ?? [];

  if (subItems.length === 0) return null;

  const activeSubId = subTab[activeModule] ?? subItems[0].id;

  return (
    <motion.div
      initial={{ width: 0, opacity: 0 }}
      animate={{ width: 232, opacity: 1 }}
      exit={{ width: 0, opacity: 0 }}
      transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
      className="hidden lg:flex flex-col h-full bg-[#FAF7EC] border-r border-[#D6C79F] shrink-0 overflow-hidden"
      style={{ width: 232 }}
    >
      <div className="h-14 flex items-center gap-2.5 px-4 border-b border-[#D6C79F] shrink-0 bg-white">
        {currentModule && (
          <>
            <currentModule.icon className="w-4 h-4 text-[#B88719] shrink-0" />
            <span className="text-[13px] font-semibold text-[#111111] truncate">{currentModule.label}</span>
          </>
        )}
      </div>

      <div className="flex-1 overflow-y-auto custom-scrollbar py-2 px-2">
        <div className="space-y-0.5">
          {subItems.map((item: SubNavItem & { action?: boolean }) => {
            const isActive = item.id === activeSubId;
            const handleClick = () => {
              if (item.action) {
                setPendingAction(item.id as any);
              } else {
                setSubTab(activeModule, item.id);
              }
            };
            return (
              <button
                key={item.id}
                onClick={handleClick}
                aria-current={isActive ? 'page' : undefined}
                className={cn('subnav-item', isActive && !item.action && 'active')}
              >
                <item.icon className="subnav-icon w-3.5 h-3.5" />
                <span className="flex-1 truncate">{item.label}</span>
                {item.badge != null && <span className="subnav-badge">{item.badge}</span>}
                {item.comingSoon && <span className="subnav-comingsoon">Soon</span>}
              </button>
            );
          })}
        </div>
      </div>

      {CONTEXT_HINTS[activeModule] && (
        <div className="p-3 border-t border-[#D6C79F] shrink-0">
          <div className="p-3 bg-white rounded-xl border border-[#D6C79F]">
            <div className="flex items-center gap-1.5 mb-1.5">
              <Sparkles className="w-3 h-3 text-[#B88719]" />
              <span className="text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wider">AI Context</span>
            </div>
            <p className="text-[12px] text-[#3F3F3F] leading-relaxed">{CONTEXT_HINTS[activeModule]}</p>
          </div>
        </div>
      )}
    </motion.div>
  );
};

/* ─── Minimal Top Bar ─────────────────────────────────────────────── */
const TopBar = ({
  onMenuClick,
  onSearchOpen,
}: {
  onMenuClick: () => void;
  onSearchOpen: () => void;
}) => {
  const { role, setRole, tenant, setTenant, user, isExpertMode, setIsExpertMode } = useAppState();
  const { logout } = useAuth();

  return (
    <header className="h-14 bg-white border-b border-[#D6C79F] px-4 lg:px-5 flex items-center justify-between shrink-0 z-20">
      <div className="flex items-center gap-2.5">
        <button
          onClick={onMenuClick}
          aria-label="Open navigation menu"
          className="lg:hidden w-9 h-9 flex items-center justify-center rounded-lg text-[#3F3F3F] hover:bg-[#F8EBC4] hover:text-[#111111] border border-transparent hover:border-[#D6C79F] transition-colors"
        >
          <Menu className="w-5 h-5" />
        </button>

        <div className="lg:hidden flex items-center gap-2">
          <div className="w-7 h-7 bg-[#111111] rounded-lg flex items-center justify-center">
            <Cpu className="w-4 h-4 text-[#D9B86C]" />
          </div>
          <span className="font-display font-semibold text-sm text-[#111111]">AeroFlow</span>
        </div>

        <button className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 bg-white hover:bg-[#FFF9E8] rounded-lg border border-[#BFA66A] transition-colors">
          <Building2 className="w-3.5 h-3.5 text-[#3F3F3F]" />
          <span className="text-xs font-medium text-[#3F3F3F]">{tenant.organization}</span>
          <span className="text-[#777777] text-xs">/</span>
          <span className="text-xs font-semibold text-[#111111]">{tenant.workspace}</span>
          <ChevronDown className="w-3 h-3 text-[#555555]" />
        </button>

        <div className="flex gap-1 overflow-x-auto no-scrollbar">
          {ENVIRONMENTS.map(env => (
            <button
              key={env.id}
              onClick={() => setTenant({ environment: env.id as Environment })}
              aria-pressed={tenant.environment === env.id}
              className={cn(
                'px-2.5 py-1 rounded-lg text-[10px] font-bold transition-all border shrink-0',
                tenant.environment === env.id
                  ? 'bg-[#F3E2A7] border-[#B88719] text-[#111111]'
                  : 'bg-white border-[#BFA66A] text-[#3F3F3F] hover:bg-[#FFF9E8] hover:text-[#111111]',
              )}
            >
              {env.id}
            </button>
          ))}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <button
          onClick={onSearchOpen}
          aria-label="Open command palette (Cmd+K)"
          className="flex items-center gap-2 px-3 py-1.5 bg-white hover:bg-[#FFF9E8] rounded-lg border border-[#BFA66A] text-[#3F3F3F] hover:text-[#111111] transition-colors"
        >
          <Search className="w-3.5 h-3.5" />
          <span className="hidden lg:inline text-xs font-medium">Search</span>
          <kbd className="hidden lg:inline px-1.5 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[10px] font-mono text-[#3F3F3F]">⌘K</kbd>
        </button>

        <button
          onClick={() => setIsExpertMode(!isExpertMode)}
          aria-pressed={isExpertMode}
          className={cn(
            'hidden sm:flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-semibold transition-all',
            isExpertMode
              ? 'bg-[#F3E2A7] border-[#B88719] text-[#111111]'
              : 'bg-white border-[#BFA66A] text-[#3F3F3F] hover:bg-[#FFF9E8] hover:text-[#111111]',
          )}
        >
          <Terminal className="w-3.5 h-3.5" />
          <span className="hidden xl:inline">Expert</span>
        </button>

        <div className="hidden md:flex items-center gap-0.5 p-0.5 bg-[#F4E8C3] rounded-lg border border-[#BFA66A]">
          {ROLES.map(r => (
            <button
              key={r.id}
              onClick={() => setRole(r.id as Role)}
              title={r.label}
              aria-pressed={role === r.id}
              className={cn(
                'px-2 py-1 rounded-md text-[10px] font-bold transition-all',
                role === r.id
                  ? 'bg-white text-[#111111] shadow-sm border border-[#BFA66A]'
                  : 'text-[#3F3F3F] hover:text-[#111111] border border-transparent',
              )}
            >
              {r.id.replace('_', ' ').split(' ').map((w: string) => w[0]).join('')}
            </button>
          ))}
        </div>

        <button aria-label="Notifications" className="relative w-9 h-9 flex items-center justify-center text-[#3F3F3F] hover:text-[#111111] hover:bg-[#F8EBC4] rounded-lg transition-colors border border-transparent hover:border-[#D6C79F]">
          <Bell className="w-4 h-4" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 bg-[#9F1D1D] border border-white rounded-full" />
        </button>

        <div className="flex items-center gap-2 pl-2 border-l border-[#D6C79F]">
          <div className="hidden sm:block text-right">
            <div className="text-[12px] font-semibold text-[#111111] leading-none">{user.name}</div>
            <div className="text-[10px] text-[#5F5F5F] mt-0.5">{role.replace(/_/g, ' ')}</div>
          </div>
          <div className="w-8 h-8 rounded-full bg-[#111111] flex items-center justify-center text-[#D9B86C] text-[12px] font-bold shrink-0 border border-[#B88719]">
            {user.name?.[0] ?? 'A'}
          </div>
          <button
            onClick={logout}
            title="Sign out"
            className="w-8 h-8 flex items-center justify-center rounded-lg text-[#3F3F3F] hover:text-[#9F1D1D] hover:bg-[#FEE2E2] border border-transparent hover:border-[#FCA5A5] transition-colors"
          >
            <LogOut className="w-4 h-4" />
          </button>
        </div>
      </div>
    </header>
  );
};

/* ─── Mobile Full-Screen Drawer ───────────────────────────────────── */
const MobileDrawer = ({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) => {
  const { role, industry, user, activeModule, navigate, subTab, setSubTab } = useAppState();
  const { logout } = useAuth();

  const filtered = NAV_ITEMS.filter(item => {
    const roleMatch = item.roles.includes(role);
    const industryMatch = !item.industry || item.industry === industry;
    return roleMatch && industryMatch;
  });

  const subItems = MODULE_SUB_ITEMS[activeModule] ?? [];
  const activeSubId = subTab[activeModule];

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            onClick={onClose}
            className="lg:hidden fixed inset-0 bg-black/40 backdrop-blur-sm z-[110]"
          />
          <motion.div
            initial={{ x: '-100%' }}
            animate={{ x: 0 }}
            exit={{ x: '-100%' }}
            transition={{ type: 'spring', damping: 28, stiffness: 220 }}
            className="lg:hidden fixed inset-y-0 left-0 w-80 bg-white border-r border-[#D6C79F] z-[120] flex flex-col shadow-2xl"
          >
            <div className="h-14 flex items-center justify-between px-4 border-b border-[#D6C79F]">
              <div className="flex items-center gap-2.5">
                <div className="w-7 h-7 bg-[#111111] rounded-xl flex items-center justify-center">
                  <Cpu className="w-4 h-4 text-[#D9B86C]" />
                </div>
                <div>
                  <div className="font-display font-semibold text-sm text-[#111111] leading-none">AeroFlow</div>
                  <div className="text-[10px] text-[#5F5F5F] uppercase tracking-wider mt-0.5">Enterprise OS</div>
                </div>
              </div>
              <button
                onClick={onClose}
                aria-label="Close menu"
                className="w-8 h-8 flex items-center justify-center text-[#3F3F3F] hover:text-[#111111] hover:bg-[#F8EBC4] rounded-lg transition-colors border border-transparent hover:border-[#D6C79F]"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto py-3 px-3 space-y-3">
              {/* Modules */}
              <div>
                <div className="px-2 py-1 text-[10px] font-bold text-[#5F5F5F] uppercase tracking-widest">Modules</div>
                <div className="space-y-0.5">
                  {filtered.map(item => (
                    <button
                      key={item.id}
                      onClick={() => { navigate(item.id); onClose(); }}
                      aria-current={activeModule === item.id ? 'page' : undefined}
                      className={cn(
                        'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-[14px] font-medium transition-all border',
                        activeModule === item.id
                          ? 'bg-[#F3E2A7] text-[#111111] border-[#D9B86C]'
                          : 'text-[#3F3F3F] hover:bg-[#F8EBC4] hover:text-[#111111] border-transparent',
                      )}
                    >
                      <item.icon className={cn('w-4 h-4', activeModule === item.id ? 'text-[#B88719]' : 'text-[#555555]')} />
                      {item.label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Sub-items for current module */}
              {subItems.length > 0 && (
                <div>
                  <div className="px-2 py-1 text-[10px] font-bold text-[#5F5F5F] uppercase tracking-widest">In this module</div>
                  <div className="space-y-0.5">
                    {subItems.map(item => (
                      <button
                        key={item.id}
                        onClick={() => { setSubTab(activeModule, item.id); onClose(); }}
                        className={cn('subnav-item', item.id === activeSubId && 'active')}
                      >
                        <item.icon className="subnav-icon w-3.5 h-3.5" />
                        <span className="flex-1 truncate">{item.label}</span>
                        {item.badge != null && <span className="subnav-badge">{item.badge}</span>}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="p-4 border-t border-[#D6C79F] bg-[#FCFBF7]">
              <div className="flex items-center gap-3 px-2 py-2 mb-1">
                <div className="w-9 h-9 rounded-full bg-[#111111] flex items-center justify-center text-[#D9B86C] text-sm font-bold shrink-0">
                  {user.name?.[0] ?? 'A'}
                </div>
                <div>
                  <div className="text-[13px] font-semibold text-[#111111]">{user.name}</div>
                  <div className="text-[10px] text-[#5F5F5F]">{role.replace(/_/g, ' ')}</div>
                </div>
              </div>
              <button
                onClick={logout}
                className="w-full flex items-center gap-2 px-3 py-2 text-[#3F3F3F] hover:text-[#111111] hover:bg-[#F8EBC4] rounded-xl text-xs font-medium transition-colors border border-transparent hover:border-[#D6C79F]"
              >
                <LogOut className="w-3.5 h-3.5" />
                Sign out
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
};

/* ─── Mobile Bottom Nav ───────────────────────────────────────────── */
const BottomNav = () => {
  const { activeModule, navigate } = useAppState();

  const items = [
    { id: 'dashboard',     label: 'Home',     icon: NAV_ITEMS.find(n => n.id === 'dashboard')?.icon ?? LayoutGrid },
    { id: 'knowledge-ops', label: 'Knowledge', icon: NAV_ITEMS.find(n => n.id === 'knowledge-ops')?.icon ?? LayoutGrid },
    { id: 'ai-runtime',    label: 'AI',       icon: NAV_ITEMS.find(n => n.id === 'ai-runtime')?.icon ?? LayoutGrid },
    { id: 'governance',    label: 'Govern',   icon: NAV_ITEMS.find(n => n.id === 'governance')?.icon ?? LayoutGrid },
    { id: 'settings',      label: 'Settings', icon: Settings },
  ];

  return (
    <div className="lg:hidden fixed bottom-0 left-0 right-0 h-16 bg-white border-t border-[#D6C79F] flex items-center justify-around px-2 z-[100]">
      {items.map(item => {
        const isActive = activeModule === item.id;
        return (
          <button
            key={item.id}
            onClick={() => navigate(item.id)}
            aria-current={isActive ? 'page' : undefined}
            className={cn(
              'flex flex-col items-center gap-0.5 transition-all px-2 py-1 rounded-xl',
              isActive ? 'text-[#111111]' : 'text-[#3F3F3F]',
            )}
          >
            <div className={cn(
              'w-9 h-9 flex items-center justify-center rounded-xl transition-all',
              isActive ? 'bg-[#F3E2A7]' : '',
            )}>
              <item.icon className={cn('w-[18px] h-[18px]', isActive ? 'text-[#B88719]' : 'text-[#555555]')} />
            </div>
            <span className={cn('text-[9px]', isActive ? 'font-bold' : 'font-medium')}>{item.label}</span>
          </button>
        );
      })}
    </div>
  );
};

/* ─── AppLayout ───────────────────────────────────────────────────── */
export const AppLayout = ({
  children,
  activeTab,
}: {
  children: React.ReactNode;
  activeTab: string;
  setActiveTab: (id: string) => void;
}) => {
  const [isMobileDrawerOpen, setIsMobileDrawerOpen] = useState(false);
  const [isCommandOpen, setIsCommandOpen]           = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        setIsCommandOpen(prev => !prev);
      }
      if (e.key === 'Escape') setIsCommandOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);

  const hasSecondaryNav = (MODULE_SUB_ITEMS[activeTab]?.length ?? 0) > 0;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#FCFBF7] text-[#111111]">
      <LeftRail />

      <AnimatePresence mode="wait">
        {hasSecondaryNav && (
          <React.Fragment key={activeTab}>
            <SecondaryNav />
          </React.Fragment>
        )}
      </AnimatePresence>

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <TopBar
          onMenuClick={() => setIsMobileDrawerOpen(true)}
          onSearchOpen={() => setIsCommandOpen(true)}
        />
        <main className="flex-1 overflow-y-auto custom-scrollbar pb-20 lg:pb-10 p-4 lg:p-8 bg-[#FCFBF7]">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.18, ease: [0.4, 0, 0.2, 1] }}
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>

      <MobileDrawer isOpen={isMobileDrawerOpen} onClose={() => setIsMobileDrawerOpen(false)} />
      <BottomNav />
      <CommandPalette isOpen={isCommandOpen} onClose={() => setIsCommandOpen(false)} />
    </div>
  );
};
