/**
 * Execution Center — recorded executions (conversations) + the live sandbox queue.
 *
 *   GET /api/conversations?limit&offset&q&agent_id      (history)
 *   GET /api/conversations/{id}/messages                (trace panel: tool calls per message)
 *   GET /api/catalog/agents                             (agent filter)
 *   SandboxQueuePanel → GET /api/sandbox/status          (queue tab)
 */
import React, { useState } from 'react';
import { Search, History, X, Inbox, Wrench, Clock, MessageSquare, Zap, Activity } from 'lucide-react';
import { cn } from '../../../lib/utils';
import SandboxQueuePanel from './SandboxQueuePanel';
import { Pagination } from '../../shared/Pagination';
import {
  Badge, toneFor, Button, EmptyState, ErrorState, LoadingState, Input, Select, Table, type Column,
} from '../../ui';
import {
  useConversations, useConversationMessages, useCatalogAgents, shortId,
  type ConversationItem, type MessageTrace,
} from '../../../lib/platformApi';
import { formatDateTime, formatDuration, formatNumber, formatTime } from '../../../lib/formatVN';
import { asJson, conversationDuration } from '../Conversations';

const PAGE_SIZE = 20;

interface ExecutionCenterProps {
  initialSearch?: string;
}

type CenterTab = 'HISTORY' | 'QUEUE';

/* ─── Trace panel for one execution ─────────────────────── */
function TraceStep({ trace, at }: { trace: MessageTrace; at: string }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="relative pl-8">
      <span className={cn(
        'absolute left-1.5 top-3 w-3 h-3 rounded-full border-2 bg-white',
        toneFor(trace.status) === 'failed' ? 'border-[#9F1D1D]' : 'border-[#6B8E23]',
      )} />
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full text-left p-3 bg-[#FFFDF6] border border-[#BFA66A] rounded-xl hover:bg-[#FFF9E8] transition-colors"
      >
        <div className="flex items-center gap-2 flex-wrap">
          <Wrench className="w-3.5 h-3.5 text-[#8A5A00]" />
          <span className="font-mono text-[12px] font-semibold text-[#111111]">{trace.tool_name || `trace #${trace.trace_index}`}</span>
          <Badge tone={toneFor(trace.status)}>{trace.status}</Badge>
          <span className="ml-auto text-[10px] text-[#5F5F5F]">
            {formatTime(at, true)}{trace.latency_ms != null && ` · ${formatDuration(trace.latency_ms)}`}
          </span>
        </div>
        {open && (
          <div className="mt-2 grid grid-cols-1 gap-2">
            <pre className="text-[10px] font-mono whitespace-pre-wrap bg-white border border-[#BFA66A] rounded-lg p-2 max-h-40 overflow-auto text-[#111111]">{asJson(trace.input)}</pre>
            <pre className="text-[10px] font-mono whitespace-pre-wrap bg-white border border-[#BFA66A] rounded-lg p-2 max-h-40 overflow-auto text-[#111111]">{asJson(trace.output)}</pre>
          </div>
        )}
      </button>
    </li>
  );
}

