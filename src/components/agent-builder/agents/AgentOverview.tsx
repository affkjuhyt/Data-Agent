import React, { useState, useEffect, useCallback } from 'react';
import {
  Bot, Cpu, Plus, Terminal, Search, Filter, Zap,
  MoreHorizontal, Database, Network, Wrench, GitBranch,
  ShieldAlert, Activity, BarChart3, Copy, Trash2, Settings2, X, CheckCircle2,
} from 'lucide-react';
import ReactDOM from 'react-dom';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { AgentType, AgentStatus } from '../../../types/agent';
import { MOCK_AGENTS } from '../../../constants/agentMock';
import { FLOW_BUILDER_URL } from '../../../lib/config';

interface ApiAgent { id: string; name: string; description: string; published_version_id: string | null; draft_version_id: string | null; created_at: string; }

interface OverviewProps {
  onNewAgent: () => void;
  onOpenCLI: () => void;
  onOpenProvision: () => void;
  onSelectAgent: (id: string) => void;
}

export const AgentRegistryOverview: React.FC<OverviewProps> = ({
  onNewAgent,
  onOpenCLI,
  onOpenProvision,
  onSelectAgent,
}) => {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });
  const [cloneTarget, setCloneTarget] = useState<{ id: string; name: string } | null>(null);
  const [cloneModalName, setCloneModalName] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [apiAgents, setApiAgents] = useState<ApiAgent[]>([]);

  useEffect(() => {
    fetch(`${FLOW_BUILDER_URL}/api/agents`)
      .then(r => r.json())
      .then((d: ApiAgent[]) => setApiAgents(Array.isArray(d) ? d : []))
      .catch(() => {});
  }, []);

  const handleMenuOpen = useCallback((e: React.MouseEvent<HTMLButtonElement>, id: string) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setMenuPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
    setOpenMenuId(id);
  }, []);

  const handleDelete = async () => {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      const res = await fetch(`${FLOW_BUILDER_URL}/api/agents/${deleteTarget.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`${res.status}`);
      setApiAgents(prev => prev.filter(a => a.id !== deleteTarget.id));
      setToast(`Đã xóa agent "${deleteTarget.name}"`);
    } catch {
      setToast('Xóa thất bại');
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  useEffect(() => {
    if (!openMenuId) return;
    const close = () => setOpenMenuId(null);
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [openMenuId]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 3000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    if (!cloneTarget) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setCloneTarget(null); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [cloneTarget]);

  const stats = {
    total:     apiAgents.length,
    active:    apiAgents.filter(a => a.published_version_id).length,
    failed:    0,
    totalCost: '--',
  };

  const getStatusStyle = (status: AgentStatus) => {
    switch (status) {
      case 'ACTIVE':           return 'bg-emerald-100 text-emerald-700 border-emerald-200';
      case 'BUSY':             return 'bg-amber-100 text-amber-700 border-amber-200';
      case 'FAILED':           return 'bg-red-100 text-red-700 border-red-200';
      case 'IDLE':             return 'bg-gray-100 text-gray-500 border-gray-200';
      case 'WAITING_APPROVAL': return 'bg-purple-100 text-purple-700 border-purple-200';
      default:                 return 'bg-gray-50 text-gray-400 border-gray-100';
    }
  };

  const getTypeIcon = (type: AgentType) => {
    switch (type) {
      case 'RAG':      return <Database className="w-4 h-4" />;
      case 'GRAPHRAG': return <Network className="w-4 h-4" />;
      case 'TOOL_USE': return <Wrench className="w-4 h-4" />;
      case 'WORKFLOW': return <GitBranch className="w-4 h-4" />;
      default:         return <Bot className="w-4 h-4" />;
    }
  };

  return (
    <>
    <div className="space-y-5">
      {/* Section header */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-[#B88719] text-[10px] font-bold font-mono tracking-widest uppercase mb-1.5">
            <Cpu className="w-3.5 h-3.5" />
            Control Plane / Registry
          </div>
          <h2 className="text-xl font-display font-bold text-[#111111]">Agent Inventory</h2>
        </div>
        <div className="flex gap-2">
          <button onClick={onOpenCLI} className="btn-secondary text-[10px]">
            <Terminal className="w-3.5 h-3.5" /> CLI Tooling
          </button>
          <button onClick={onNewAgent} className="btn-primary">
            <Plus className="w-4 h-4" /> New Agent
          </button>
        </div>
      </div>

      {/* Stat cards */}
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-3">
        {[
          { label: 'Total Agents',     val: stats.total,          icon: Bot,        color: 'text-[#B88719]' },
          { label: 'Active Runtimes',  val: stats.active,         icon: Activity,   color: 'text-emerald-600' },
          { label: 'Critical Failures',val: stats.failed,         icon: ShieldAlert,color: 'text-red-600' },
          { label: 'Platform Health',  val: '94%',                icon: Zap,        color: 'text-amber-600' },
          { label: 'Daily Accrual',    val: `$${stats.totalCost}`, icon: BarChart3,  color: 'text-purple-600', span: true },
        ].map((s) => (
          <div key={s.label} className={cn('warm-card rounded-xl p-4', s.span && 'col-span-2 lg:col-span-1')}>
            <div className="flex justify-between items-start mb-3">
              <s.icon className={cn('w-5 h-5', s.color)} />
              <span className="text-[8px] font-bold text-[#8A8A7A] uppercase tracking-widest">Live</span>
            </div>
            <div className="text-2xl font-display font-bold text-[#111111]">{s.val}</div>
            <div className="text-[9px] text-[#5A5A5A] mt-1 font-semibold uppercase tracking-wide">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Table container */}
      <div className="warm-panel rounded-xl overflow-hidden">
        {/* Toolbar */}
        <div className="px-5 py-3.5 border-b border-[#BFA66A]/40 bg-[#FFF9E8] flex flex-col lg:flex-row lg:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#8A8A7A]" />
              <input
                type="text"
                placeholder="Query agents..."
                className="w-56 bg-white border border-[#BFA66A]/60 rounded-lg pl-9 pr-3 py-1.5 text-xs text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#8A5A00]"
              />
            </div>
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#BFA66A]/60 rounded-lg text-[10px] font-semibold text-[#5A5A5A] hover:border-[#8A5A00] transition-colors">
              <Filter className="w-3 h-3" /> Filter Environments
            </button>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={onOpenProvision} className="btn-secondary text-[10px] py-1.5">
              <Zap className="w-3.5 h-3.5" /> Quick Provision
            </button>
            <button className="p-1.5 bg-white border border-[#BFA66A]/60 rounded-lg hover:border-[#8A5A00] transition-colors">
              <MoreHorizontal className="w-4 h-4 text-[#5A5A5A]" />
            </button>
          </div>
        </div>

        {/* Desktop table */}
        <div className="hidden lg:block">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b border-[#BFA66A]/30 bg-white">
                {['Agent', 'Environment', 'KB', 'Latency / Health', 'Error Rate', 'Status', ''].map((h, i) => (
                  <th key={h} className={cn('px-5 py-3 text-[10px] font-bold text-[#5A5A5A] uppercase tracking-widest', i === 6 && 'text-right')}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#BFA66A]/15">
              {apiAgents.length === 0 && (
                <tr><td colSpan={7} className="px-5 py-10 text-center text-sm text-[#8A8A7A]">
                  {'Chưa có agent nào. Bấm + New Agent để tạo.'}
                </td></tr>
              )}
              {apiAgents.map(agent => (
                <tr
                  key={agent.id}
                  onClick={() => onSelectAgent(agent.id)}
                  className="hover:bg-[#FFF9E8] transition-colors cursor-pointer group"
                >
                  <td className="px-5 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl flex items-center justify-center border shrink-0 bg-blue-50 border-blue-200 text-blue-600">
                        <Bot className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="font-semibold text-[#111111] text-sm leading-snug">{agent.name}</div>
                        <div className="text-[10px] font-mono text-[#B88719] mt-0.5">{agent.id.slice(0, 8)}...</div>
                        <div className="text-[10px] text-[#8A8A7A] italic mt-0.5 truncate max-w-[180px]">{agent.description || '--'}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-5 py-4">
                    <div className="text-sm font-semibold text-[#2A2A2A] leading-snug">Dev</div>
                    <div className="inline-block mt-1 px-2 py-0.5 text-[9px] font-bold tracking-widest uppercase rounded bg-[#F3E2A7] text-[#7C5A0E] border border-[#BFA66A]/40">LOCAL</div>
                  </td>
                  <td className="px-5 py-4"><span className="text-[#8A8A7A] italic text-[11px]">--</span></td>
                  <td className="px-5 py-4"><span className="text-[#8A8A7A] italic text-[11px]">--</span></td>
                  <td className="px-5 py-4"><span className="text-[#8A8A7A] italic text-[11px]">--</span></td>
                  <td className="px-5 py-4">
                    <span className={cn(
                      'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[9px] font-bold uppercase tracking-wide border',
                      agent.published_version_id
                        ? 'bg-emerald-100 text-emerald-700 border-emerald-200'
                        : 'bg-gray-100 text-gray-500 border-gray-200',
                    )}>
                      {agent.published_version_id && <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0" />}
                      {agent.published_version_id ? 'Published' : 'Draft'}
                    </span>
                  </td>
                  <td className="px-5 py-4 text-right">
                    <button
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); handleMenuOpen(e, agent.id); }}
                      className="p-1.5 hover:bg-[#F3E2A7] rounded-lg transition-colors text-[#C8BCA8] hover:text-[#5A5A5A]"
                    >
                      <MoreHorizontal className="w-4 h-4" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Portal dropdown */}
        {openMenuId && ReactDOM.createPortal(
          <div
            onMouseDown={(e) => e.stopPropagation()}
            style={{ position: 'fixed', top: menuPos.top, right: menuPos.right, zIndex: 9999 }}
            className="w-44 bg-white border border-[#BFA66A]/50 rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-100"
          >
            {apiAgents.filter(a => a.id === openMenuId).map(agent => (
              <React.Fragment key={agent.id}>
                <button
                  onClick={() => setOpenMenuId(null)}
                  className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-semibold text-[#2A2A2A] hover:bg-[#FFF9E8] transition-colors"
                >
                  <Settings2 className="w-3.5 h-3.5 text-[#8A8A7A]" /> Edit
                </button>
                <button
                  onClick={() => { setOpenMenuId(null); setCloneModalName(`Copy of ${agent.name}`); setCloneTarget({ id: agent.id, name: agent.name }); }}
                  className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-semibold text-[#2A2A2A] hover:bg-[#FFF9E8] transition-colors"
                >
                  <Copy className="w-3.5 h-3.5 text-[#8A8A7A]" /> Clone
                </button>
                <div className="h-px bg-[#ECE7DA] mx-3" />
                <button
                  onClick={() => { setOpenMenuId(null); setDeleteTarget({ id: agent.id, name: agent.name }); }}
                  className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-semibold text-red-600 hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" /> Delete
                </button>
              </React.Fragment>
            ))}
          </div>,
          document.body
        )}

        {/* Mobile/Tablet card list */}
        <div className="lg:hidden divide-y divide-[#BFA66A]/20">
          {MOCK_AGENTS.map(agent => (
            <div
              key={agent.id}
              onClick={() => onSelectAgent(agent.id)}
              className="p-5 hover:bg-[#FFF9E8] transition-colors cursor-pointer space-y-3"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <div className={cn('p-2 rounded-xl border', getStatusStyle(agent.status))}>
                    {getTypeIcon(agent.type)}
                  </div>
                  <div>
                    <div className="font-semibold text-[#111111]">{agent.name}</div>
                    <div className="text-[10px] text-[#5A5A5A] font-mono">{agent.id} Â· {agent.environment}</div>
                  </div>
                </div>
                <span className={cn('px-2 py-0.5 rounded text-[9px] font-bold uppercase tracking-wide border', getStatusStyle(agent.status))}>
                  {agent.status}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="p-2.5 bg-[#F8F5EE] rounded-lg border border-[#BFA66A]/30">
                  <div className="text-[8px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1">KB Connection</div>
                  <div className="text-[10px] font-mono text-[#2A2A2A] truncate">{agent.kbConnection}</div>
                </div>
                <div className="p-2.5 bg-[#F8F5EE] rounded-lg border border-[#BFA66A]/30">
                  <div className="text-[8px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1">Performance</div>
                  <div className="text-[10px] font-mono text-[#2A2A2A]">{agent.latency} / {agent.errorRate}% err</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="fixed top-5 right-5 z-[9999] flex items-center gap-2.5 px-4 py-3 bg-[#111111] text-white text-[12px] font-semibold rounded-xl shadow-xl"
          >
            <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0" />
            {toast}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Clone Agent Modal */}
      <AnimatePresence>
        {cloneTarget && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[9998] flex items-center justify-center bg-black/30 backdrop-blur-sm"
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              onClick={(e) => e.stopPropagation()}
              onMouseDown={(e) => e.stopPropagation()}
              className="bg-white border border-[#ECE7DA] rounded-2xl shadow-2xl p-6 w-[420px]"
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-bold text-[#111111]">Clone Agent</h3>
                <button onClick={() => setCloneTarget(null)} className="p-1.5 hover:bg-[#F3E2A7] rounded-lg transition-colors text-[#8A8A7A]">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="text-xs text-[#8A8A7A] mb-4">Nháº­p tÃªn cho agent má»›i Ä'Æ°á»£c clone tá»« <span className="font-semibold text-[#2A2A2A]">{cloneTarget.name}</span></p>
              <input
                autoFocus
                type="text"
                value={cloneModalName}
                onChange={(e) => setCloneModalName(e.target.value)}
                className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2.5 px-4 text-sm text-[#111111] focus:outline-none focus:border-[#BFA66A] mb-5"
              />
              <div className="flex justify-end gap-2">
                <button
                  onClick={() => setCloneTarget(null)}
                  className="btn-secondary"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    setToast(`Agent "${cloneModalName}" cloned successfully`);
                    setCloneTarget(null);
                  }}
                  disabled={!cloneModalName.trim()}
                  className="btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Copy className="w-4 h-4" /> Clone
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Delete Confirm Modal */}
      <AnimatePresence>
        {deleteTarget && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[9998] flex items-center justify-center bg-black/30 backdrop-blur-sm"
            onClick={() => setDeleteTarget(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              onClick={e => e.stopPropagation()}
              onMouseDown={e => e.stopPropagation()}
              className="bg-white border border-[#ECE7DA] rounded-2xl shadow-2xl p-6 w-[400px]"
            >
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-base font-bold text-[#111111]">Xóa agent</h3>
                <button onClick={() => setDeleteTarget(null)} className="p-1.5 hover:bg-[#F3E2A7] rounded-lg transition-colors text-[#8A8A7A]">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <p className="text-sm text-[#8A8A7A] mb-5">
                Bạn chắc chắn muốn xóa agent{' '}
                <span className="font-semibold text-[#111111]">"{deleteTarget.name}"</span>?
                Hành động này không thể hoàn tác.
              </p>
              <div className="flex justify-end gap-2">
                <button onClick={() => setDeleteTarget(null)} className="btn-secondary" disabled={deleting}>
                  Huỷ
                </button>
                <button
                  onClick={handleDelete}
                  disabled={deleting}
                  className="flex items-center gap-1.5 px-4 py-2 bg-red-500 hover:bg-red-600 disabled:opacity-50 text-white text-xs font-bold rounded-xl transition-all"
                >
                  {deleting ? 'Đang xóa...' : <><Trash2 className="w-3.5 h-3.5" /> Xóa</>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};
