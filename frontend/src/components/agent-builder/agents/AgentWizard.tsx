import React, { useState } from 'react';
import { X, ArrowRight, Database, Network, User, Layers, Loader2 } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { TEMPLATE_FLOWS } from '../workflow/BuilderTemplates';
import { apiMutate } from '../../../lib/apiClient';


type TemplateChoice = 'blank' | 'rag' | 'multi-agent' | 'hitl';

const TEMPLATES: Array<{ id: TemplateChoice; label: string; desc: string; Icon: any }> = [
  { id: 'blank',       label: 'Blank',       desc: 'Canvas rỗng, tự kéo node vào.',                    Icon: Layers   },
  { id: 'rag',         label: 'RAG',          desc: 'Planner → KB Search → Reranker → Responder.',       Icon: Database },
  { id: 'multi-agent', label: 'Multi-Agent',  desc: 'KB Search + MCP Tool chạy song song.',              Icon: Network  },
  { id: 'hitl',        label: 'HITL',         desc: 'Có Human Approval trước khi trả về kết quả.',       Icon: User     },
];

export interface CreatedAgent {
  agentId: string;
  workflowId: string;
  workflowVersionId: string;
  agentVersionId: string;
  name: string;
  initialNodes: any[];
  initialEdges: any[];
}

interface WizardProps {
  onCancel: () => void;
  onComplete: (data: CreatedAgent) => void;
  initialStep?: 1 | 2;
  initialName?: string;
}

function serializeTemplate(templateId: TemplateChoice) {
  if (templateId === 'blank') return { nodes: [], edges: [] };
  const flow = TEMPLATE_FLOWS[templateId];
  return {
    nodes: flow.nodes.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
    edges: flow.edges.map(e => ({
      id: e.id, source: e.source, target: e.target,
      type: e.type, label: (e as any).label ?? null,
    })),
  };
}