function ExecutionTrace({ execution, onClose }: { execution: ConversationItem; onClose: () => void }) {
  const { data, loading, error, refetch } = useConversationMessages(execution.id);
  const messages = data?.messages ?? [];
  const steps = messages.flatMap(m => (m.traces ?? []).map(t => ({ trace: t, at: m.created_at, messageId: m.id })));
  const tokens = messages.reduce((s, m) => s + (m.tokens_used ?? 0), 0);

  return (
    <div className="flex flex-col h-full bg-white">
      <div className="p-4 border-b border-[#BFA66A] bg-[#FFF9E8] flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-[10px] font-bold text-[#8A5A00] uppercase tracking-widest">Execution</div>
          <h3 className="text-sm font-bold text-[#111111] font-mono truncate">{execution.id}</h3>
          <p className="text-[11px] text-[#3F3F3F]">{execution.agent_name ?? 'Agent không xác định'} · {execution.user_ref ?? '—'}</p>
        </div>
        <Button size="sm" variant="ghost" icon={X} onClick={onClose} aria-label="Đóng" />
      </div>

      <div className="flex-1 overflow-auto p-4 space-y-4">
        <div className="grid grid-cols-2 gap-2">
          {[
            { label: 'Thời lượng', value: conversationDuration(execution), icon: Clock },
            { label: 'Tin nhắn', value: formatNumber(execution.message_count), icon: MessageSquare },
            { label: 'Tokens', value: formatNumber(execution.tokens ?? tokens), icon: Zap },
            { label: 'Độ trễ TB', value: execution.avg_latency_ms == null ? '—' : formatDuration(execution.avg_latency_ms), icon: Activity },
          ].map(s => (
            <div key={s.label} className="p-3 bg-[#FFFDF6] border border-[#BFA66A] rounded-xl">
              <div className="flex items-center gap-1.5 text-[#5F5F5F] mb-1">
                <s.icon className="w-3.5 h-3.5 text-[#8A5A00]" />
                <span className="text-[9px] font-bold uppercase tracking-widest">{s.label}</span>
              </div>
              <div className="text-sm font-bold text-[#111111]">{s.value}</div>
            </div>
          ))}
        </div>

        {loading && !data && <LoadingState label="Đang tải trace…" />}
        {error && <ErrorState message={error} onRetry={() => void refetch()} />}
        {data && steps.length === 0 && (
          <EmptyState icon={Inbox} title="Không có tool call" description={`${formatNumber(messages.length)} tin nhắn, không có trace nào được ghi.`} />
        )}
        {steps.length > 0 && (
          <ol className="relative space-y-2">
            <span className="absolute left-[11px] top-3 bottom-3 w-px bg-[#BFA66A]" />
            {steps.map(s => <TraceStep key={`${s.messageId}-${s.trace.trace_index}`} trace={s.trace} at={s.at} />)}
          </ol>
        )}
      </div>
    </div>
  );
}

