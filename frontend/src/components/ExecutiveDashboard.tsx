/**
 * Executive dashboard — platform pulse for business users.
 *
 *   GET /api/platform/metrics?days=7  → agents, conversations, messages, tokens, latency, error rate, series
 *   GET /api/platform/services        → service health (issues = anything not ok)
 */
import React from 'react';
import {
  Activity, Bot, MessageSquare, Layers, Zap, Clock, AlertTriangle, Server, ArrowRight, RefreshCw, CheckCircle2, Inbox,
} from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from 'recharts';
import { OperationalHeader } from './shared/OperationalHeader';
import { useAppState } from '../AppStateContext';
import { usePlatformMetrics, usePlatformServices, type ServiceHealth, type PlatformMetrics } from '../lib/platformApi';
import { Badge, toneFor, Button, Card, EmptyState, ErrorState, LoadingState, StatTile } from './ui';
import { formatCompact, formatDate, formatDuration, formatNumber, formatPercent } from '../lib/formatVN';

interface Issue { id: string; severity: 'HIGH' | 'MEDIUM' | 'LOW'; label: string; detail: string }

/** Issues are derived, never literal: unhealthy services + a non-zero error rate. */
const deriveIssues = (services: ServiceHealth[], metrics: PlatformMetrics | null): Issue[] => {
  const issues: Issue[] = services
    .filter(s => s.status !== 'ok')
    .map(s => ({
      id: `svc-${s.name}`,
      severity: s.status === 'down' ? 'HIGH' : s.status === 'degraded' ? 'MEDIUM' : 'LOW',
      label: `${s.name} (${s.kind}) đang ${s.status}`,
      detail: s.detail ?? 'Không có chi tiết',
    }));
  if (metrics?.error_rate && metrics.error_rate > 0) {
    issues.push({
      id: 'error-rate',
      severity: metrics.error_rate >= 0.1 ? 'HIGH' : metrics.error_rate >= 0.02 ? 'MEDIUM' : 'LOW',
      label: `Tỷ lệ tool call lỗi ${formatPercent(metrics.error_rate)}`,
      detail: `${formatNumber(metrics.series.reduce((s, p) => s + p.errors, 0))} lỗi trong ${metrics.series.length} ngày`,
    });
  }
  const order = { HIGH: 0, MEDIUM: 1, LOW: 2 };
  return issues.sort((a, b) => order[a.severity] - order[b.severity]);
};

const SEVERITY_DOT: Record<Issue['severity'], string> = { HIGH: 'bg-[#9F1D1D]', MEDIUM: 'bg-[#B88719]', LOW: 'bg-[#8A8A7A]' };

