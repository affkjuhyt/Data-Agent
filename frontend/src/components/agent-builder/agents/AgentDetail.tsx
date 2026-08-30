/**
 * AgentDetailView — compact quick view of one agent (rendered in a drawer).
 *
 *   GET /api/agents/{id}                       → identity + draft config
 *   GET /api/llm-providers, /api/system-prompts → names for the ids
 *   GET /api/conversations?agent_id&limit=8    → recent runs
 *
 * Full editing lives in AgentDetailPage (onOpenConfig).
 */
import React from 'react';
import { Bot, Cpu, FileText, MemoryStick, GitBranch, Zap, Inbox, Settings2 } from 'lucide-react';
import {
  useAgentDetail, useApi, useConversations, shortId,
  type ConversationItem, type LLMProvider, type SystemPrompt,
} from '../../../lib/platformApi';
import { Badge, toneFor, Button, Card, EmptyState, ErrorState, LoadingState, Table, type Column } from '../../ui';
import { formatDateTime, formatNumber } from '../../../lib/formatVN';
import { conversationDuration } from '../Conversations';

interface AgentDetailProps {
  agentId: string;
  onOpenConfig: () => void;
  onViewRuns: () => void;
}

const FieldRow = ({ icon: Icon, label, value }: { icon: React.ElementType; label: string; value: React.ReactNode }) => (
  <div className="flex items-start gap-3 py-2.5 border-b border-[#BFA66A] last:border-b-0">
    <div className="w-8 h-8 rounded-lg bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center shrink-0">
      <Icon className="w-4 h-4 text-[#8A5A00]" />
    </div>
    <div className="min-w-0">
      <div className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wider">{label}</div>
      <div className="text-sm text-[#111111] break-words">{value}</div>
    </div>
  </div>
);

export const AgentDetailView: React.FC<AgentDetailProps> = ({ agentId, onOpenConfig, onViewRuns }) => {
  const agent = useAgentDetail(agentId);
  const providers = useApi<LLMProvider[]>('/api/llm-providers');
  const prompts = useApi<SystemPrompt[]>('/api/system-prompts');
  const runs = useConversations({ agent_id: agentId, limit: 8, offset: 0 });

  if (agent.loading && !agent.data) return <div className="p-5"><LoadingState label="Đang tải agent…" /></div>;
  if (agent.error || !agent.data) return <div className="p-5"><ErrorState message={agent.error ?? 'Không tìm thấy agent'} onRetry={() => void agent.refetch()} /></div>;

  const a = agent.data;
  const provider = (providers.data ?? []).find(p => p.id === a.responder_model_id);
  const prompt = (prompts.data ?? []).find(p => p.id === a.system_prompt_id);
  const isPublished = !!a.published_version_id;

  const columns: Column<ConversationItem>[] = [
    { header: 'Run', cell: c => <span className="font-mono text-[11px] text-[#8A5A00] font-semibold">{shortId(c.id)}</span> },
    { header: 'Bắt đầu', cell: c => <span className="text-[#3F3F3F]">{formatDateTime(c.started_at)}</span> },
    { header: 'Thời lượng', cell: c => <span className="text-[#3F3F3F]">{conversationDuration(c)}</span> },
    { header: 'Tin nhắn', align: 'right', cell: c => formatNumber(c.message_count) },
    { header: 'Trạng thái', cell: c => <Badge tone={toneFor(c.status)}>{c.status}</Badge> },
  ];

  return (
    <div className="p-5 space-y-5">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-2xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center text-[#8A5A00] font-bold text-xl font-display shrink-0">
          {a.name.charAt(0).toUpperCase()}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-lg font-bold text-[#111111] font-display leading-tight">{a.name}</h3>
            <Badge tone={isPublished ? 'healthy' : 'draft'}>{isPublished ? 'Published' : 'Draft'}</Badge>
          </div>
          <p className="text-[12px] text-[#3F3F3F] mt-1">{a.description || 'Chưa có mô tả.'}</p>
          <p className="text-[10px] font-mono text-[#5F5F5F] mt-1">{a.id} · tạo {formatDateTime(a.created_at)}</p>
        </div>
        <Button size="sm" variant="primary" icon={Settings2} onClick={onOpenConfig}>Mở trang cấu hình</Button>
      </div>

      <Card title="Cấu hình hiện tại" subtitle="Đọc từ draft version của agent" padding="sm">
        <FieldRow icon={Cpu} label="Model" value={provider ? <span className="font-mono text-[12px]">{provider.name} ({provider.model_id})</span> : <span className="text-[#5F5F5F]">Chưa chọn</span>} />
        <FieldRow icon={FileText} label="System prompt" value={prompt?.name ?? <span className="text-[#5F5F5F]">Chưa chọn</span>} />
        <FieldRow icon={MemoryStick} label="Memory" value={a.memory_enabled ? 'Bật' : 'Tắt'} />
        <FieldRow icon={GitBranch} label="Workflow đang chạy" value={a.active_workflow_name ?? <span className="text-[#5F5F5F]">Chưa publish workflow nào</span>} />
        <FieldRow icon={Bot} label="Versions" value={<span className="font-mono text-[11px]">published {shortId(a.published_version_id)} · draft {shortId(a.draft_version_id)}</span>} />
      </Card>

      <Card
        title="Runs gần đây"
        subtitle={runs.data ? `${formatNumber(runs.data.total)} run tổng cộng` : undefined}
        padding="none"
        actions={<Button size="sm" variant="ghost" icon={Zap} onClick={onViewRuns}>Xem tất cả</Button>}
      >
        {runs.loading && !runs.data && <LoadingState label="Đang tải runs…" />}
        {runs.error && <div className="p-3"><ErrorState message={runs.error} onRetry={() => void runs.refetch()} /></div>}
        {runs.data && (
          <Table<ConversationItem>
            rows={runs.data.items}
            columns={columns}
            rowKey={c => c.id}
            empty={<EmptyState icon={Inbox} title="Chưa có run" description="Agent này chưa có hội thoại nào được ghi lại." />}
          />
        )}
      </Card>
    </div>
  );
};