/* ─── Main ────────────────────────────────────────────────── */
const ExecutionCenter = ({ initialSearch }: ExecutionCenterProps = {}) => {
  const [tab, setTab] = useState<CenterTab>('HISTORY');
  const [selected, setSelected] = useState<ConversationItem | null>(null);
  const [searchDraft, setSearchDraft] = useState(initialSearch ?? '');
  const [q, setQ] = useState(initialSearch ?? '');
  const [agentId, setAgentId] = useState('');
  const [channel, setChannel] = useState('');
  const [page, setPage] = useState(1);

  const agents = useCatalogAgents();
  const { data, loading, error, refetch } = useConversations({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, q, agent_id: agentId });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const channels = Array.from(new Set(items.map(c => c.channel).filter((c): c is string => !!c)));
  const visible = channel ? items.filter(c => c.channel === channel) : items;

  const columns: Column<ConversationItem>[] = [
    {
      header: 'Execution / Agent',
      cell: c => (
        <div>
          <div className="font-mono text-[11px] text-[#8A5A00] font-semibold">{shortId(c.id, 12)}</div>
          <div className="text-[12px] text-[#111111]">{c.agent_name ?? '—'}</div>
        </div>
      ),
    },
    { header: 'Kênh / Người dùng', cell: c => <div><div className="font-mono text-[11px] text-[#3F3F3F]">{c.channel ?? '—'}</div><div className="text-[11px] text-[#5F5F5F]">{c.user_ref ?? '—'}</div></div> },
    { header: 'Trạng thái', cell: c => <Badge tone={toneFor(c.status)}>{c.status}</Badge> },
    { header: 'Thời lượng', cell: c => <span className="text-[#3F3F3F]">{conversationDuration(c)}</span> },
    { header: 'Tin nhắn', align: 'right', cell: c => formatNumber(c.message_count) },
    { header: 'Bắt đầu', align: 'right', cell: c => <span className="text-[#3F3F3F]">{formatDateTime(c.started_at)}</span> },
  ];

  const headerCopy: Record<CenterTab, { title: string; subtitle: string }> = {
    HISTORY: { title: 'Executions', subtitle: 'Hội thoại đã ghi lại và tool calls của từng lần chạy' },
    QUEUE:   { title: 'Sandbox Queue', subtitle: 'Pool container và các job execute_python / execute_sql đang chạy' },
  };

  return (
    <div className={cn('flex bg-[#FFFDF6] overflow-hidden rounded-2xl border border-[#BFA66A]', (selected || tab !== 'HISTORY') ? 'h-[680px]' : '')}>
      <div className="flex-1 flex flex-col min-w-0">
        <div className="p-5 border-b border-[#BFA66A] bg-white">
          <div className={cn('flex items-center justify-between gap-3', tab === 'HISTORY' && 'mb-4')}>
            <div className="flex items-center gap-3">
              <div className="p-2 bg-[#F4E8C3] rounded-xl border border-[#BFA66A]">
                <History className="w-5 h-5 text-[#8A5A00]" />
              </div>
              <div>
                <h2 className="text-lg font-display font-bold text-[#111111]">{headerCopy[tab].title}</h2>
                <p className="text-xs text-[#5F5F5F] mt-0.5">{headerCopy[tab].subtitle}</p>
              </div>
            </div>
            <div className="flex items-center gap-1 p-1 bg-[#F4E8C3] rounded-xl border border-[#BFA66A]">
              {(['HISTORY', 'QUEUE'] as CenterTab[]).map(t => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={cn(
                    'px-4 py-1.5 rounded-lg text-xs font-bold transition-all',
                    tab === t ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#3F3F3F] hover:text-[#111111]',
                  )}
                >
                  {t === 'HISTORY' ? 'History' : 'Queue'}
                </button>
              ))}
            </div>
          </div>

          {tab === 'HISTORY' && (
            <form className="flex items-center gap-2 flex-wrap" onSubmit={e => { e.preventDefault(); setPage(1); setQ(searchDraft.trim()); }}>
              <div className="relative flex-1 min-w-56">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#5F5F5F]" />
                <Input value={searchDraft} onChange={e => setSearchDraft(e.target.value)} placeholder="Tìm theo ID, người dùng…" className="pl-9" aria-label="Tìm execution" />
              </div>
              <Select value={agentId} onChange={e => { setAgentId(e.target.value); setPage(1); }} aria-label="Lọc theo agent" className="w-52">
                <option value="">Tất cả agent</option>
                {(agents.data ?? []).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </Select>
              <Select value={channel} onChange={e => setChannel(e.target.value)} aria-label="Lọc theo kênh" className="w-40">
                <option value="">Mọi kênh</option>
                {channels.map(c => <option key={c} value={c}>{c}</option>)}
              </Select>
              <Button type="submit" size="sm" icon={Search}>Tìm</Button>
            </form>
          )}
        </div>

        {tab === 'QUEUE' && <SandboxQueuePanel />}

        {tab === 'HISTORY' && (
          <>
            <div className="flex-1 overflow-auto bg-white">
              {loading && !data && <LoadingState label="Đang tải executions…" />}
              {error && <div className="p-4"><ErrorState message={error} onRetry={() => void refetch()} /></div>}
              {!error && data && (
                <Table<ConversationItem>
                  rows={visible}
                  columns={columns}
                  rowKey={c => c.id}
                  onRowClick={setSelected}
                  empty={
                    <div className="p-4">
                      <EmptyState
                        icon={Inbox}
                        title="Chưa có execution"
                        description={q || agentId || channel ? 'Không có execution nào khớp bộ lọc.' : 'Mỗi hội thoại với agent là một execution — chạy thử trong Playground để thấy ở đây.'}
                      />
                    </div>
                  }
                />
              )}
            </div>
            <div className="px-4 py-2.5 border-t border-[#BFA66A] bg-white flex items-center justify-between">
              <span className="text-xs text-[#5F5F5F]">{formatNumber(total)} executions</span>
              <Pagination page={page} totalPages={totalPages} hasNext={page < totalPages} onJump={setPage} />
            </div>
          </>
        )}
      </div>

      {tab === 'HISTORY' && selected && (
        <div className="w-[420px] shrink-0 border-l border-[#BFA66A]">
          <ExecutionTrace key={selected.id} execution={selected} onClose={() => setSelected(null)} />
        </div>
      )}
    </div>
  );
};

export default ExecutionCenter;
