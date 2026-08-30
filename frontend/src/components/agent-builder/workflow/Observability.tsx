/**
 * Workflow Observability — platform metrics + service health.
 *
 *   GET /api/platform/metrics?days=N  → tiles + daily series
 *   GET /api/platform/services        → service health list
 */
import React, { useState } from 'react';
import { Activity, Clock, Zap, MessageSquare, AlertTriangle, Layers, RefreshCw, Server, Inbox } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, AreaChart, Area, Legend } from 'recharts';
import { cn } from '../../../lib/utils';
import { usePlatformMetrics, usePlatformServices, type ServiceHealth } from '../../../lib/platformApi';
import {
  Badge, toneFor, Button, Card, EmptyState, ErrorState, LoadingState, SectionTitle, StatTile, Table, type Column,
} from '../../ui';
import { formatCompact, formatDate, formatDuration, formatNumber, formatPercent } from '../../../lib/formatVN';

const RANGES: { days: number; label: string }[] = [
  { days: 1, label: '24 giờ' },
  { days: 7, label: '7 ngày' },
  { days: 30, label: '30 ngày' },
];

const TOOLTIP_STYLE = { backgroundColor: '#FFFFFF', border: '1px solid #A8945E', borderRadius: '12px', fontSize: '12px', color: '#111111' };

const WorkflowObservability = () => {
  const [days, setDays] = useState(7);
  const metrics = usePlatformMetrics(days);
  const services = usePlatformServices();
  const m = metrics.data;
  const series = (m?.series ?? []).map(p => ({ ...p, label: formatDate(p.date) }));
  const svc = services.data?.items ?? [];
  const unhealthy = svc.filter(s => s.status !== 'ok').length;

  const serviceColumns: Column<ServiceHealth>[] = [
    { header: 'Service', cell: s => <span className="font-semibold text-[#111111]">{s.name}</span> },
    { header: 'Loại', cell: s => <span className="font-mono text-[11px] text-[#3F3F3F]">{s.kind}</span> },
    { header: 'Trạng thái', cell: s => <Badge tone={toneFor(s.status)}>{s.status}</Badge> },
    { header: 'Độ trễ', align: 'right', cell: s => s.latency_ms == null ? '—' : formatDuration(s.latency_ms) },
    { header: 'Chi tiết', cell: s => <span className="text-[#3F3F3F]">{s.detail ?? '—'}</span> },
  ];

  return (
    <div className="space-y-5">
      <SectionTitle
        title="Observability"
        subtitle="Khối lượng hội thoại, tokens, độ trễ, tỷ lệ lỗi và sức khoẻ service."
        actions={
          <>
            <div className="flex p-1 bg-[#F4E8C3] border border-[#BFA66A] rounded-xl gap-1">
              {RANGES.map(r => (
                <button
                  key={r.days}
                  onClick={() => setDays(r.days)}
                  aria-pressed={days === r.days}
                  className={cn('px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-widest transition-all',
                    days === r.days ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#3F3F3F] hover:text-[#111111]')}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => { void metrics.refetch(); void services.refetch(); }} loading={metrics.loading && !!m}>Làm mới</Button>
          </>
        }
      />

      {metrics.error && <ErrorState message={metrics.error} onRetry={() => void metrics.refetch()} />}

      <div className="grid grid-cols-2 lg:grid-cols-3 xl:grid-cols-6 gap-4">
        <StatTile label="Hội thoại 24h" value={m ? formatNumber(m.conversations.last_24h) : '—'} hint={m ? `${formatNumber(m.conversations.active)} đang mở` : undefined} icon={MessageSquare} loading={metrics.loading && !m} />
        <StatTile label="Tin nhắn 24h" value={m ? formatNumber(m.messages.last_24h) : '—'} icon={Layers} loading={metrics.loading && !m} />
        <StatTile label="Tokens 24h" value={m ? formatCompact(m.tokens.last_24h) : '—'} icon={Zap} loading={metrics.loading && !m} />
        <StatTile label="Độ trễ TB" value={m?.latency_ms.avg == null ? '—' : formatDuration(m.latency_ms.avg)} icon={Clock} loading={metrics.loading && !m} />
        <StatTile label="Độ trễ p95" value={m?.latency_ms.p95 == null ? '—' : formatDuration(m.latency_ms.p95)} icon={Activity} loading={metrics.loading && !m} />
        <StatTile label="Tỷ lệ lỗi" value={m?.error_rate == null ? '—' : formatPercent(m.error_rate)} tone={m?.error_rate ? 'bad' : 'ok'} icon={AlertTriangle} loading={metrics.loading && !m} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <Card title="Khối lượng" subtitle="Hội thoại, tin nhắn và lỗi theo ngày">
          {metrics.loading && !m && <LoadingState />}
          {m && series.length === 0 && <EmptyState icon={Inbox} title="Chưa có dữ liệu" description="Chưa có hội thoại nào trong khoảng thời gian này." />}
          {series.length > 0 && (
            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={series}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#BFA66A" vertical={false} />
                  <XAxis dataKey="label" stroke="#5F5F5F" fontSize={10} axisLine={false} tickLine={false} />
                  <YAxis stroke="#5F5F5F" fontSize={10} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Bar dataKey="conversations" name="Hội thoại" fill="#B88719" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="messages" name="Tin nhắn" fill="#0E5F63" radius={[4, 4, 0, 0]} />
                  <Bar dataKey="errors" name="Lỗi" fill="#A83A32" radius={[4, 4, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>

        <Card title="Độ trễ & tokens" subtitle="Độ trễ trung bình (ms) và tokens theo ngày">
          {metrics.loading && !m && <LoadingState />}
          {m && series.length === 0 && <EmptyState icon={Inbox} title="Chưa có dữ liệu" description="Độ trễ xuất hiện khi có tin nhắn của assistant." />}
          {series.length > 0 && (
            <div className="h-56 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={series}>
                  <defs>
                    <linearGradient id="obsLatency" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#B88719" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#B88719" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#BFA66A" vertical={false} />
                  <XAxis dataKey="label" stroke="#5F5F5F" fontSize={10} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="ms" stroke="#5F5F5F" fontSize={10} axisLine={false} tickLine={false} />
                  <YAxis yAxisId="tk" orientation="right" stroke="#5F5F5F" fontSize={10} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={TOOLTIP_STYLE} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Area yAxisId="ms" type="monotone" dataKey="avg_latency_ms" name="Độ trễ TB (ms)" stroke="#B88719" strokeWidth={2} fill="url(#obsLatency)" />
                  <Area yAxisId="tk" type="monotone" dataKey="tokens" name="Tokens" stroke="#0E5F63" strokeWidth={2} fillOpacity={0} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </Card>
      </div>

      <Card
        title="Sức khoẻ service"
        subtitle={svc.length ? `${svc.length - unhealthy}/${svc.length} service ok` : undefined}
        padding="none"
        actions={<Server className="w-4 h-4 text-[#8A5A00]" />}
      >
        {services.loading && svc.length === 0 && <LoadingState label="Đang kiểm tra service…" />}
        {services.error && <div className="p-3"><ErrorState message={services.error} onRetry={() => void services.refetch()} /></div>}
        {!services.loading && !services.error && (
          <Table<ServiceHealth>
            rows={svc}
            columns={serviceColumns}
            rowKey={s => s.name}
            empty={<EmptyState icon={Server} title="Không có service nào được báo cáo" description="Endpoint /api/platform/services chưa trả về service nào." />}
          />
        )}
      </Card>
    </div>
  );
};

export default WorkflowObservability;
