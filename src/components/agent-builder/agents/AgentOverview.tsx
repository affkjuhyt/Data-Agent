import React, { useState, useEffect, useRef, useCallback } from 'react';
import ReactDOM from 'react-dom';
import { motion, AnimatePresence } from 'motion/react';
import {
  Bot, Cpu, Plus, Search, Activity, BarChart3, ShieldAlert, Zap,
  Copy, Trash2, Settings2, X, CheckCircle2, MoreHorizontal,
  SlidersHorizontal, SearchX,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { FLOW_BUILDER_URL } from '../../../lib/config';

interface ApiAgent {
  id: string;
  name: string;
  description: string;
  published_version_id: string | null;
  draft_version_id: string | null;
  created_at: string;
}

type AgentStatus = 'ACTIVE' | 'IDLE';

function deriveStatus(agent: ApiAgent): AgentStatus {
  return agent.published_version_id ? 'ACTIVE' : 'IDLE';
}

const STATUS_CONFIG: Record<AgentStatus, { color: string; label: string }> = {
  ACTIVE: { color: '#6B8E23', label: 'Active' },
  IDLE:   { color: '#9AA1A8', label: 'Idle'   },
};

interface OverviewProps {
  onNewAgent: () => void;
  onOpenCLI: () => void;
  onOpenProvision: () => void;
  onSelectAgent: (id: string) => void;
}

export const AgentRegistryOverview: React.FC<OverviewProps> = ({
  onNewAgent,
  onSelectAgent,
}) => {
  const [apiAgents, setApiAgents] = useState<ApiAgent[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState('');
  const [filterOpen, setFilterOpen] = useState(false);
  const [fStatus, setFStatus] = useState<string>('ALL');
  const filterRef = useRef<HTMLDivElement>(null);

  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });
  const [cloneTarget, setCloneTarget] = useState<{ id: string; name: string } | null>(null);
  const [cloneModalName, setCloneModalName] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${FLOW_BUILDER_URL}/api/agents`)
      .then(r => r.json())
      .then((d: ApiAgent[]) => setApiAgents(Array.isArray(d) ? d : []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (filterRef.current && !filterRef.current.contains(e.target as Node)) setFilterOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, []);

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

  const handleMenuOpen = useCallback((e: React.MouseEvent<HTMLButtonElement>, id: string) => {
    e.stopPropagation();
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
      setToast(`Agent "${deleteTarget.name}" deleted`);
    } catch {
      setToast('Delete failed');
    } finally {
      setDeleting(false);
      setDeleteTarget(null);
    }
  };

  const q = query.trim().toLowerCase();
  const activeFilters = fStatus !== 'ALL' ? 1 : 0;
  const agents = apiAgents.filter(a => {
    const matchQ = !q || `${a.name} ${a.id} ${a.description ?? ''}`.toLowerCase().includes(q);
    const matchStatus = fStatus === 'ALL' || deriveStatus(a) === fStatus;
    return matchQ && matchStatus;
  });

  const activeCount = apiAgents.filter(a => a.published_version_id).length;

  const stats = [
    { label: 'Total Agents',      value: loading ? '—' : apiAgents.length, icon: Bot,        color: '#B88719' },
    { label: 'Active Runtimes',   value: loading ? '—' : activeCount,       icon: Activity,   color: '#10B981' },
    { label: 'Critical Failures', value: 0,                                  icon: ShieldAlert, color: '#DC2626' },
    { label: 'Platform Health',   value: '94%',                              icon: Zap,        color: '#D9A040' },
    { label: 'Daily Accrual',     value: '$—',                               icon: BarChart3,  color: '#8B5CF6' },
  ];

  return (
    <>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>

        {/* Header */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              color: '#B88719', fontSize: 10, fontWeight: 700,
              fontFamily: 'var(--font-mono)', letterSpacing: '0.14em',
              textTransform: 'uppercase', marginBottom: 6,
            }}>
              <Cpu size={13} />
              Build / Agents
            </div>
            <h2 style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-display)', margin: 0 }}>
              Agent Inventory
            </h2>
          </div>
          <button className="btn-primary" onClick={onNewAgent}>
            <Plus size={15} />
            New Agent
          </button>
        </div>

        {/* Stats cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(148px, 1fr))', gap: 12 }}>
          {stats.map(s => (
            <div key={s.label} className="warm-card" style={{ borderRadius: 14, padding: 16 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
                <s.icon size={20} style={{ color: s.color }} />
                <span style={{
                  fontSize: 8, fontWeight: 700, color: '#8A8A7A',
                  textTransform: 'uppercase', letterSpacing: '0.14em',
                }}>Live</span>
              </div>
              <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--font-display)', lineHeight: 1.1 }}>
                {s.value}
              </div>
              <div style={{
                fontSize: 9, color: '#5A5A5A', marginTop: 4,
                fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em',
              }}>
                {s.label}
              </div>
            </div>
          ))}
        </div>

        {/* Table panel */}
        <div className="warm-panel" style={{ borderRadius: 14, overflow: 'hidden' }}>

          {/* Toolbar */}
          <div style={{
            padding: '12px 16px',
            background: '#FFF9E8',
            borderBottom: '1px solid rgba(191,166,106,0.4)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            gap: 12, flexWrap: 'wrap',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ position: 'relative' }}>
                <Search size={13} style={{
                  position: 'absolute', left: 11, top: '50%',
                  transform: 'translateY(-50%)', color: '#8A8A7A',
                }} />
                <input
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  placeholder="Query agents..."
                  style={{
                    width: 216, background: '#fff',
                    border: '1px solid rgba(191,166,106,0.6)',
                    borderRadius: 8, padding: '7px 12px 7px 30px',
                    fontSize: 12, outline: 'none', fontFamily: 'inherit',
                  }}
                />
              </div>
              <span style={{ fontSize: 11, color: '#8A8A7A', fontFamily: 'var(--font-mono)' }}>
                {agents.length} of {apiAgents.length}
              </span>
            </div>

            <div ref={filterRef} style={{ position: 'relative' }}>
              <button
                className="btn-secondary"
                style={{ fontSize: 10, padding: '7px 12px', borderColor: activeFilters ? '#B88719' : undefined }}
                onClick={() => setFilterOpen(o => !o)}
              >
                <SlidersHorizontal size={13} />
                Filters
                {activeFilters > 0 && (
                  <span style={{
                    marginLeft: 4, background: '#B88719', color: '#fff',
                    borderRadius: 9999, fontSize: 9, fontWeight: 800, padding: '1px 6px',
                  }}>
                    {activeFilters}
                  </span>
                )}
              </button>

              {filterOpen && (
                <div style={{
                  position: 'absolute', right: 0, top: '100%', marginTop: 6,
                  width: 196, background: '#fff', border: '1px solid #BFA66A',
                  borderRadius: 12, boxShadow: '0 10px 30px rgba(0,0,0,0.14)',
                  padding: 14, zIndex: 40,
                }}>
                  <div style={{ marginBottom: 10 }}>
                    <label className="field-label" style={{ marginBottom: 5 }}>Status</label>
                    <select
                      className="field-select"
                      value={fStatus}
                      onChange={e => setFStatus(e.target.value)}
                      style={{ fontSize: 12, padding: '7px 10px' }}
                    >
                      <option value="ALL">All</option>
                      <option value="ACTIVE">Active</option>
                      <option value="IDLE">Idle / Draft</option>
                    </select>
                  </div>
                  {activeFilters > 0 && (
                    <button
                      className="btn-ghost"
                      style={{ width: '100%', justifyContent: 'center', fontSize: 11 }}
                      onClick={() => setFStatus('ALL')}
                    >
                      <X size={12} /> Clear filters
                    </button>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Table */}
          <table className="t-table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Environment</th>
                <th>KB</th>
                <th>Latency / Health</th>
                <th>Error Rate</th>
                <th>Status</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: 'center', padding: '40px 20px', color: '#8A8A7A', fontSize: 13 }}>
                    Loading agents…
                  </td>
                </tr>
              ) : agents.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ padding: 0 }}>
                    <div style={{
                      display: 'flex', flexDirection: 'column', alignItems: 'center',
                      justifyContent: 'center', padding: '48px 20px', gap: 10,
                    }}>
                      {(query || activeFilters > 0) ? (
                        <>
                          <SearchX size={32} style={{ color: '#BFA66A' }} />
                          <div style={{ fontSize: 14, fontWeight: 600, color: '#3F3F3F' }}>No agents match</div>
                          <div style={{ fontSize: 12, color: '#8A8A7A' }}>
                            Try a different search term or clear your filters.
                          </div>
                          <button
                            className="btn-ghost"
                            style={{ fontSize: 11 }}
                            onClick={() => { setQuery(''); setFStatus('ALL'); }}
                          >
                            <X size={12} /> Clear filters
                          </button>
                        </>
                      ) : (
                        <>
                          <Bot size={32} style={{ color: '#BFA66A' }} />
                          <div style={{ fontSize: 14, fontWeight: 600, color: '#3F3F3F' }}>No agents yet</div>
                          <div style={{ fontSize: 12, color: '#8A8A7A' }}>
                            Click New Agent to create your first agent.
                          </div>
                          <button className="btn-primary" style={{ marginTop: 4 }} onClick={onNewAgent}>
                            <Plus size={13} /> New Agent
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ) : agents.map(agent => {
                const status = deriveStatus(agent);
                const { color, label } = STATUS_CONFIG[status];
                return (
                  <tr key={agent.id} onClick={() => onSelectAgent(agent.id)} style={{ cursor: 'pointer' }}>
                    <td>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
                        <div style={{
                          width: 36, height: 36, borderRadius: 10,
                          display: 'flex', alignItems: 'center', justifyContent: 'center',
                          background: '#EFF4FB', border: '1px solid #BFD3EC',
                          color: '#2D6BB8', flexShrink: 0,
                        }}>
                          <Bot size={16} />
                        </div>
                        <div style={{ minWidth: 0 }}>
                          <div style={{
                            fontWeight: 600, color: '#111',
                            whiteSpace: 'nowrap', overflow: 'hidden',
                            textOverflow: 'ellipsis', lineHeight: 1.3,
                          }}>
                            {agent.name}
                          </div>
                          <div style={{
                            fontSize: 10, fontFamily: 'var(--font-mono)', color: '#B88719',
                            marginTop: 2, whiteSpace: 'nowrap',
                          }}>
                            {agent.id.slice(0, 12)}…
                          </div>
                          {agent.description && (
                            <div style={{
                              fontSize: 10, color: '#8A8A7A', marginTop: 1,
                              overflow: 'hidden', textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap', maxWidth: 220,
                            }}>
                              {agent.description}
                            </div>
                          )}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span style={{
                        fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
                        background: '#F3E2A7', border: '1px solid rgba(191,166,106,0.5)',
                        color: '#7C5A0E',
                      }}>
                        DEV
                      </span>
                    </td>
                    <td style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#A8A899' }}>—</td>
                    <td style={{ fontSize: 12, color: '#A8A899' }}>—</td>
                    <td style={{ fontSize: 12, color: '#A8A899' }}>—</td>
                    <td>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: 6,
                        fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
                        letterSpacing: '0.06em', color: '#2A2A2A',
                      }}>
                        <span
                          className={status === 'ACTIVE' ? 'pulse-soft' : ''}
                          style={{
                            width: 7, height: 7, borderRadius: '50%',
                            background: color, display: 'inline-block',
                          }}
                        />
                        {label}
                      </span>
                    </td>
                    <td style={{ textAlign: 'right', paddingRight: 12 }}>
                      <button
                        onMouseDown={e => e.stopPropagation()}
                        onClick={e => handleMenuOpen(e, agent.id)}
                        style={{
                          padding: '6px 8px', background: 'transparent', border: 'none',
                          borderRadius: 8, cursor: 'pointer', color: '#C8BCA8', transition: 'all 0.12s',
                        }}
                        onMouseEnter={e => {
                          (e.currentTarget as HTMLButtonElement).style.background = '#F3E2A7';
                          (e.currentTarget as HTMLButtonElement).style.color = '#2A2A2A';
                        }}
                        onMouseLeave={e => {
                          (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                          (e.currentTarget as HTMLButtonElement).style.color = '#C8BCA8';
                        }}
                      >
                        <MoreHorizontal size={16} />
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Context menu — portal */}
      {openMenuId && ReactDOM.createPortal(
        <div
          onMouseDown={e => e.stopPropagation()}
          style={{
            position: 'fixed', top: menuPos.top, right: menuPos.right, zIndex: 9999,
            width: 172, background: '#fff', border: '1px solid rgba(191,166,106,0.5)',
            borderRadius: 12, boxShadow: '0 8px 24px rgba(0,0,0,0.14)', overflow: 'hidden',
          }}
        >
          {(() => {
            const agent = apiAgents.find(a => a.id === openMenuId);
            if (!agent) return null;
            return (
              <>
                <button
                  onClick={() => { setOpenMenuId(null); onSelectAgent(agent.id); }}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 16px', fontSize: 12, fontWeight: 600, color: '#2A2A2A',
                    background: 'transparent', border: 'none', cursor: 'pointer',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#FFF9E8')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Settings2 size={14} style={{ color: '#8A8A7A' }} /> Edit / View
                </button>
                <button
                  onClick={() => {
                    setOpenMenuId(null);
                    setCloneModalName(`Copy of ${agent.name}`);
                    setCloneTarget({ id: agent.id, name: agent.name });
                  }}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 16px', fontSize: 12, fontWeight: 600, color: '#2A2A2A',
                    background: 'transparent', border: 'none', cursor: 'pointer',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#FFF9E8')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Copy size={14} style={{ color: '#8A8A7A' }} /> Clone
                </button>
                <div style={{ height: 1, background: '#ECE7DA', margin: '0 12px' }} />
                <button
                  onClick={() => {
                    setOpenMenuId(null);
                    setDeleteTarget({ id: agent.id, name: agent.name });
                  }}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 16px', fontSize: 12, fontWeight: 600, color: '#DC2626',
                    background: 'transparent', border: 'none', cursor: 'pointer',
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = '#FFF5F5')}
                  onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
                >
                  <Trash2 size={14} /> Delete
                </button>
              </>
            );
          })()}
        </div>,
        document.body,
      )}

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.18 }}
            style={{
              position: 'fixed', top: 20, right: 20, zIndex: 9999,
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '12px 16px', background: '#111', color: '#fff',
              fontSize: 12, fontWeight: 600, borderRadius: 14,
              boxShadow: '0 8px 32px rgba(0,0,0,0.28)', fontFamily: 'inherit',
            }}
          >
            <CheckCircle2 size={15} style={{ color: '#6BD42B', flexShrink: 0 }} />
            {toast}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Clone Modal */}
      <AnimatePresence>
        {cloneTarget && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{
              position: 'fixed', inset: 0, zIndex: 9998,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(0,0,0,0.28)', backdropFilter: 'blur(4px)',
            }}
            onClick={() => setCloneTarget(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ duration: 0.16 }}
              onClick={e => e.stopPropagation()}
              className="modal-shell"
              style={{ width: 420 }}
            >
              <div className="modal-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>Clone Agent</span>
                <button
                  onClick={() => setCloneTarget(null)}
                  style={{ padding: 6, background: 'transparent', border: 'none', borderRadius: 8, cursor: 'pointer', color: '#E5E5E5' }}
                >
                  <X size={16} />
                </button>
              </div>
              <div className="modal-body">
                <p style={{ fontSize: 13, color: '#5A5A5A', marginBottom: 16 }}>
                  New agent cloned from <strong style={{ color: '#111' }}>{cloneTarget.name}</strong>
                </p>
                <label className="field-label">New Agent Name</label>
                <input
                  autoFocus
                  value={cloneModalName}
                  onChange={e => setCloneModalName(e.target.value)}
                  className="field-input"
                  onKeyDown={e => {
                    if (e.key === 'Enter' && cloneModalName.trim()) {
                      setToast(`Agent "${cloneModalName}" cloned`);
                      setCloneTarget(null);
                    }
                  }}
                />
              </div>
              <div className="modal-footer">
                <button className="btn-secondary" onClick={() => setCloneTarget(null)}>Cancel</button>
                <button
                  className="btn-primary"
                  disabled={!cloneModalName.trim()}
                  onClick={() => { setToast(`Agent "${cloneModalName}" cloned`); setCloneTarget(null); }}
                >
                  <Copy size={14} /> Clone
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Delete Modal */}
      <AnimatePresence>
        {deleteTarget && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{
              position: 'fixed', inset: 0, zIndex: 9998,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              background: 'rgba(0,0,0,0.28)', backdropFilter: 'blur(4px)',
            }}
            onClick={() => !deleting && setDeleteTarget(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              transition={{ duration: 0.16 }}
              onClick={e => e.stopPropagation()}
              className="modal-shell"
              style={{ width: 400 }}
            >
              <div className="modal-header danger" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <span style={{ fontSize: 14, fontWeight: 700 }}>Delete Agent</span>
                <button
                  onClick={() => setDeleteTarget(null)}
                  disabled={deleting}
                  style={{ padding: 6, background: 'transparent', border: 'none', borderRadius: 8, cursor: 'pointer', color: '#E5E5E5' }}
                >
                  <X size={16} />
                </button>
              </div>
              <div className="modal-body">
                <p style={{ fontSize: 13, color: '#5A5A5A', lineHeight: 1.6 }}>
                  Delete <strong style={{ color: '#111' }}>"{deleteTarget.name}"</strong>? This will remove the agent and all its workflows. This action cannot be undone.
                </p>
              </div>
              <div className="modal-footer">
                <button className="btn-secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
                  Cancel
                </button>
                <button className="btn-danger" onClick={handleDelete} disabled={deleting}>
                  {deleting ? 'Deleting…' : <><Trash2 size={13} /> Delete</>}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};