export const NewAgentWizard: React.FC<WizardProps> = ({ onCancel, onComplete, initialStep = 1, initialName = '' }) => {
  const [step, setStep]               = useState<1 | 2>(initialStep);
  const [name, setName]               = useState(initialName);
  const [description, setDescription] = useState('');
  const [selected, setSelected]       = useState<TemplateChoice | null>(null);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState<string | null>(null);

  const handleCreate = async () => {
    if (!selected) return;
    setLoading(true);
    setError(null);

    try {
      const agentData = await apiMutate<any>('POST', '/api/agents', { name: name.trim(), description: description.trim() });
      const { agent_id, workflow_id, workflow_version_id, agent_version_id } = agentData;

      // Canvas không save lên DB ở đây — giống AgentDetailPage.handleCreateWorkflow.
      // Builder nhận initialNodes/initialEdges, user phải bấm Save mới lưu vào DB.
      const { nodes, edges } = serializeTemplate(selected);

      onComplete({
        agentId: agent_id,
        workflowId: workflow_id,
        workflowVersionId: workflow_version_id,
        agentVersionId: agent_version_id,
        name: name.trim(),
        initialNodes: nodes,
        initialEdges: edges,
      });
    } catch (e: any) {
      setError(e.message ?? 'Lỗi không xác định');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6">
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onCancel}
      />

      <motion.div
        initial={{ opacity: 0, y: 16, scale: 0.97 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 16, scale: 0.97 }}
        transition={{ type: 'spring', stiffness: 380, damping: 32 }}
        className="relative w-full max-w-lg bg-white border border-[#ECE7DA] rounded-2xl shadow-2xl flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 pt-6 pb-4 border-b border-[#ECE7DA]">
          <div>
            <p className="text-[9px] font-black text-[#8A8A7A] uppercase tracking-widest mb-0.5">
              {step === 1 ? 'Bước 1 / 2' : 'Bước 2 / 2'}
            </p>
            <h2 className="text-sm font-bold text-[#111111]">
              {step === 1 ? 'Thông tin agent' : 'Chọn workflow template'}
            </h2>
          </div>
          <button
            onClick={onCancel}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-[#8A8A7A] hover:bg-[#F3E2A7] transition-all"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Body */}
        <div className="px-6 py-5">
          <AnimatePresence mode="wait">
            {step === 1 ? (
              <motion.div
                key="step1"
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -12 }}
                transition={{ duration: 0.15 }}
                className="space-y-4"
              >
                <div>
                  <label className="text-[9px] font-black text-[#8A8A7A] uppercase tracking-widest block mb-1.5">
                    Tên agent <span className="text-red-500">*</span>
                  </label>
                  <input
                    autoFocus
                    type="text"
                    value={name}
                    onChange={e => setName(e.target.value)}
                    onKeyDown={e => { if (e.key === 'Enter' && name.trim()) setStep(2); }}
                    placeholder="VD: Chatbot tra cứu phạt nguội"
                    className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl px-4 py-2.5 text-sm text-[#111111] placeholder:text-[#AFAFAF] focus:outline-none focus:border-[#BFA66A] transition-colors"
                  />
                </div>
                <div>
                  <label className="text-[9px] font-black text-[#8A8A7A] uppercase tracking-widest block mb-1.5">
                    Mô tả
                  </label>
                  <textarea
                    value={description}
                    onChange={e => setDescription(e.target.value)}
                    placeholder="Mục đích của agent này..."
                    rows={3}
                    className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl px-4 py-2.5 text-sm text-[#111111] placeholder:text-[#AFAFAF] focus:outline-none focus:border-[#BFA66A] transition-colors resize-none"
                  />
                </div>
              </motion.div>
            ) : (
              <motion.div
                key="step2"
                initial={{ opacity: 0, x: 12 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 12 }}
                transition={{ duration: 0.15 }}
                className="grid grid-cols-2 gap-3"
              >
                {TEMPLATES.map(t => (
                  <button
                    key={t.id}
                    onClick={() => setSelected(t.id)}
                    className={`text-left p-4 rounded-xl border transition-all ${
                      selected === t.id
                        ? 'border-[#BFA66A] bg-[#FFF9E8]'
                        : 'border-[#ECE7DA] bg-[#FAFAF5] hover:bg-[#FFF9E8]'
                    }`}
                  >
                    <div className={`mb-2 ${selected === t.id ? 'text-[#B88719]' : 'text-[#8A8A7A]'}`}>
                      <t.Icon className="w-5 h-5" />
                    </div>
                    <div className="text-xs font-bold text-[#111111] mb-1">{t.label}</div>
                    <div className="text-[10px] text-[#8A8A7A] leading-relaxed">{t.desc}</div>
                  </button>
                ))}
              </motion.div>
            )}
          </AnimatePresence>

          {error && (
            <p className="mt-4 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              {error}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 pb-6 flex items-center justify-between">
          <button
            onClick={() => step === 1 ? onCancel() : setStep(1)}
            className="text-xs font-semibold text-[#8A8A7A] hover:text-[#111111] transition-colors px-3 py-2"
          >
            {step === 1 ? 'Huỷ' : '← Quay lại'}
          </button>

          {step === 1 ? (
            <button
              onClick={() => setStep(2)}
              disabled={!name.trim()}
              className="flex items-center gap-1.5 px-4 py-2 bg-[#B88719] hover:bg-[#9A7015] disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-bold rounded-xl transition-all"
            >
              Tiếp theo <ArrowRight className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              onClick={handleCreate}
              disabled={!selected || loading}
              className="flex items-center gap-1.5 px-4 py-2 bg-[#B88719] hover:bg-[#9A7015] disabled:opacity-40 disabled:cursor-not-allowed text-white text-xs font-bold rounded-xl transition-all"
            >
              {loading ? (
                <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Đang tạo...</>
              ) : (
                'Tạo agent'
              )}
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
};
