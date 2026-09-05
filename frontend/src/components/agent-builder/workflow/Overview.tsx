import React, { useState, useEffect, useCallback } from 'react';
import {
  Plus,
  Search,
  Filter,
  GitMerge,
  Play,
  Clock,
  CheckCircle2,
  AlertCircle,
  MoreVertical,
  History,
  Code,
  Settings2,
  LayoutGrid,
  List,
  Copy,
  Bot,
  ShieldCheck,
  Activity,
  ArrowRight,
  X,
  Database,
  Zap,
  Trash2,
  FileCode,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import ReactDOM from 'react-dom';
import { Workflow } from '../../../types/workflow';
import WorkflowBuilder, { type TemplateId } from './Builder';
import ExecutionCenter from './ExecutionCenter';
import SchedulingCenter from './SchedulingCenter';
import WorkflowObservability from './Observability';
import { apiGet, apiMutate } from '../../../lib/apiClient';

interface ApiAgent {
  id: string;
  name: string;
  description: string;
  published_version_id: string | null;
  draft_version_id: string | null;
  created_at: string;
}

interface ApiWorkflow {
  id: string;
  name: string;
  draft_version_id: string | null;
  published_version_id: string | null;
}


// â”€â”€â”€ Template Definitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

type TemplateCategory = 'ALL' | 'RAG' | 'MULTI_AGENT' | 'HITL' | 'AUTOMATION' | 'KNOWLEDGE';

const TEMPLATE_LIBRARY: Array<{
  builderTemplate: TemplateId;
  name: string;
  desc: string;
  nodeCount: number;
  Icon: React.ElementType;
  color: string;
  category: TemplateCategory;
  isPopular?: boolean;
}> = [
  { builderTemplate: 'blank',        name: 'Blank Canvas',             desc: 'Báº¯t Ä‘áº§u tá»« canvas trá»‘ng â€” chá»‰ cÃ³ Trigger node.',                       nodeCount: 1,  Icon: LayoutGrid, color: '#64748b', category: 'AUTOMATION' },
  { builderTemplate: 'multi-agent',  name: 'Full RAG Agent',           desc: 'KB Search + Reranker + Responder â€” flow chuáº©n cho há»i Ä‘Ã¡p tÃ i liá»‡u.',    nodeCount: 8,  Icon: Bot,        color: '#3b82f6', category: 'RAG',        isPopular: true },
  { builderTemplate: 'hitl',         name: 'Human-in-the-Loop',        desc: 'Cáº§n ngÆ°á»i review vÃ  phÃª duyá»‡t trÆ°á»›c khi tráº£ káº¿t quáº£.',                   nodeCount: 9,  Icon: ShieldCheck, color: '#f97316', category: 'HITL',       isPopular: true },
  { builderTemplate: 'multi-agent',  name: 'GraphRAG Pipeline',        desc: 'Káº¿t há»£p vector search vÃ  knowledge graph Ä‘á»ƒ tráº£ lá»i phá»©c táº¡p.',          nodeCount: 12, Icon: Activity,   color: '#10b981', category: 'KNOWLEDGE',  isPopular: true },
  { builderTemplate: 'multi-agent',  name: 'Multi-Agent Triage',       desc: 'Planner phÃ¢n loáº¡i cÃ¢u há»i vÃ  Ä‘iá»u phá»‘i sang agent chuyÃªn biá»‡t.',         nodeCount: 10, Icon: Bot,        color: '#8b5cf6', category: 'MULTI_AGENT' },
  { builderTemplate: 'blank',        name: 'Document Processing',      desc: 'Extract, chunk, embed vÃ  index tÃ i liá»‡u vÃ o knowledge base.',            nodeCount: 7,  Icon: Code,       color: '#6366f1', category: 'RAG' },
  { builderTemplate: 'blank',        name: 'Scheduled KB Sync',        desc: 'Tá»± Ä‘á»™ng cáº­p nháº­t knowledge base theo lá»‹ch CRON.',                       nodeCount: 6,  Icon: Clock,      color: '#06b6d4', category: 'AUTOMATION' },
  { builderTemplate: 'hitl',         name: 'Compliance Review Bot',    desc: 'Review tÃ i liá»‡u phÃ¡p lÃ½ vá»›i Human-in-the-Loop vÃ  audit trail Ä‘áº§y Ä‘á»§.',   nodeCount: 11, Icon: ShieldCheck, color: '#ef4444', category: 'HITL' },
  { builderTemplate: 'multi-agent',  name: 'Customer Support',         desc: 'Bot há»— trá»£ khÃ¡ch hÃ ng â€” tÃ­ch há»£p CRM, KB vÃ  escalation tá»± Ä‘á»™ng.',       nodeCount: 9,  Icon: Bot,        color: '#22c55e', category: 'MULTI_AGENT' },
  { builderTemplate: 'multi-agent',  name: 'Code Review Agent',        desc: 'PhÃ¢n tÃ­ch code, kiá»ƒm tra security vÃ  Ä‘á» xuáº¥t refactor.',                 nodeCount: 8,  Icon: Code,       color: '#a855f7', category: 'MULTI_AGENT' },
  { builderTemplate: 'blank',        name: 'Data Extraction Pipeline', desc: 'TrÃ­ch xuáº¥t dá»¯ liá»‡u cÃ³ cáº¥u trÃºc tá»« tÃ i liá»‡u phi cáº¥u trÃºc.',              nodeCount: 5,  Icon: Database,   color: '#f59e0b', category: 'RAG' },
  { builderTemplate: 'blank',        name: 'Event-Driven Orchestrator',desc: 'Trigger workflow tá»« webhook, message queue hoáº·c database event.',        nodeCount: 7,  Icon: Zap,        color: '#ec4899', category: 'AUTOMATION' },
];

const CATEGORY_LABELS: Record<TemplateCategory, string> = {
  ALL: 'Táº¥t cáº£', RAG: 'RAG', MULTI_AGENT: 'Multi-Agent',
  HITL: 'Human-in-Loop', AUTOMATION: 'Automation', KNOWLEDGE: 'Knowledge',
};

// â”€â”€â”€ Template Picker (full-page) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface TemplatePickerProps {
  onSelect: (t: TemplateId) => void;
  onClose: () => void;
}

