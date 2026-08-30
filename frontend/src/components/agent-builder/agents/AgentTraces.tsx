/**
 * Trace Explorer — tool-call traces recorded by the runtime.
 *
 *   GET /api/traces?limit&offset&status&q → { items, total }
 *
 * Row click opens the trace (input / output JSON, latency, status).
 */
import React, { useState } from 'react';
import { Activity, Search, RefreshCw, Inbox, Clock, MessageCircle } from 'lucide-react';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { Pagination } from '../../shared/Pagination';
import {
  Badge, toneFor, Button, EmptyState, ErrorState, LoadingState, Input, Select, SectionTitle, StatTile, Table, type Column,
} from '../../ui';
import { useTraces, shortId, type TraceItem } from '../../../lib/platformApi';
import { formatDateTime, formatDuration, formatNumber } from '../../../lib/formatVN';
import { asJson } from '../Conversations';

const PAGE_SIZE = 25;

function TraceDrawer({ trace, onClose }: { trace: TraceItem | null; onClose: () => void }) {
  return (
    <DetailDrawer
      isOpen={!!trace}
      onClose={onClose}
      title={trace ? `${trace.tool_name || 'trace'} · ${shortId(trace.id)}` : 'Trace'}
      subtitle={trace ? `Hội thoại ${shortId(trace.conversation_id)} · ${formatDateTime(trace.created_at)}` : undefined}
      icon={Activity}
      size="standard"
      persistKey="trace"
    >
      {trace && (
        <div className="p-5 space-y-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <StatTile label="Trạng thái" value={<Badge tone={toneFor(trace.status)}>{trace.status}</Badge>} />
            <StatTile label="Độ trễ" value={trace.latency_ms == null ? '—' : formatDuration(trace.latency_ms)} icon={Clock} />
            <StatTile label="Người dùng" value={<span className="text-sm">{trace.user_ref ?? '—'}</span>} />
            <StatTile label="Message" value={<span className="font-mono text-sm">{shortId(trace.message_id)}</span>} icon={MessageCircle} />
          </div>
          <div>
            <p className="text-[11px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1.5">Input</p>
            <pre className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap bg-[#FFFDF6] border border-[#BFA66A] rounded-xl p-3 max-h-72 overflow-auto text-[#111111]">{asJson(trace.input)}</pre>
          </div>
          <div>
            <p className="text-[11px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1.5">Output</p>
            <pre className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap bg-[#FFFDF6] border border-[#BFA66A] rounded-xl p-3 max-h-72 overflow-auto text-[#111111]">{asJson(trace.output)}</pre>
          </div>
        </div>
      )}
    </DetailDrawer>
  );
}

export const TraceExplorer = () => {
  const [searchDraft, setSearchDraft] = useState('');
  const [q, setQ] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<TraceItem | null>(null);

  const { data, loading, error, refetch } = useTraces({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, q, status });
  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const errorsOnPage = items.filter(t => toneFor(t.status) === 'failed').length;
  const latencies = items.map(t => t.latency_ms).filter((v): v is number => typeof v === 'number');
  const avgLatency = latencies.length ? latencies.reduce((a, b) => a + b, 0) / latencies.length : null;

  const columns: Column<TraceItem>[] = [
    { header: 'Thời điểm', cell: t => <span className="text-[#3F3F3F]">{formatDateTime(t.created_at)}</span> },
    { header: 'Tool', cell: t => <span className="font-mono text-[11px] text-[#8A5A00] font-semibold">{t.tool_name || '—'}</span> },
    { header: 'Trạng thái', cell: t => <Badge tone={toneFor(t.status)}>{t.status}</Badge> },
    { header: 'Độ trễ', align: 'right', cell: t => t.latency_ms == null ? '—' : formatDuration(t.latency_ms) },
    { header: 'Hội thoại', cell: t => <span className="font-mono text-[11px] text-[#3F3F3F]">{shortId(t.conversation_id)}</span> },
    { header: 'Người dùng', cell: t => <span className="text-[#3F3F3F]">{t.user_ref ?? '—'}</span> },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Traces"
        subtitle="Mọi lần gọi tool trong hội thoại: input, output, độ trễ và trạng thái."
        actions={<Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void refetch()} loading={loading && !!data}>Làm mới</Button>}
      />

      <div className="grid grid-cols-3 gap-4">
        <StatTile label="Tổng traces" value={formatNumber(total)} icon={Activity} loading={loading && !data} />
        <StatTile label="Lỗi (trang này)" value={formatNumber(errorsOnPage)} tone={errorsOnPage > 0 ? 'bad' : 'ok'} loading={loading && !data} />
        <StatTile label="Độ trễ TB (trang này)" value={avgLatency == null ? '—' : formatDuration(avgLatency)} icon={Clock} loading={loading && !data} />
      </div>

      <form className="flex items-center gap-3 flex-wrap" onSubmit={e => { e.preventDefault(); setPage(1); setQ(searchDraft.trim()); }}>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#5F5F5F]" />
          <Input value={searchDraft} onChange={e => setSearchDraft(e.target.value)} placeholder="Tìm theo tool, ID hội thoại, người dùng…" className="pl-9 w-72" aria-label="Tìm trace" />
        </div>
        <Select value={status} onChange={e => { setStatus(e.target.value); setPage(1); }} aria-label="Lọc theo trạng thái" className="w-40">
          <option value="">Mọi trạng thái</option>
          <option value="success">Thành công</option>
          <option value="error">Lỗi</option>
        </Select>
        <Button type="submit" size="sm" icon={Search}>Tìm</Button>
        <span className="text-xs text-[#5F5F5F] ml-auto">{formatNumber(total)} traces</span>
      </form>

      <div className="content-card content-card--static overflow-hidden">
        {loading && !data && <LoadingState label="Đang tải traces…" />}
        {error && <div className="p-4"><ErrorState message={error} onRetry={() => void refetch()} /></div>}
        {!error && data && (
          <Table<TraceItem>
            rows={items}
            columns={columns}
            rowKey={t => t.id}
            onRowClick={setSelected}
            empty={
              <EmptyState
                icon={Inbox}
                title="Chưa có trace"
                description={q || status ? 'Không có trace nào khớp bộ lọc.' : 'Traces xuất hiện khi agent gọi tool (KB search, MCP, sandbox…) trong hội thoại.'}
              />
            }
          />
        )}
      </div>

      <Pagination page={page} totalPages={totalPages} hasNext={page < totalPages} onJump={setPage} className="justify-end" />

      <TraceDrawer trace={selected} onClose={() => setSelected(null)} />
    </div>
  );
};