export const ExecutiveDashboard = () => {
  const { navigate } = useAppState();
  const metrics = usePlatformMetrics(7);
  const services = usePlatformServices();
  const m = metrics.data;
  const svc = services.data?.items ?? [];
  const healthy = svc.filter(s => s.status === 'ok').length;
  const issues = deriveIssues(svc, m);
  const series = (m?.series ?? []).map(p => ({ ...p, label: formatDate(p.date) }));
  const loading = metrics.loading && !m;

  const overall = services.loading && svc.length === 0
    ? 'checking'
    : svc.some(s => s.status === 'down') ? 'down' : svc.some(s => s.status !== 'ok') ? 'degraded' : 'ok';

  return (
    <div className="space-y-6">
      <OperationalHeader
        title="Tổng quan vận hành"
        subtitle="Số liệu 7 ngày gần nhất từ runtime và sức khoẻ các service."
        breadcrumbs={[{ label: 'Vận hành' }, { label: 'Tổng quan' }]}
        status={<Badge tone={toneFor(overall)}>{overall === 'checking' ? 'đang kiểm tra' : overall}</Badge>}
        actions={
          <Button variant="secondary" icon={RefreshCw} onClick={() => { void metrics.refetch(); void services.refetch(); }} loading={metrics.loading && !!m}>
            Làm mới
          </Button>
        }
      />

      {metrics.error && <ErrorState message={metrics.error} onRetry={() => void metrics.refetch()} />}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Agent" value={m ? formatNumber(m.agents.total) : '—'} hint={m ? `${formatNumber(m.agents.published)} published` : undefined} icon={Bot} loading={loading} />
        <StatTile label="Hội thoại" value={m ? formatNumber(m.conversations.total) : '—'} hint={m ? `+${formatNumber(m.conversations.last_24h)} / 24h` : undefined} icon={MessageSquare} loading={loading} />
        <StatTile label="Tin nhắn" value={m ? formatNumber(m.messages.total) : '—'} hint={m ? `+${formatNumber(m.messages.last_24h)} / 24h` : undefined} icon={Layers} loading={loading} />
        <StatTile label="Tokens" value={m ? formatCompact(m.tokens.total) : '—'} hint={m ? `+${formatCompact(m.tokens.last_24h)} / 24h` : undefined} icon={Zap} loading={loading} />
        <StatTile label="Độ trễ TB" value={m?.latency_ms.avg == null ? '—' : formatDuration(m.latency_ms.avg)} hint={m?.latency_ms.p95 != null ? `p95 ${formatDuration(m.latency_ms.p95)}` : undefined} icon={Clock} loading={loading} />
        <StatTile label="Tỷ lệ lỗi" value={m?.error_rate == null ? '—' : formatPercent(m.error_rate)} tone={m?.error_rate ? 'bad' : 'ok'} icon={AlertTriangle} loading={loading} />
        <StatTile label="Service khoẻ" value={services.data ? `${healthy}/${svc.length}` : '—'} tone={svc.length && healthy === svc.length ? 'ok' : svc.length ? 'warn' : 'neutral'} icon={Server} loading={services.loading && !services.data} />
        <StatTile label="Hội thoại đang mở" value={m ? formatNumber(m.conversations.active) : '—'} icon={Activity} loading={loading} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Card title="Khối lượng theo ngày" subtitle="Hội thoại và tin nhắn, 7 ngày">
            {loading && <LoadingState />}
            {m && series.length === 0 && <EmptyState icon={Inbox} title="Chưa có dữ liệu" description="Chưa có hội thoại nào trong 7 ngày qua." />}
            {series.length > 0 && (
              <div className="h-[320px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={series}>
                    <defs>
                      <linearGradient id="execConv" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#B88719" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#B88719" stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id="execMsg" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#0E5F63" stopOpacity={0.2} />
                        <stop offset="95%" stopColor="#0E5F63" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#BFA66A" vertical={false} />
                    <XAxis dataKey="label" axisLine={false} tickLine={false} tick={{ fill: '#5F5F5F', fontSize: 11 }} dy={10} />
                    <YAxis axisLine={false} tickLine={false} tick={{ fill: '#5F5F5F', fontSize: 11 }} />
                    <Tooltip contentStyle={{ backgroundColor: '#FFFFFF', border: '1px solid #A8945E', borderRadius: '12px', color: '#111111' }} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    <Area type="monotone" dataKey="conversations" name="Hội thoại" stroke="#B88719" strokeWidth={2.5} fill="url(#execConv)" />
                    <Area type="monotone" dataKey="messages" name="Tin nhắn" stroke="#0E5F63" strokeWidth={2.5} fill="url(#execMsg)" />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}
          </Card>
        </div>

        <Card title="Sự cố đang mở" subtitle="Service không ok và tỷ lệ lỗi > 0">
          {services.loading && svc.length === 0 && <LoadingState label="Đang kiểm tra service…" />}
          {services.error && <ErrorState message={services.error} onRetry={() => void services.refetch()} />}
          {!services.loading && !services.error && issues.length === 0 && (
            <EmptyState icon={CheckCircle2} title="Không có sự cố" description="Mọi service đều ok và không ghi nhận tool call lỗi." />
          )}
          {issues.length > 0 && (
            <ul className="divide-y divide-[#BFA66A]">
              {issues.map(issue => (
                <li key={issue.id} className="py-3 first:pt-0 last:pb-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${SEVERITY_DOT[issue.severity]}`} />
                    <span className="text-[10px] font-bold uppercase tracking-widest text-[#5F5F5F]">{issue.severity}</span>
                  </div>
                  <p className="text-[13px] font-semibold text-[#111111] leading-snug">{issue.label}</p>
                  <p className="text-[11px] text-[#3F3F3F] mt-0.5">{issue.detail}</p>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>

      <Card
        title="AI Runtime"
        subtitle="Agent, hội thoại và độ trễ hiện tại"
        actions={<Button size="sm" variant="ghost" icon={ArrowRight} onClick={() => navigate('agents', 'TEAM')}>Xem runtime</Button>}
      >
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
          {[
            { label: 'Agent published', value: m ? `${formatNumber(m.agents.published)} / ${formatNumber(m.agents.total)}` : '—', icon: Bot },
            { label: 'Hội thoại 24h', value: m ? formatNumber(m.conversations.last_24h) : '—', icon: MessageSquare },
            { label: 'Độ trễ TB', value: m?.latency_ms.avg == null ? '—' : formatDuration(m.latency_ms.avg), icon: Clock },
            { label: 'Tỷ lệ thành công', value: m?.error_rate == null ? '—' : formatPercent(1 - m.error_rate), icon: Activity },
          ].map(x => (
            <div key={x.label} className="flex items-center gap-3 p-3.5 bg-[#FFFDF6] border border-[#BFA66A] rounded-2xl">
              <div className="p-2 bg-[#F4E8C3] rounded-lg border border-[#BFA66A] shrink-0">
                <x.icon className="w-4 h-4 text-[#8A5A00]" />
              </div>
              <div className="min-w-0">
                <div className="text-xl font-bold font-display text-[#111111] leading-tight">{x.value}</div>
                <div className="text-[10px] text-[#5F5F5F] font-medium truncate">{x.label}</div>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
};
