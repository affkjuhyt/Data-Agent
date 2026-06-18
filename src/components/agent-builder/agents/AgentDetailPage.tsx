import React, { useState, useEffect, useCallback, useRef } from 'react';
import ReactDOM from 'react-dom';
import {
  ArrowLeft, GitMerge, Plus, Settings2, Trash2, Upload,
  CheckCircle2, Clock, Edit3, Save, X, Zap, ChevronDown, RotateCcw,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import WorkflowBuilder from '../workflow/Builder';
import { TEMPLATE_FLOWS } from '../workflow/BuilderTemplates';
import type { TemplateId } from '../workflow/Builder';
import { FLOW_BUILDER_URL, WORKFLOW_RUNTIME_URL } from '../../../lib/config';

interface ApiWorkflow {
  id: string;
  name: string;
  draft_version_id: string | null;
  published_version_id: string | null;
  created_at: string;
}

interface ApiAgent {
  id: string;
  name: string;
  description: string;
  published_version_id: string | null;
  draft_version_id: string | null;
  created_at: string;
  active_workflow_id: string | null;
  active_workflow_name: string | null;
  workflows?: ApiWorkflow[];
}

interface LLMProvider { id: string; name: string; model_id: string; type: string; }
interface SystemPrompt { id: string; name: string; }

const TEMPLATE_OPTIONS: { id: TemplateId; label: string; desc: string }[] = [
  { id: 'blank',       label: 'Blank',          desc: 'Bắt đầu từ đầu' },
  { id: 'rag',         label: 'RAG',            desc: 'Planner → KB Search → Reranker → Responder' },
  { id: 'multi-agent', label: 'Multi-Agent',    desc: 'RAG + MCP Tool song song' },
  { id: 'hitl',        label: 'Human Approval', desc: 'RAG + MCP + Human review' },
];

type DetailTab = 'WORKFLOWS' | 'SETTINGS';
type BuilderState = {
  workflowVersionId: string;
  workflowName: string;
  workflowId?: string;
  isNewWorkflow?: boolean;
  isNewDraft?: boolean;
  initialNodes?: any[];
  initialEdges?: any[];
  sourceVersionId?: string;
} | null;

interface Props {
  agentId: string;
  onBack: () => void;
}

interface WorkflowVersion {
  id: string;
  version: number;
  status: 'draft' | 'published' | 'archived';
  changelog: string | null;
  created_at: string;
  published_at: string | null;
}

export const AgentDetailPage: React.FC<Props> = ({ agentId, onBack }) => {
  const [agent, setAgent]           = useState<ApiAgent | null>(null);
  const [activeTab, setActiveTab]   = useState<DetailTab>('WORKFLOWS');
  const [builder, setBuilder]       = useState<BuilderState>(null);
  const [publishing, setPublishing] = useState(false);
  const [toast, setToast]           = useState<string | null>(null);

  // Version history
  const [wfVersions, setWfVersions]               = useState<Record<string, WorkflowVersion[]>>({});
  const [deployDropdown, setDeployDropdown]        = useState<string | null>(null);
  const [deployBtnRect, setDeployBtnRect]          = useState<{ top: number; right: number } | null>(null);
  const [publishModal, setPublishModal]            = useState<{ versionId: string; mode: 'publish' | 'republish' } | null>(null);
  const [changelog, setChangelog]                  = useState('');
  const [deleteConfirm, setDeleteConfirm]          = useState<string | null>(null); // versionId
  const [deleteWfConfirm, setDeleteWfConfirm]      = useState<string | null>(null); // workflowId

  // Create workflow modal
  const [showCreateWf, setShowCreateWf]   = useState(false);
  const [newWfName, setNewWfName]         = useState('');
  const [newWfTemplate, setNewWfTemplate] = useState<TemplateId>('rag');
  const [creating, setCreating]           = useState(false);

  // Settings state
  const [llmProviders, setLlmProviders]   = useState<LLMProvider[]>([]);
  const [systemPrompts, setSystemPrompts] = useState<SystemPrompt[]>([]);
  const [settingsEdit, setSettingsEdit]   = useState(false);
  const [settingsForm, setSettingsForm]   = useState({
    name: '', description: '',
    responder_model_id: '', system_prompt_id: '', memory_enabled: false,
  });
  const [savingSettings, setSavingSettings] = useState(false);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const fetchVersions = useCallback(async (workflowId: string) => {
    const res = await fetch(`${FLOW_BUILDER_URL}/api/workflows/${workflowId}/versions`);
    const data = await res.json();
    setWfVersions(prev => ({ ...prev, [workflowId]: data }));
  }, []);


  const handleConfirmPublish = useCallback(async () => {
    if (!publishModal) return;
    const { versionId, mode } = publishModal;
    const endpoint = mode === 'republish'
      ? `${FLOW_BUILDER_URL}/api/workflow-versions/${versionId}/republish`
      : `${FLOW_BUILDER_URL}/api/workflow-versions/${versionId}/publish`;
    try {
      await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: mode === 'publish' ? JSON.stringify({ changelog }) : undefined,
      });
      showToast(mode === 'republish' ? '✓ Rollback thành công' : '✓ Published thành công');
      setPublishModal(null);
      setChangelog('');
      // Refetch agent + versions
      const r = await fetch(`${FLOW_BUILDER_URL}/api/agents/${agentId}`);
      const data: ApiAgent = await r.json();
      setAgent(data);
      for (const wf of data.workflows ?? []) {
        fetch(`${FLOW_BUILDER_URL}/api/workflows/${wf.id}/versions`)
          .then(rv => rv.json())
          .then(versions => setWfVersions(prev => ({ ...prev, [wf.id]: versions })))
          .catch(() => {});
      }
    } catch { showToast('Thao tác thất bại'); }
  }, [publishModal, changelog, agentId]);

  const fetchAgent = useCallback(async () => {
    const res = await fetch(`${FLOW_BUILDER_URL}/api/agents/${agentId}`);
    const data: ApiAgent = await res.json();
    setAgent(data);
    setSettingsForm(f => ({
      ...f,
      name: data.name,
      description: data.description || '',
    }));
    // Fetch versions cho tất cả workflows
    (data.workflows ?? []).forEach(wf => {
      fetch(`${FLOW_BUILDER_URL}/api/workflows/${wf.id}/versions`)
        .then(r => r.json())
        .then(versions => setWfVersions(prev => ({ ...prev, [wf.id]: versions })))
        .catch(() => {});
    });
  }, [agentId]);

  useEffect(() => { fetchAgent(); }, [fetchAgent]);

  useEffect(() => {
    if (!deployDropdown) return;
    const close = () => { setDeployDropdown(null); setDeployBtnRect(null); };
    document.addEventListener('scroll', close, true);
    document.addEventListener('mousedown', close);
    return () => {
      document.removeEventListener('scroll', close, true);
      document.removeEventListener('mousedown', close);
    };
  }, [deployDropdown]);

  useEffect(() => {
    fetch(`${WORKFLOW_RUNTIME_URL}/api/llm-providers`)
      .then(r => r.json()).then(setLlmProviders).catch(() => {});
    fetch(`${WORKFLOW_RUNTIME_URL}/api/system-prompts`)
      .then(r => r.json()).then(setSystemPrompts).catch(() => {});
  }, []);

  const handlePublish = async () => {
    if (!agent?.workflows?.length) { showToast('Chưa có workflow để publish'); return; }
    setPublishing(true);
    try {
      await fetch(`${FLOW_BUILDER_URL}/api/agents/${agentId}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workflow_id: agent.workflows[0].id }),
      });
      showToast('✓ Published thành công');
      fetchAgent();
    } catch { showToast('Publish thất bại'); }
    finally { setPublishing(false); }
  };

  const handleCreateWorkflow = async () => {
    if (!newWfName.trim()) return;
    setCreating(true);
    try {
      const res = await fetch(`${FLOW_BUILDER_URL}/api/agents/${agentId}/workflows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newWfName.trim(), description: '' }),
      });
      const data = await res.json();
      const wvId: string = data.workflow_version_id;
      const tpl = TEMPLATE_FLOWS[newWfTemplate];

      setShowCreateWf(false);
      setNewWfName('');
      await fetchAgent();
      setBuilder({
        workflowVersionId: wvId,
        workflowName: newWfName.trim(),
        workflowId: data.workflow_id,
        isNewWorkflow: true,
        initialNodes: tpl?.nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })) ?? [],
        initialEdges: tpl?.edges.map(e => ({ id: e.id, source: e.source, target: e.target, type: e.type, label: e.label })) ?? [],
      });
    } catch { showToast('Tạo workflow thất bại'); }
    finally { setCreating(false); }
  };

  const handleDeleteWorkflow = async () => {
    if (!deleteWfConfirm) return;
    try {
      const res = await fetch(`${FLOW_BUILDER_URL}/api/workflows/${deleteWfConfirm}`, { method: 'DELETE' });
      if (!res.ok) { const e = await res.json(); showToast(e.detail || 'Xóa thất bại'); return; }
      showToast('Đã xóa workflow');
      setDeleteWfConfirm(null);
      fetchAgent();
    } catch { showToast('Xóa thất bại'); }
  };

  const handleDeleteDraft = async () => {
    if (!deleteConfirm) return;
    try {
      const res = await fetch(`${FLOW_BUILDER_URL}/api/workflow-versions/${deleteConfirm}`, { method: 'DELETE' });
      if (!res.ok) { const e = await res.json(); showToast(e.detail || 'Xóa thất bại'); return; }
      showToast('Đã xóa draft');
      setDeleteConfirm(null);
      fetchAgent();
    } catch { showToast('Xóa thất bại'); }
  };

  const handleSaveSettings = async () => {
    setSavingSettings(true);
    try {
      await fetch(`${FLOW_BUILDER_URL}/api/agents/${agentId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(settingsForm),
      });
      showToast('✓ Đã lưu settings');
      setSettingsEdit(false);
      fetchAgent();
    } catch { showToast('Lưu thất bại'); }
    finally { setSavingSettings(false); }
  };

  // Open Builder
  if (builder) {
    return (
      <WorkflowBuilder
        onClose={() => { setBuilder(null); fetchAgent(); }}
        workflow={null}
        agentId={agentId}
        workflowVersionId={builder.workflowVersionId}
        workflowId={builder.workflowId}
        isNewWorkflow={builder.isNewWorkflow}
        isNewDraft={builder.isNewDraft}
        initialNodes={builder.initialNodes}
        initialEdges={builder.initialEdges}
        sourceVersionId={builder.sourceVersionId}
        onLeave={builder.isNewWorkflow ? async () => {
          if (builder.workflowId) {
            await fetch(`${FLOW_BUILDER_URL}/api/workflows/${builder.workflowId}`, { method: 'DELETE' });
          }
          setBuilder(null);
          setNewWfName(builder.workflowName ?? '');
          setShowCreateWf(true);
          fetchAgent();
        } : undefined}
      />
    );
  }

  if (!agent) return <div className="p-8 text-slate-400 text-sm">Đang tải...</div>;

  const isPublished = !!agent.published_version_id;

  return (
    <div className="flex flex-col min-h-[calc(100vh-120px)]">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#ECE7DA] bg-[#FAFAF5] shrink-0">
        <div className="flex items-center gap-3">
          <button onClick={onBack} className="p-2 hover:bg-[#F3E2A7] rounded-lg transition-colors text-[#8A8A7A]">
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h2 className="text-base font-bold text-[#111111]">{agent.name}</h2>
            <div className="flex items-center gap-2 mt-0.5">
              <div className={`w-1.5 h-1.5 rounded-full ${isPublished ? 'bg-emerald-500' : 'bg-gray-400'}`} />
              <span className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-wider">
                {isPublished ? 'Published' : 'Draft'}
              </span>
            </div>
          </div>
        </div>
        <button
          onClick={handlePublish}
          disabled={publishing}
          className="flex items-center gap-1.5 px-4 py-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white rounded-xl text-xs font-bold transition-colors"
        >
          <Upload className="w-3.5 h-3.5" />
          {publishing ? 'Publishing…' : 'Publish'}
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-[#ECE7DA] px-6 shrink-0 bg-white">
        {(['WORKFLOWS', 'SETTINGS'] as DetailTab[]).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-3 text-xs font-bold uppercase tracking-wider border-b-2 transition-colors ${
              activeTab === tab
                ? 'border-[#B88719] text-[#B88719]'
                : 'border-transparent text-[#8A8A7A] hover:text-[#111111]'
            }`}
          >
            {tab === 'WORKFLOWS' ? 'Workflows' : 'Settings'}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 p-6">

        {/* ── Workflows tab ── */}
        {activeTab === 'WORKFLOWS' && (
          <div>
            <div className="flex items-center justify-between mb-4">
              <span className="text-sm font-semibold text-[#2A2A2A]">
                {agent.workflows?.length ?? 0} workflow
              </span>
              <button
                onClick={() => { setNewWfName(''); setNewWfTemplate('rag'); setShowCreateWf(true); }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-[#B88719] hover:bg-[#9A7015] text-white rounded-lg text-xs font-bold transition-colors"
              >
                <Plus className="w-3.5 h-3.5" /> New Workflow
              </button>
            </div>

            <div className="space-y-4">
              {(agent.workflows ?? []).map(wf => {
                const versions   = wfVersions[wf.id] ?? [];
                const published  = versions.find(v => v.status === 'published');
                const draft      = versions.find(v => v.status === 'draft');
                const archived   = versions.filter(v => v.status === 'archived');
                const hasDraft   = !!draft;
                const isOpen     = deployDropdown === wf.id;

                return (
                  <div key={wf.id} className="warm-panel rounded-xl" onClick={() => isOpen && setDeployDropdown(null)}>
                    {/* Header */}
                    <div className="flex items-center justify-between px-4 py-3 border-b border-[#ECE7DA]">
                      <div className="flex items-center gap-2">
                        <GitMerge className="w-4 h-4 text-blue-500" />
                        <span className="text-sm font-semibold text-[#111111]">{wf.name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          onClick={async () => {
                            try {
                              const res = await fetch(`${FLOW_BUILDER_URL}/api/workflows/${wf.id}/versions`, { method: 'POST' });
                              if (!res.ok) { const e = await res.json(); showToast(e.detail || 'Thất bại'); return; }
                              const data = await res.json();
                              await fetchAgent();
                              setBuilder({
                                workflowVersionId: data.workflow_version_id,
                                workflowName: wf.name,
                                workflowId: wf.id,
                                isNewDraft: true,
                                sourceVersionId: data.source_version_id ?? undefined,
                              });
                            } catch { showToast('Thất bại'); }
                          }}
                          disabled={hasDraft}
                          title={hasDraft ? 'Publish draft trước khi tạo mới' : 'Tạo draft version mới'}
                          className="flex items-center gap-1 px-2.5 py-1.5 text-xs font-semibold border border-amber-300 text-amber-700 hover:bg-amber-50 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          <Plus className="w-3 h-3" /> New Draft
                        </button>
                        <button
                          onClick={() => setDeleteWfConfirm(wf.id)}
                          title="Xóa workflow"
                          className="p-1.5 border border-red-200 text-red-400 hover:bg-red-50 rounded-lg transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </div>

                    {/* Published row */}
                    {published ? (
                      <div className="flex items-center justify-between px-4 py-3 bg-emerald-50/50">
                        <div className="flex items-center gap-3">
                          <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                          <span className="text-xs font-bold text-emerald-700">v{published.version} · đang chạy</span>
                          {published.changelog && (
                            <span className="text-[11px] text-slate-400 italic max-w-[200px] truncate">"{published.changelog}"</span>
                          )}
                          {published.published_at && (
                            <span className="text-[10px] text-slate-400">{new Date(published.published_at).toLocaleDateString('vi-VN')}</span>
                          )}
                        </div>
                        {/* Deploy v cũ dropdown */}
                        {archived.length > 0 && (
                          <div>
                            <button
                              onMouseDown={e => e.stopPropagation()}
                              onClick={e => {
                                e.stopPropagation();
                                if (isOpen) {
                                  setDeployDropdown(null);
                                  setDeployBtnRect(null);
                                } else {
                                  const rect = e.currentTarget.getBoundingClientRect();
                                  setDeployDropdown(wf.id);
                                  setDeployBtnRect({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
                                }
                              }}
                              className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 border border-indigo-200 text-indigo-600 hover:bg-indigo-50 rounded-lg font-medium transition-colors"
                            >
                              <RotateCcw className="w-3 h-3" /> Deploy v cũ <ChevronDown className="w-3 h-3" />
                            </button>
                          </div>
                        )}
                      </div>
                    ) : (
                      <div className="px-4 py-3 text-xs text-slate-400 italic flex items-center gap-2">
                        <Clock className="w-3.5 h-3.5" /> Chưa có version nào được publish
                      </div>
                    )}

                    {/* Draft row */}
                    {draft && (
                      <div className="flex items-center justify-between px-4 py-3 border-t border-[#ECE7DA]">
                        <div className="flex items-center gap-2">
                          <Clock className="w-4 h-4 text-amber-500" />
                          <span className="text-xs font-bold text-amber-700">v{draft.version} · draft</span>
                        </div>
                        <div className="flex gap-2">
                          <button
                            onClick={() => setBuilder({ workflowVersionId: draft.id, workflowName: wf.name, workflowId: wf.id })}
                            className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 border border-[#BFA66A]/50 hover:bg-[#F3E2A7] text-[#5A5A5A] rounded-lg font-medium transition-colors"
                          >
                            <Edit3 className="w-3 h-3" /> Edit Canvas
                          </button>
                          <button
                            onClick={() => setPublishModal({ versionId: draft.id, mode: 'publish' })}
                            className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 border border-emerald-300 text-emerald-700 hover:bg-emerald-50 rounded-lg font-medium transition-colors"
                          >
                            <Upload className="w-3 h-3" /> Publish
                          </button>
                          <button
                            onClick={() => setDeleteConfirm(draft.id)}
                            className="flex items-center gap-1 text-[11px] px-2.5 py-1.5 border border-red-200 text-red-500 hover:bg-red-50 rounded-lg font-medium transition-colors"
                          >
                            <Trash2 className="w-3 h-3" />
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}

              {(!agent.workflows || agent.workflows.length === 0) && (
                <div className="text-center py-12 text-[#8A8A7A] text-sm">
                  Chưa có workflow. Bấm "+ New Workflow" để tạo.
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Settings tab ── */}
        {activeTab === 'SETTINGS' && (
          <div className="max-w-lg space-y-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-[#111111]">Agent Config</h3>
              {!settingsEdit ? (
                <button onClick={() => setSettingsEdit(true)} className="flex items-center gap-1.5 px-3 py-1.5 border border-[#BFA66A]/50 hover:bg-[#F3E2A7] text-[#5A5A5A] rounded-lg text-xs font-semibold">
                  <Edit3 className="w-3 h-3" /> Edit
                </button>
              ) : (
                <div className="flex gap-2">
                  <button onClick={() => setSettingsEdit(false)} className="px-3 py-1.5 border border-[#ECE7DA] text-[#8A8A7A] rounded-lg text-xs font-semibold hover:bg-[#F3E2A7]">
                    <X className="w-3 h-3" />
                  </button>
                  <button onClick={handleSaveSettings} disabled={savingSettings} className="flex items-center gap-1.5 px-3 py-1.5 bg-[#B88719] text-white rounded-lg text-xs font-bold disabled:opacity-50">
                    <Save className="w-3 h-3" /> {savingSettings ? 'Saving…' : 'Save'}
                  </button>
                </div>
              )}
            </div>

            {[
              { label: 'Name', key: 'name', type: 'text' },
              { label: 'Description', key: 'description', type: 'text' },
            ].map(({ label, key, type }) => (
              <div key={key}>
                <label className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1.5 block">{label}</label>
                {settingsEdit ? (
                  <input
                    type={type}
                    value={(settingsForm as any)[key]}
                    onChange={e => setSettingsForm(f => ({ ...f, [key]: e.target.value }))}
                    className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 px-3 text-sm text-[#111111] focus:outline-none focus:border-[#BFA66A]"
                  />
                ) : (
                  <div className="text-sm text-[#2A2A2A] py-2 px-3 bg-[#FAFAF5] rounded-xl border border-[#ECE7DA]">
                    {(agent as any)[key] || '—'}
                  </div>
                )}
              </div>
            ))}

            {/* Responder Model */}
            <div>
              <label className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1.5 block">Responder Model</label>
              {settingsEdit ? (
                <select
                  value={settingsForm.responder_model_id}
                  onChange={e => setSettingsForm(f => ({ ...f, responder_model_id: e.target.value }))}
                  className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 px-3 text-sm text-[#111111] focus:outline-none focus:border-[#BFA66A]"
                >
                  <option value="">— Chọn model —</option>
                  {llmProviders.filter(p => p.type === 'chat').map(p => (
                    <option key={p.id} value={p.id}>{p.name} ({p.model_id})</option>
                  ))}
                </select>
              ) : (
                <div className="text-sm text-[#2A2A2A] py-2 px-3 bg-[#FAFAF5] rounded-xl border border-[#ECE7DA]">
                  {llmProviders.find(p => p.id === settingsForm.responder_model_id)?.name || '—'}
                </div>
              )}
            </div>

            {/* System Prompt */}
            <div>
              <label className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1.5 block">System Prompt</label>
              {settingsEdit ? (
                <select
                  value={settingsForm.system_prompt_id}
                  onChange={e => setSettingsForm(f => ({ ...f, system_prompt_id: e.target.value }))}
                  className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 px-3 text-sm text-[#111111] focus:outline-none focus:border-[#BFA66A]"
                >
                  <option value="">— Chọn prompt —</option>
                  {systemPrompts.map(p => (
                    <option key={p.id} value={p.id}>{p.name}</option>
                  ))}
                </select>
              ) : (
                <div className="text-sm text-[#2A2A2A] py-2 px-3 bg-[#FAFAF5] rounded-xl border border-[#ECE7DA]">
                  {systemPrompts.find(p => p.id === settingsForm.system_prompt_id)?.name || '—'}
                </div>
              )}
            </div>

            {/* Memory */}
            <div className="flex items-center justify-between py-2 px-3 bg-[#FAFAF5] rounded-xl border border-[#ECE7DA]">
              <div>
                <div className="text-sm font-semibold text-[#2A2A2A]">Memory</div>
                <div className="text-[10px] text-[#8A8A7A]">Bật memory middleware</div>
              </div>
              <button
                onClick={() => settingsEdit && setSettingsForm(f => ({ ...f, memory_enabled: !f.memory_enabled }))}
                className={`w-10 h-5 rounded-full transition-colors ${settingsForm.memory_enabled ? 'bg-emerald-500' : 'bg-gray-300'} ${!settingsEdit ? 'cursor-default' : 'cursor-pointer'}`}
              >
                <div className={`w-4 h-4 bg-white rounded-full shadow transition-transform mx-0.5 ${settingsForm.memory_enabled ? 'translate-x-5' : 'translate-x-0'}`} />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Deploy v cũ — portal dropdown (escapes any overflow container) */}
      {deployDropdown && deployBtnRect && ReactDOM.createPortal(
        <div
          style={{ position: 'fixed', top: deployBtnRect.top, right: deployBtnRect.right, zIndex: 9999 }}
          className="w-72 bg-white border border-slate-200 rounded-xl shadow-2xl overflow-hidden"
          onMouseDown={e => e.stopPropagation()}
        >
          <div className="px-3 py-2 text-[10px] font-black text-slate-400 uppercase tracking-widest border-b border-slate-100">
            Chọn version để deploy
          </div>
          {(wfVersions[deployDropdown] ?? []).filter(v => v.status === 'archived').map(v => (
            <div key={v.id} className="flex items-center justify-between px-3 py-2.5 hover:bg-slate-50">
              <div>
                <span className="text-xs font-bold text-slate-700">v{v.version}</span>
                {v.changelog && <span className="text-[11px] text-slate-400 ml-2 italic">"{v.changelog}"</span>}
                {v.published_at && (
                  <div className="text-[10px] text-slate-400 mt-0.5">{new Date(v.published_at).toLocaleDateString('vi-VN')}</div>
                )}
              </div>
              <button
                onMouseDown={e => e.stopPropagation()}
                onClick={() => { setDeployDropdown(null); setDeployBtnRect(null); setPublishModal({ versionId: v.id, mode: 'republish' }); }}
                className="text-[11px] px-2.5 py-1 bg-indigo-500 hover:bg-indigo-600 text-white rounded-lg font-medium transition-colors"
              >
                Deploy
              </button>
            </div>
          ))}
        </div>,
        document.body
      )}

      {/* Create Workflow Modal */}
      <AnimatePresence>
        {showCreateWf && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-[9998] flex items-center justify-center bg-black/30 backdrop-blur-sm"
            onClick={() => setShowCreateWf(false)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} exit={{ scale: 0.95, opacity: 0 }}
              onClick={e => e.stopPropagation()}
              className="bg-white border border-[#ECE7DA] rounded-2xl shadow-2xl p-6 w-[480px]"
            >
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-base font-bold text-[#111111]">New Workflow</h3>
                <button onClick={() => setShowCreateWf(false)} className="p-1.5 hover:bg-[#F3E2A7] rounded-lg text-[#8A8A7A]">
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="mb-4">
                <label className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-1.5 block">Tên Workflow</label>
                <input
                  autoFocus
                  type="text"
                  value={newWfName}
                  onChange={e => setNewWfName(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter') handleCreateWorkflow(); }}
                  placeholder="VD: Workflow tra cứu biển số"
                  className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2.5 px-4 text-sm text-[#111111] focus:outline-none focus:border-[#BFA66A]"
                />
              </div>

              <div className="mb-5">
                <label className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-2 block">Template</label>
                <div className="grid grid-cols-2 gap-2">
                  {TEMPLATE_OPTIONS.map(t => (
                    <button
                      key={t.id}
                      onClick={() => setNewWfTemplate(t.id)}
                      className={`text-left p-3 rounded-xl border transition-colors ${
                        newWfTemplate === t.id
                          ? 'border-[#B88719] bg-[#FFF9E8]'
                          : 'border-[#ECE7DA] hover:border-[#BFA66A]/50 hover:bg-[#FAFAF5]'
                      }`}
                    >
                      <div className="text-xs font-bold text-[#111111]">{t.label}</div>
                      <div className="text-[10px] text-[#8A8A7A] mt-0.5">{t.desc}</div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-2">
                <button onClick={() => setShowCreateWf(false)} className="px-4 py-2 text-xs font-semibold text-[#8A8A7A] hover:bg-[#F3E2A7] rounded-xl">Huỷ</button>
                <button
                  onClick={handleCreateWorkflow}
                  disabled={!newWfName.trim() || creating}
                  className="px-4 py-2 text-xs font-bold bg-[#B88719] hover:bg-[#9A7015] text-white rounded-xl disabled:opacity-50"
                >
                  {creating ? 'Đang tạo…' : 'Tạo & Mở Builder'}
                </button>
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Publish / Rollback modal */}
      {publishModal && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <h3 className="font-bold text-slate-800">
              {publishModal.mode === 'republish' ? '↩ Rollback version này?' : '🚀 Publish version này?'}
            </h3>
            {publishModal.mode === 'publish' && (
              <div className="space-y-1">
                <label className="text-xs font-semibold text-slate-500">Changelog (tuỳ chọn)</label>
                <input
                  autoFocus
                  value={changelog}
                  onChange={e => setChangelog(e.target.value)}
                  placeholder="Mô tả thay đổi trong version này..."
                  className="w-full border border-slate-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-300"
                />
              </div>
            )}
            {publishModal.mode === 'republish' && (
              <p className="text-sm text-slate-500">Version hiện tại sẽ bị archive. Agent sẽ chạy version này ngay lập tức.</p>
            )}
            <div className="flex justify-end gap-2">
              <button onClick={() => { setPublishModal(null); setChangelog(''); }} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Hủy</button>
              <button
                onClick={handleConfirmPublish}
                className={`px-4 py-2 text-sm font-semibold text-white rounded-lg ${publishModal.mode === 'republish' ? 'bg-indigo-500 hover:bg-indigo-600' : 'bg-emerald-500 hover:bg-emerald-600'}`}
              >
                {publishModal.mode === 'republish' ? 'Rollback' : 'Publish'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete workflow confirm */}
      {deleteWfConfirm && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <h3 className="font-bold text-slate-800">Xóa workflow này?</h3>
            <p className="text-sm text-slate-500">Toàn bộ versions và canvas sẽ bị xóa khỏi database và không thể khôi phục.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeleteWfConfirm(null)} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Hủy</button>
              <button onClick={handleDeleteWorkflow} className="px-4 py-2 text-sm font-semibold text-white bg-red-500 hover:bg-red-600 rounded-lg">Xóa</button>
            </div>
          </div>
        </div>
      )}

      {/* Delete draft confirm */}
      {deleteConfirm && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/40">
          <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
            <h3 className="font-bold text-slate-800">Xóa draft này?</h3>
            <p className="text-sm text-slate-500">Draft sẽ bị xóa khỏi database và không thể khôi phục.</p>
            <div className="flex justify-end gap-2">
              <button onClick={() => setDeleteConfirm(null)} className="px-4 py-2 text-sm text-slate-500 hover:bg-slate-100 rounded-lg">Hủy</button>
              <button onClick={handleDeleteDraft} className="px-4 py-2 text-sm font-semibold text-white bg-red-500 hover:bg-red-600 rounded-lg">Xóa</button>
            </div>
          </div>
        </div>
      )}

      {/* Toast */}
      <AnimatePresence>
        {toast && (
          <motion.div
            initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 20 }}
            className="fixed bottom-6 right-6 z-[9999] bg-[#1e293b] text-white px-4 py-2.5 rounded-xl text-sm shadow-xl"
          >
            {toast}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default AgentDetailPage;
