/**
 * Conversations — every chat session the runtime recorded.
 *
 *   GET /api/conversations?limit&offset&status&q&agent_id   (list, server-side filters)
 *   GET /api/conversations/{id}/messages                    (transcript + tool traces)
 *   GET /api/catalog/agents                                  (agent filter options)
 *
 * The drawer is read-only: it shows what happened, it does not send.
 */
import React, { useState } from 'react';
import {
  Search, ChevronDown, ChevronRight, Clock, Zap, Bot, User, MessageCircle,
  Users, Wrench, Inbox, RefreshCw,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { DetailDrawer } from '../shared/DetailDrawer';
import { Pagination } from '../shared/Pagination';
import {
  Badge, toneFor, Button, EmptyState, ErrorState, LoadingState, Input, Select, StatTile, Table, type Column,
} from '../ui';
import {
  useConversations, useConversationMessages, useCatalogAgents, durationMs, shortId,
  type ConversationItem, type ConversationMessage, type MessageTrace,
} from '../../lib/platformApi';
import { formatDateTime, formatDuration, formatNumber, formatTime } from '../../lib/formatVN';

const PAGE_SIZE = 20;

export const asJson = (v: unknown): string => {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch { return String(v); }
};

export const conversationDuration = (c: ConversationItem): string => {
  const ms = durationMs(c.started_at, c.ended_at);
  return ms === null ? '—' : formatDuration(ms);
};

/* ─── Tool call card (from message.traces) ───────────────── */
function ToolCallCard({ trace }: { trace: MessageTrace }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2 border border-[#BFA66A] rounded-lg overflow-hidden text-[11px] bg-white">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 bg-[#FFF9E8] hover:bg-[#F4E8C3] transition-colors text-left"
      >
        <Wrench size={11} className="text-[#8A5A00]" />
        <span className="font-mono text-[#8A5A00] font-semibold">{trace.tool_name || `trace #${trace.trace_index}`}</span>
        <Badge tone={toneFor(trace.status)}>{trace.status}</Badge>
        {trace.latency_ms != null && <span className="text-[#5F5F5F]">{formatDuration(trace.latency_ms)}</span>}
        <span className="ml-auto text-[#5F5F5F]">{open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}</span>
      </button>
      {open && (
        <div className="px-2.5 py-2 space-y-2 border-t border-[#BFA66A]">
          <div>
            <p className="text-[10px] text-[#5F5F5F] mb-0.5 uppercase tracking-wider font-bold">Input</p>
            <pre className="text-[#111111] font-mono text-[10px] leading-relaxed whitespace-pre-wrap bg-[#FFFDF6] rounded p-1.5 max-h-48 overflow-auto">{asJson(trace.input)}</pre>
          </div>
          <div>
            <p className="text-[10px] text-[#5F5F5F] mb-0.5 uppercase tracking-wider font-bold">Output</p>
            <pre className="text-[#111111] font-mono text-[10px] leading-relaxed whitespace-pre-wrap bg-[#FFFDF6] rounded p-1.5 max-h-48 overflow-auto">{asJson(trace.output)}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

/* ─── Chat bubble ─────────────────────────────────────────── */
function MsgBubble({ msg }: { msg: ConversationMessage }) {
  const isUser = msg.role === 'user';
  return (
    <div className={cn('flex gap-2.5', isUser && 'flex-row-reverse')}>
      <div className={cn('w-7 h-7 rounded-full flex items-center justify-center shrink-0 mt-0.5 border border-[#BFA66A]',
        isUser ? 'bg-[#F4E8C3]' : 'bg-[#FFF9E8]')}>
        {isUser ? <User size={12} className="text-[#8A5A00]" /> : <Bot size={12} className="text-[#3F3F3F]" />}
      </div>
      <div className={cn('max-w-[80%] flex flex-col gap-1', isUser ? 'items-end' : 'items-start')}>
        <div className={cn('rounded-xl px-3 py-2 text-xs leading-relaxed border border-[#BFA66A] whitespace-pre-wrap',
          isUser ? 'bg-[#F4E8C3] text-[#111111] rounded-tr-sm' : 'bg-white text-[#111111] rounded-tl-sm')}>
          {msg.content || <span className="text-[#5F5F5F] italic">(không có nội dung)</span>}
          {msg.traces?.map(t => <ToolCallCard key={`${msg.id}-${t.trace_index}`} trace={t} />)}
        </div>
        <div className="flex items-center gap-2 px-1 text-[10px] text-[#5F5F5F]">
          <span>{formatTime(msg.created_at, true)}</span>
          {msg.role !== 'user' && msg.role !== 'assistant' && <span className="font-mono">{msg.role}</span>}
          {msg.tokens_used != null && <span className="flex items-center gap-0.5"><Zap size={9} /> {formatNumber(msg.tokens_used)} tk</span>}
          {msg.latency_ms != null && <span className="flex items-center gap-0.5"><Clock size={9} /> {formatDuration(msg.latency_ms)}</span>}
        </div>
      </div>
    </div>
  );
}

/* ─── Drawer (read-only transcript) — shared with RunRegistry ─── */
export function ConversationDrawer({ conversation, onClose }: { conversation: ConversationItem | null; onClose: () => void }) {
  const { data, loading, error, refetch } = useConversationMessages(conversation?.id ?? null);
  const messages = data?.messages ?? [];

  return (
    <DetailDrawer
      isOpen={!!conversation}
      onClose={onClose}
      title={conversation ? `Hội thoại ${shortId(conversation.id)}` : 'Hội thoại'}
      subtitle={conversation ? `${conversation.agent_name ?? 'Agent không xác định'} · ${conversation.user_ref ?? '—'}` : undefined}
      icon={MessageCircle}
      size="standard"
      persistKey="conversation"
    >
      {conversation && (
        <>
          <div className="px-5 py-2.5 border-b border-[#BFA66A] bg-[#FFF9E8] flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[#3F3F3F]">
            <span>Bắt đầu: <b className="text-[#111111]">{formatDateTime(conversation.started_at)}</b></span>
            <span>Thời lượng: <b className="text-[#111111]">{conversationDuration(conversation)}</b></span>
            <span>{formatNumber(conversation.message_count)} tin nhắn</span>
            {conversation.tokens != null && <span>{formatNumber(conversation.tokens)} tokens</span>}
            {conversation.avg_latency_ms != null && <span>TB {formatDuration(conversation.avg_latency_ms)}</span>}
            {conversation.channel && <span className="font-mono">{conversation.channel}</span>}
            <span className="ml-auto"><Badge tone={toneFor(conversation.status)}>{conversation.status}</Badge></span>
          </div>
          <div className="px-4 py-5 space-y-4">
            {loading && <LoadingState label="Đang tải transcript…" />}
            {!loading && error && <ErrorState message={error} onRetry={() => void refetch()} />}
            {!loading && !error && messages.length === 0 && (
              <EmptyState icon={Inbox} title="Chưa có tin nhắn" description="Hội thoại này chưa ghi nhận tin nhắn nào." />
            )}
            {!loading && !error && messages.map(m => <MsgBubble key={m.id} msg={m} />)}
          </div>
        </>
      )}
    </DetailDrawer>
  );
}

/* ─── Main ────────────────────────────────────────────────── */
export default function Conversations() {
  const [searchDraft, setSearchDraft] = useState('');
  const [q, setQ] = useState('');
  const [agentId, setAgentId] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [active, setActive] = useState<ConversationItem | null>(null);

  const agents = useCatalogAgents();
  const { data, loading, error, refetch } = useConversations({
    limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, q, agent_id: agentId, status,
  });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const activeOnPage = items.filter(c => c.status === 'active').length;
  const distinctUsers = new Set(items.map(c => c.user_ref).filter(Boolean)).size;
  const avgMessages = items.length ? Math.round(items.reduce((s, c) => s + (c.message_count ?? 0), 0) / items.length) : 0;
  const tokensOnPage = items.reduce((s, c) => s + (c.tokens ?? 0), 0);

  const columns: Column<ConversationItem>[] = [
    { header: 'ID', cell: c => <span className="font-mono text-[11px] text-[#8A5A00] font-semibold">{shortId(c.id)}</span> },
    { header: 'Agent', cell: c => c.agent_name ?? <span className="text-[#5F5F5F]">—</span> },
    { header: 'Người dùng', cell: c => <span className="text-[#3F3F3F]">{c.user_ref ?? '—'}</span> },
    { header: 'Kênh', cell: c => <span className="font-mono text-[11px] text-[#3F3F3F]">{c.channel ?? '—'}</span> },
    { header: 'Bắt đầu', cell: c => <span className="text-[#3F3F3F]">{formatDateTime(c.started_at)}</span> },
    { header: 'Thời lượng', cell: c => <span className="text-[#3F3F3F]">{conversationDuration(c)}</span> },
    { header: 'Tin nhắn', align: 'right', cell: c => formatNumber(c.message_count) },
    { header: 'Tokens', align: 'right', cell: c => c.tokens == null ? '—' : formatNumber(c.tokens) },
    { header: 'Trạng thái', cell: c => <Badge tone={toneFor(c.status)}>{c.status}</Badge> },
  ];

  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Tổng hội thoại" value={formatNumber(total)} icon={MessageCircle} loading={loading && !data} />
        <StatTile label="Đang hoạt động (trang này)" value={formatNumber(activeOnPage)} icon={Zap} tone={activeOnPage > 0 ? 'ok' : 'neutral'} loading={loading && !data} />
        <StatTile label="Người dùng (trang này)" value={formatNumber(distinctUsers)} icon={Users} loading={loading && !data} />
        <StatTile label="TB tin nhắn / phiên" value={formatNumber(avgMessages)} hint={tokensOnPage ? `${formatNumber(tokensOnPage)} tokens` : undefined} icon={Bot} loading={loading && !data} />
      </div>

      <form
        className="flex items-center gap-3 flex-wrap"
        onSubmit={e => { e.preventDefault(); setPage(1); setQ(searchDraft.trim()); }}
      >
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
          <Input
            value={searchDraft}
            onChange={e => setSearchDraft(e.target.value)}
            placeholder="Tìm theo ID, người dùng…"
            className="pl-9 w-64"
            aria-label="Tìm hội thoại"
          />
        </div>
        <Select value={agentId} onChange={e => { setAgentId(e.target.value); setPage(1); }} aria-label="Lọc theo agent" className="w-56">
          <option value="">Tất cả agent</option>
          {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
        </Select>
        <Select value={status} onChange={e => { setStatus(e.target.value); setPage(1); }} aria-label="Lọc theo trạng thái" className="w-40">
          <option value="">Mọi trạng thái</option>
          <option value="active">Đang hoạt động</option>
          <option value="ended">Đã kết thúc</option>
        </Select>
        <Button type="submit" size="sm" icon={Search}>Tìm</Button>
        <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void refetch()} loading={loading && !!data}>Làm mới</Button>
        <span className="text-xs text-[#5F5F5F] ml-auto">{formatNumber(total)} hội thoại</span>
      </form>

      <div className="content-card content-card--static overflow-hidden">
        {loading && !data && <LoadingState label="Đang tải hội thoại…" />}
        {error && <div className="p-4"><ErrorState message={error} onRetry={() => void refetch()} /></div>}
        {!error && data && (
          <Table<ConversationItem>
            rows={items}
            columns={columns}
            rowKey={c => c.id}
            onRowClick={setActive}
            empty={
              <EmptyState
                icon={Inbox}
                title="Chưa có hội thoại"
                description={q || agentId || status ? 'Không có hội thoại nào khớp bộ lọc.' : 'Khi người dùng bắt đầu chat với agent, hội thoại sẽ xuất hiện ở đây.'}
              />
            }
          />
        )}
      </div>

      <Pagination page={page} totalPages={totalPages} hasNext={page < totalPages} onJump={setPage} className="justify-end" />

      <ConversationDrawer conversation={active} onClose={() => setActive(null)} />
    </div>
  );
}