const TemplatePicker = ({ onSelect, onClose }: TemplatePickerProps) => {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<TemplateCategory>('ALL');

  const filtered = TEMPLATE_LIBRARY.filter(t => {
    const matchCat = category === 'ALL' || t.category === category;
    const matchSearch = t.name.toLowerCase().includes(search.toLowerCase()) || t.desc.toLowerCase().includes(search.toLowerCase());
    return matchCat && matchSearch;
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
      className="space-y-5"
    >
      {/* Header */}
      <div className="bg-white border border-[#ECE7DA] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <button
              onClick={onClose}
              className="p-2 hover:bg-[#F3E2A7] rounded-xl border border-[#ECE7DA] transition-colors text-[#5A5A5A]"
            >
              <ArrowRight className="w-4 h-4 rotate-180" />
            </button>
            <div>
              <h2 className="text-lg font-display font-bold text-[#111111]">Chá»n Template</h2>
              <p className="text-xs text-[#8A8A7A] mt-0.5">Báº¯t Ä‘áº§u tá»« template cÃ³ sáºµn hoáº·c táº¡o workflow trá»‘ng</p>
            </div>
          </div>
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A8A7A]" />
            <input
              type="text"
              placeholder="TÃ¬m template..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="w-56 bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 pl-10 pr-4 text-sm text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#BFA66A]"
            />
          </div>
        </div>

        {/* Category filters */}
        <div className="flex items-center gap-2 flex-wrap">
          {(Object.keys(CATEGORY_LABELS) as TemplateCategory[]).map(cat => (
            <button
              key={cat}
              onClick={() => setCategory(cat)}
              className={`px-3 py-1.5 rounded-lg text-[11px] font-semibold transition-colors border ${
                category === cat
                  ? 'bg-[#111111] text-white border-[#111111]'
                  : 'bg-white text-[#5A5A5A] border-[#ECE7DA] hover:border-[#BFA66A] hover:text-[#2A2A2A]'
              }`}
            >
              {CATEGORY_LABELS[cat]}
            </button>
          ))}
        </div>
      </div>

      {/* Template grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {filtered.map((t, i) => (
          <motion.button
            key={i}
            initial={{ opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.03 }}
            onClick={() => onSelect(t.builderTemplate)}
            className="group text-left bg-white border border-[#ECE7DA] rounded-2xl p-5 hover:border-[#BFA66A] hover:shadow-md transition-all relative"
          >
            {t.isPopular && (
              <span className="absolute top-3 right-3 text-[8px] font-black uppercase tracking-widest px-2 py-0.5 rounded-full bg-[#F3E2A7] text-[#7C5A0E] border border-[#BFA66A]/40">
                Popular
              </span>
            )}
            <div className="flex items-center justify-between mb-4">
              <div className="p-2.5 rounded-xl" style={{ background: `${t.color}15`, border: `1px solid ${t.color}30` }}>
                <t.Icon size={18} style={{ color: t.color }} />
              </div>
              <span className="text-[9px] font-bold text-[#8A8A7A] uppercase tracking-widest">
                {t.nodeCount} nodes
              </span>
            </div>
            <div className="text-sm font-bold text-[#111111] mb-1.5 group-hover:text-[#B88719] transition-colors">
              {t.name}
            </div>
            <p className="text-[11px] text-[#8A8A7A] leading-relaxed line-clamp-2">{t.desc}</p>
            <div className="mt-4 pt-3 border-t border-[#ECE7DA] flex items-center justify-between">
              <span className="text-[9px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full" style={{ background: `${t.color}10`, color: t.color }}>
                {CATEGORY_LABELS[t.category]}
              </span>
              <span className="text-[10px] font-semibold text-[#B88719] opacity-0 group-hover:opacity-100 transition-opacity">
                DÃ¹ng template â†’
              </span>
            </div>
          </motion.button>
        ))}
      </div>
    </motion.div>
  );
};

// â”€â”€â”€ Workflow Engine â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

type ActiveTab = 'REGISTRY' | 'BUILDER' | 'TEMPLATE_PICKER' | 'EXECUTIONS' | 'SCHEDULING' | 'OBSERVABILITY';

const WorkflowEngine = () => {
  const [activeTab, setActiveTab] = useState<ActiveTab>('REGISTRY');
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [selectedTemplate, setSelectedTemplate] = useState<TemplateId>('multi-agent');
  const [builderSource, setBuilderSource] = useState<'REGISTRY' | 'TEMPLATE_PICKER'>('REGISTRY');
  const [filterWorkflowName, setFilterWorkflowName] = useState<string | undefined>(undefined);
  const [toast, setToast] = useState<string | null>(null);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });

  // ── Real API state ──────────────────────────────────────────────────────────
  const [apiAgents, setApiAgents] = useState<ApiAgent[]>([]);
  const [registrySearch, setRegistrySearch] = useState('');
  const [builderAgentId, setBuilderAgentId] = useState<string | undefined>();
  const [builderWorkflowVersionId, setBuilderWorkflowVersionId] = useState<string | undefined>();
  const [builderWorkflowId, setBuilderWorkflowId] = useState<string | undefined>();
  const [builderSourceVersionId, setBuilderSourceVersionId] = useState<string | undefined>();
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState('');
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [publishing, setPublishing] = useState<string | null>(null);

  useEffect(() => {
    apiGet<ApiAgent[]>('/api/agents')
      .then(data => setApiAgents(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  const handleCreateAgent = async () => {
    if (!createName.trim()) return;
    setCreating(true);
    try {
      const data = await apiMutate<any>('POST', '/api/agents', { name: createName.trim(), description: '' });
      setApiAgents(prev => [...prev, {
        id: data.agent_id,
        name: createName.trim(),
        description: '',
        published_version_id: null,
        draft_version_id: data.agent_version_id,
        created_at: new Date().toISOString(),
      }]);
      setBuilderAgentId(data.agent_id);
      setBuilderWorkflowVersionId(data.workflow_version_id);
      setBuilderWorkflowId(data.workflow_id);
      setBuilderSourceVersionId(undefined);
      setShowCreateModal(false);
      setCreateName('');
      setSelectedWorkflow(null);
      setBuilderSource('REGISTRY');
      setActiveTab('BUILDER');
    } catch {
      setToast('Tạo agent thất bại');
    } finally {
      setCreating(false);
    }
  };

  const handleEditAgent = async (agent: ApiAgent) => {
    try {
      const data = await apiGet<any>(`/api/agents/${agent.id}`);
      const workflows: ApiWorkflow[] = data.workflows ?? [];
      const workflowId = workflows[0]?.id;
      let wv = workflows[0]?.draft_version_id ?? null;
      let sourceVersionId: string | undefined;

      // publish_workflow_version flips the draft to "published" in place — it
      // doesn't leave a new draft behind — so a published-only workflow has no
      // draft_version_id here. Create one now (before the canvas ever renders)
      // so Builder's existing sourceVersionId-copy effect can load the published
      // nodes into it; doing this lazily on Save instead races that same effect
      // and wipes the on-screen canvas back to empty.
      if (!wv && workflowId) {
        const created = await apiMutate<any>('POST', `/api/workflows/${workflowId}/versions`);
        wv = created.workflow_version_id;
        sourceVersionId = created.source_version_id ?? undefined;
      }

      setBuilderAgentId(agent.id);
      setBuilderWorkflowVersionId(wv ?? undefined);
      setBuilderWorkflowId(workflowId ?? undefined);
      setBuilderSourceVersionId(sourceVersionId);
      setSelectedWorkflow(null);
      setBuilderSource('REGISTRY');
      setActiveTab('BUILDER');
    } catch {
      setToast('Không thể mở agent');
    }
  };

  const handlePublishAgent = async (agent: ApiAgent) => {
    setPublishing(agent.id);
    try {
      const agentDetail = await apiGet<any>(`/api/agents/${agent.id}`);
      const workflows: ApiWorkflow[] = agentDetail.workflows ?? [];
      if (!workflows[0]) { setToast('Không có workflow để publish'); return; }
      await apiMutate('POST', `/api/agents/${agent.id}/publish`, { workflow_id: workflows[0].id });
      setToast(`✓ "${agent.name}" đã publish`);
      const updated = await apiGet<ApiAgent[]>('/api/agents');
      setApiAgents(Array.isArray(updated) ? updated : []);
    } catch {
      setToast('Publish thất bại');
    } finally {
      setPublishing(null);
    }
  };

  const handleDeleteAgent = async (agent: ApiAgent) => {
    if (!window.confirm(`Xoá agent "${agent.name}" cùng toàn bộ workflow?`)) return;
    try {
      await apiMutate('DELETE', `/api/agents/${agent.id}`);
      setApiAgents(prev => prev.filter(a => a.id !== agent.id));
      setToast(`Đã xoá "${agent.name}"`);
    } catch {
      setToast('Xoá thất bại');
    }
  };

  const handleMenuOpen = useCallback((e: React.MouseEvent<HTMLButtonElement>, id: string) => {
    const rect = e.currentTarget.getBoundingClientRect();
    setMenuPos({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
    setOpenMenuId(id);
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



  const handleNewWorkflow = () => { setCreateName(''); setShowCreateModal(true); };

  const handleTemplateSelect = (t: TemplateId) => {
    setSelectedTemplate(t);
    setSelectedWorkflow(null);
    setBuilderSource('TEMPLATE_PICKER');
    setActiveTab('BUILDER');
  };


  const handleBuilderClose = () => {
    setSelectedWorkflow(null);
    setBuilderAgentId(undefined);
    setBuilderWorkflowVersionId(undefined);
    setBuilderWorkflowId(undefined);
    setBuilderSourceVersionId(undefined);
    setActiveTab(builderSource);
  };

  const WORKFLOW_TABS = [
    { id: 'REGISTRY',      label: 'Registry',      Icon: List     },
    { id: 'EXECUTIONS',    label: 'Executions',    Icon: History  },
    { id: 'SCHEDULING',    label: 'Scheduling',    Icon: Clock    },
    { id: 'OBSERVABILITY', label: 'Observability', Icon: Activity },
  ] as const;

  const registryQuery = registrySearch.trim().toLowerCase();
  const visibleAgents = apiAgents.filter(a =>
    !registryQuery || `${a.name} ${a.description ?? ''}`.toLowerCase().includes(registryQuery));

  const renderRegistry = () => (
    <div className="flex flex-col relative">

      {/* Registry Table */}
      <div className="warm-panel rounded-xl mb-5 overflow-x-auto">
        <table className="w-full text-left">
          <thead>
            <tr className="border-b border-[#BFA66A]/30 bg-[#FFF9E8]">
              {['Agent / Description', 'Status', 'Created', 'Actions'].map((h, i) => (
                <th key={h} className={`px-5 py-3 text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider ${i === 5 ? 'text-right' : ''} ${i === 0 ? 'rounded-tl-xl' : ''} ${i === 5 ? 'rounded-tr-xl' : ''}`}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A]/15">
            {visibleAgents.length === 0 && (
              <tr>
                <td colSpan={5} className="px-5 py-10 text-center text-sm text-[#5F5F5F]">
                  {registrySearch.trim() ? 'Không có agent nào khớp.' : 'Chưa có agent nào. Bấm "+ New Workflow" để tạo.'}
                </td>
              </tr>
            )}
            {visibleAgents.map((agent) => (
              <motion.tr
                initial={{ opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                key={agent.id}
                className="hover:bg-[#FFF9E8] transition-colors"
              >
                <td className="px-5 py-4">
                  <div className="flex items-center gap-3">
                    <div className="p-2.5 rounded-xl border bg-blue-50 border-blue-200 text-blue-600">
                      <GitMerge className="w-4 h-4" />
                    </div>
                    <div>
                      <div
                        className="text-[#111111] font-semibold text-sm mb-0.5 hover:text-[#B88719] transition-colors cursor-pointer"
                        onClick={() => handleEditAgent(agent)}
                      >
                        {agent.name}
                      </div>
                      <div className="text-[10px] text-[#8A8A7A]">{agent.description || '—'}</div>
                    </div>
                  </div>
                </td>

                <td className="px-5 py-4">
                  <div className="flex items-center gap-2">
                    <div className={`w-1.5 h-1.5 rounded-full ${agent.published_version_id ? 'bg-emerald-500' : 'bg-gray-400'}`} />
                    <span className="text-xs font-bold text-[#111111] uppercase tracking-tight">
                      {agent.published_version_id ? 'Published' : 'Draft'}
                    </span>
                  </div>
                </td>

                <td className="px-5 py-4">
                  <div className="text-[10px] text-[#8A8A7A] font-mono">
                    {new Date(agent.created_at).toLocaleDateString('vi-VN')}
                  </div>
                </td>

                <td className="px-5 py-4 text-right">
                  <div className="flex items-center justify-end gap-1">
                    <button
                      onClick={() => handlePublishAgent(agent)}
                      disabled={publishing === agent.id}
                      className="px-2.5 py-1 text-[10px] font-bold rounded-lg border border-emerald-300 text-emerald-700 hover:bg-emerald-50 disabled:opacity-50 transition-colors"
                      title="Publish"
                    >
                      {publishing === agent.id ? '…' : 'Publish'}
                    </button>
                    <button
                      onMouseDown={(e) => e.stopPropagation()}
                      onClick={(e) => { e.stopPropagation(); handleMenuOpen(e, agent.id); }}
                      className="p-1.5 hover:bg-[#F3E2A7] rounded-lg transition-colors text-[#5A5A5A] hover:text-[#111111]"
                    >
                      <MoreVertical className="w-4 h-4" />
                    </button>
                  </div>
                </td>
              </motion.tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Portal dropdown â€” rendered outside overflow containers */}
      {openMenuId && ReactDOM.createPortal(
        <div
          onMouseDown={(e) => e.stopPropagation()}
          style={{ position: 'fixed', top: menuPos.top, right: menuPos.right, zIndex: 9999 }}
          className="w-44 bg-white border border-[#BFA66A]/50 rounded-xl shadow-xl overflow-hidden animate-in fade-in slide-in-from-top-1 duration-100"
        >
          {apiAgents.filter(a => a.id === openMenuId).map(agent => (
            <React.Fragment key={agent.id}>
              <button
                onClick={() => { setOpenMenuId(null); handleEditAgent(agent); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-semibold text-[#2A2A2A] hover:bg-[#FFF9E8] transition-colors"
              >
                <Settings2 className="w-3.5 h-3.5 text-[#8A8A7A]" /> Edit Canvas
              </button>
              <div className="h-px bg-[#ECE7DA] mx-3" />
              <button
                onClick={() => { setOpenMenuId(null); void handleDeleteAgent(agent); }}
                className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-semibold text-red-600 hover:bg-red-50 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" /> Delete
              </button>
            </React.Fragment>
          ))}
        </div>,
        document.body
      )}

    </div>
  );

  if (activeTab === 'BUILDER') {
    return (
      <WorkflowBuilder
        onClose={handleBuilderClose}
        workflow={selectedWorkflow}
        template={selectedTemplate}
        agentId={builderAgentId}
        workflowVersionId={builderWorkflowVersionId}
        workflowId={builderWorkflowId}
        sourceVersionId={builderSourceVersionId}
      />
    );
  }

  if (activeTab === 'TEMPLATE_PICKER') {
    return (
      <TemplatePicker
        onSelect={handleTemplateSelect}
        onClose={() => setActiveTab('REGISTRY')}
      />
    );
  }

  const renderContent = () => {
    switch (activeTab) {
      case 'EXECUTIONS':    return <ExecutionCenter key={filterWorkflowName ?? 'all'} initialSearch={filterWorkflowName} />;
      case 'SCHEDULING':    return <SchedulingCenter />;
      case 'OBSERVABILITY': return <WorkflowObservability />;
      default:              return renderRegistry();
    }
  };

  return (
    <div className="space-y-4">

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

      {/* Unified header panel */}
      <div className="bg-white border border-[#ECE7DA] rounded-2xl overflow-hidden">

        {/* Tab bar + action in one row */}
        <div className="flex items-center border-b border-[#ECE7DA] px-6">
          <div className="flex items-center flex-1 overflow-x-auto">
            {WORKFLOW_TABS.map(({ id, label, Icon }) => (
              <button
                key={id}
                onClick={() => { if (id !== 'EXECUTIONS') setFilterWorkflowName(undefined); setActiveTab(id); }}
                className={`flex items-center gap-1.5 px-4 py-3.5 text-[11px] font-semibold whitespace-nowrap border-b-2 transition-colors ${
                  activeTab === id
                    ? 'border-[#B88719] text-[#B88719]'
                    : 'border-transparent text-[#8A8A7A] hover:text-[#2A2A2A] hover:border-[#D4CBBA]'
                }`}
              >
                <Icon className="w-3.5 h-3.5" />
                {label}
              </button>
            ))}
          </div>
          <div className="pl-4 py-2 shrink-0">
            <button onClick={handleNewWorkflow} className="btn-primary">
              <Plus className="w-4 h-4" />
              New Workflow
            </button>
          </div>
        </div>

        {/* Search + filters â€” Registry only */}
        {activeTab === 'REGISTRY' && (
          <div className="px-6 pt-4 pb-4 flex items-center gap-3">
            <div className="flex-1 relative">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A8A7A]" />
              <input
                type="text"
                value={registrySearch}
                onChange={e => setRegistrySearch(e.target.value)}
                placeholder="Tìm agent theo tên hoặc mô tả…"
                aria-label="Tìm agent"
                className="w-full bg-[#FAFAF5] border border-[#BFA66A] rounded-xl py-2 pl-10 pr-4 text-sm text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#8A5A00]"
              />
            </div>
          </div>
        )}
      </div>

      {/* Tab content */}
      <motion.div
        key={activeTab}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.15 }}
      >
        {renderContent()}
      </motion.div>

      {/* Create Agent Modal */}
      {showCreateModal && (
        <div
          className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40"
          onClick={() => { if (!creating) setShowCreateModal(false); }}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4"
            onClick={e => e.stopPropagation()}
          >
            <h3 className="font-bold text-[#111111]">New Workflow</h3>
            <div className="space-y-1">
              <label className="text-xs font-semibold text-[#5F5F5F]">Tên agent</label>
              <input
                autoFocus
                type="text"
                value={createName}
                onChange={e => setCreateName(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && handleCreateAgent()}
                placeholder="VD: Customer Support Bot"
                className="ui-input w-full px-3 py-2 text-sm"
              />
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setShowCreateModal(false)} className="btn-ghost" disabled={creating}>Hủy</button>
              <button
                onClick={handleCreateAgent}
                disabled={creating || !createName.trim()}
                className="px-4 py-2 text-sm font-semibold text-white bg-[#B88719] hover:bg-[#8A5A00] disabled:opacity-50 rounded-lg"
              >
                {creating ? 'Đang tạo…' : 'Tạo'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default WorkflowEngine;
