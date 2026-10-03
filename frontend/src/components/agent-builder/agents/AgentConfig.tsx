/**
 * Config Registry — the real per-agent configuration, side by side.
 *
 *   GET /api/agents            → list
 *   GET /api/agents/{id}       → responder_model / system_prompt_id / memory_enabled / active workflow
 *   GET /api/llm-providers     → model names
 *   GET /api/system-prompts    → prompt names
 *
 * "Cấu hình" opens the agent's real settings page (AgentDetailPage) via onSelect;
 * "Xem nhanh" opens the compact AgentDetailView drawer via onQuickView.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { Search, RefreshCw, Settings2, Eye, Bot } from 'lucide-react';
import { apiGet } from '../../../lib/apiClient';
import {
  errorMessage, type AgentDetail, type AgentSummary, type LLMProvider, type SystemPrompt,
} from '../../../lib/platformApi';
import {
  Badge, toneFor, Button, EmptyState, ErrorState, LoadingState, Input, SectionTitle, Table, type Column,
} from '../../ui';
import { formatDate } from '../../../lib/formatVN';

interface ConfigRegistryProps {
  onSelect: (agentId: string) => void;
  onQuickView: (agentId: string) => void;
}

type Row = AgentSummary & Partial<Pick<AgentDetail, 'responder_model' | 'system_prompt_id' | 'memory_enabled' | 'active_workflow_name' | 'active_workflow_id'>>;

export const ConfigRegistry: React.FC<ConfigRegistryProps> = ({ onSelect, onQuickView }) => {
  const [rows, setRows] = useState<Row[]>([]);
  const [providers, setProviders] = useState<LLMProvider[]>([]);
  const [prompts, setPrompts] = useState<SystemPrompt[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, prov, prom] = await Promise.all([
        apiGet<AgentSummary[]>('/api/agents'),
        apiGet<LLMProvider[]>('/api/llm-providers').catch(() => [] as LLMProvider[]),
        apiGet<SystemPrompt[]>('/api/system-prompts').catch(() => [] as SystemPrompt[]),
      ]);
      const agents = Array.isArray(list) ? list : [];
      const details = await Promise.all(
        agents.map(a => apiGet<AgentDetail>(`/api/agents/${a.id}`).catch(() => null)),
      );
      setRows(agents.map((a, i) => ({ ...a, ...(details[i] ?? {}) })));
      setProviders(Array.isArray(prov) ? prov : []);
      setPrompts(Array.isArray(prom) ? prom : []);
    } catch (e) {
      setError(errorMessage(e));
      setRows([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // responder_model is the LiteLLM alias, which /api/llm-providers lists as model_id.
  const providerName = (alias: string | null | undefined) => {
    if (!alias) return null;
    return providers.find(x => x.model_id === alias)?.name ?? alias;
  };
  const promptName = (id: string | null | undefined) => {
    if (!id) return null;
    return prompts.find(x => x.id === id)?.name ?? id;
  };

  const q = query.trim().toLowerCase();
  const visible = rows.filter(r => !q || `${r.name} ${r.description ?? ''} ${r.id}`.toLowerCase().includes(q));

  const columns: Column<Row>[] = [
    {
      header: 'Agent',
      cell: r => (
        <div>
          <div className="font-semibold text-[#111111]">{r.name}</div>
          <div className="text-[11px] text-[#5F5F5F] line-clamp-1">{r.description || '—'}</div>
        </div>
      ),
    },
    { header: 'Model', cell: r => <span className="font-mono text-[11px] text-[#111111]">{providerName(r.responder_model) ?? <span className="text-[#5F5F5F]">Chưa chọn</span>}</span> },
    { header: 'System prompt', cell: r => <span className="text-[#3F3F3F]">{promptName(r.system_prompt_id) ?? <span className="text-[#5F5F5F]">Chưa chọn</span>}</span> },
    { header: 'Memory', cell: r => <Badge tone={r.memory_enabled ? 'healthy' : 'draft'}>{r.memory_enabled ? 'Bật' : 'Tắt'}</Badge> },
    { header: 'Workflow đang chạy', cell: r => <span className="text-[#3F3F3F]">{r.active_workflow_name ?? '—'}</span> },
    { header: 'Trạng thái', cell: r => <Badge tone={toneFor(r.published_version_id ? 'published' : 'draft')}>{r.published_version_id ? 'Published' : 'Draft'}</Badge> },
    { header: 'Tạo', cell: r => <span className="text-[#3F3F3F]">{formatDate(r.created_at)}</span> },
    {
      header: '',
      align: 'right',
      cell: r => (
        <div className="flex items-center justify-end gap-1.5" onClick={e => e.stopPropagation()}>
          <Button size="sm" variant="ghost" icon={Eye} onClick={() => onQuickView(r.id)}>Xem nhanh</Button>
          <Button size="sm" icon={Settings2} onClick={() => onSelect(r.id)}>Cấu hình</Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Config Registry"
        subtitle="Model, system prompt, memory và workflow đang chạy của từng agent — đọc từ draft version hiện tại."
        actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void load()} loading={loading && rows.length > 0}>Làm mới</Button>}
      />

      <div className="relative w-80">
        <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
        <Input value={query} onChange={e => setQuery(e.target.value)} placeholder="Tìm agent…" className="pl-9" aria-label="Tìm agent" />
      </div>

      <div className="content-card content-card--static overflow-hidden">
        {loading && rows.length === 0 && <LoadingState label="Đang tải cấu hình agent…" />}
        {error && <div className="p-4"><ErrorState message={error} onRetry={() => void load()} /></div>}
        {!loading && !error && (
          <Table<Row>
            rows={visible}
            columns={columns}
            rowKey={r => r.id}
            onRowClick={r => onQuickView(r.id)}
            empty={
              <EmptyState
                icon={Bot}
                title={q ? 'Không có agent nào khớp' : 'Chưa có agent'}
                description={q ? 'Thử từ khoá khác.' : 'Tạo agent từ tab Registry để thấy cấu hình ở đây.'}
              />
            }
          />
        )}
      </div>
    </div>
  );
};
