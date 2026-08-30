/**
 * Run Registry — a "run" is one recorded conversation with an agent.
 *
 *   GET /api/conversations?limit&offset&status&agent_id&q
 *   GET /api/catalog/agents (agent filter)
 *
 * Click a row to open the read-only transcript drawer shared with Conversations.
 */
import React, { useState } from 'react';
import { Search, RefreshCw, Inbox } from 'lucide-react';
import { Pagination } from '../../shared/Pagination';
import {
  Badge, toneFor, Button, EmptyState, ErrorState, LoadingState, Input, Select, SectionTitle, Table, type Column,
} from '../../ui';
import { useConversations, useCatalogAgents, shortId, type ConversationItem } from '../../../lib/platformApi';
import { formatDateTime, formatNumber } from '../../../lib/formatVN';
import { ConversationDrawer, conversationDuration } from '../Conversations';

const PAGE_SIZE = 20;

export const RunRegistry = () => {
  const [searchDraft, setSearchDraft] = useState('');
  const [q, setQ] = useState('');
  const [agentId, setAgentId] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<ConversationItem | null>(null);

  const agents = useCatalogAgents();
  const { data, loading, error, refetch } = useConversations({
    limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, q, agent_id: agentId, status,
  });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const columns: Column<ConversationItem>[] = [
    { header: 'Run ID', cell: c => <span className="font-mono text-[11px] text-[#8A5A00] font-semibold">{shortId(c.id)}</span> },
    { header: 'Agent', cell: c => c.agent_name ?? <span className="text-[#5F5F5F]">—</span> },
    { header: 'Người dùng', cell: c => <span className="text-[#3F3F3F]">{c.user_ref ?? '—'}</span> },
    { header: 'Bắt đầu', cell: c => <span className="text-[#3F3F3F]">{formatDateTime(c.started_at)}</span> },
    { header: 'Thời lượng', cell: c => <span className="text-[#3F3F3F]">{conversationDuration(c)}</span> },
    { header: 'Tin nhắn', align: 'right', cell: c => formatNumber(c.message_count) },
    { header: 'Tokens', align: 'right', cell: c => c.tokens == null ? '—' : formatNumber(c.tokens) },
    { header: 'Trạng thái', cell: c => <Badge tone={toneFor(c.status)}>{c.status}</Badge> },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Runs"
        subtitle="Mỗi run là một hội thoại đã được runtime ghi lại. Bấm vào một dòng để xem transcript và tool calls."
        actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void refetch()} loading={loading && !!data}>Làm mới</Button>}
      />

      <form className="flex items-center gap-3 flex-wrap" onSubmit={e => { e.preventDefault(); setPage(1); setQ(searchDraft.trim()); }}>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
          <Input value={searchDraft} onChange={e => setSearchDraft(e.target.value)} placeholder="Tìm theo ID, người dùng…" className="pl-9 w-64" aria-label="Tìm run" />
        </div>
        <Select value={agentId} onChange={e => { setAgentId(e.target.value); setPage(1); }} aria-label="Lọc theo agent" className="w-56">
          <option value="">Tất cả agent</option>
          {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
        <Select value={status} onChange={e => { setStatus(e.target.value); setPage(1); }} aria-label="Lọc theo trạng thái" className="w-40">
          <option value="">Mọi trạng thái</option>
          <option value="active">Đang chạy</option>
          <option value="ended">Đã kết thúc</option>
        </Select>
        <Button type="submit" size="sm" icon={Search}>Tìm</Button>
        <span className="text-xs text-[#5F5F5F] ml-auto">{formatNumber(total)} runs</span>
      </form>

      <div className="content-card content-card--static overflow-hidden">
        {loading && !data && <LoadingState label="Đang tải runs…" />}
        {error && <div className="p-4"><ErrorState message={error} onRetry={() => void refetch()} /></div>}
        {!error && data && (
          <Table<ConversationItem>
            rows={items}
            columns={columns}
            rowKey={c => c.id}
            onRowClick={setSelected}
            empty={
              <EmptyState
                icon={Inbox}
                title="Chưa có run nào"
                description={q || agentId || status ? 'Không có run nào khớp bộ lọc.' : 'Chạy agent trong Playground hoặc qua API để thấy run ở đây.'}
              />
            }
          />
        )}
      </div>

      <Pagination page={page} totalPages={totalPages} hasNext={page < totalPages} onJump={setPage} className="justify-end" />

      <ConversationDrawer conversation={selected} onClose={() => setSelected(null)} />
    </div>
  );
};

