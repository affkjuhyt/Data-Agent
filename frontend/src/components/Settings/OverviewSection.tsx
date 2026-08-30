/**
 * Settings → Overview ("Platform Pulse").
 *
 * Every number on this page comes from a live endpoint:
 *   compliance score   useCompliance()           GET /api/auth/compliance/score
 *   providers          useLlmProviders()         GET /api/llm-providers (+ /health)
 *   secrets            useSecrets()              GET /api/auth/secrets
 *   tenants            usePlatformTenants()      GET /api/auth/me/tenants
 *   conversations/err  usePlatformMetrics(7)     GET /api/platform/metrics
 *   critical alerts    usePlatformServices()     GET /api/platform/services (down/degraded)
 *                      + secrets past rotation_due_at + compliance score < 70
 */
import React, { useMemo } from 'react';
import {
  ShieldCheck, Cpu, Key, Building2, MessagesSquare, AlertTriangle, ArrowRight, RefreshCw,
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, StatTile, toneFor } from '../ui';
import { useCompliance } from '../../lib/useCompliance';
import { useSecrets } from '../../lib/useSecrets';
import { useLlmProviders } from '../../lib/useLlmProviders';
import { usePlatformMetrics, usePlatformServices, usePlatformTenants } from '../../lib/usePlatformData';
import type { SettingsSection } from './Overview';

const COMPLIANCE_ALERT_THRESHOLD = 70;

const goTo = (section: SettingsSection) => { window.location.hash = `#settings/${section}`; };

const pct = (v: number | null | undefined) =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${(v * 100).toFixed(2)}%`;

interface Alert { id: string; level: 'bad' | 'warn'; code: string; text: string; section: SettingsSection }

export const OverviewSection = () => {
  const compliance = useCompliance();
  const secrets = useSecrets();
  const llm = useLlmProviders();
  const tenants = usePlatformTenants();
  const metrics = usePlatformMetrics(7);
  const services = usePlatformServices();

  const score = compliance.score?.score ?? null;
  const activeSecrets = secrets.secrets.filter(s => s.is_active);
  const now = Date.now();
  const overdueSecrets = activeSecrets.filter(s => s.rotation_due_at && new Date(s.rotation_due_at).getTime() < now);

  const alerts = useMemo<Alert[]>(() => {
    const out: Alert[] = [];
    for (const svc of services.data?.items ?? []) {
      if (svc.status === 'down' || svc.status === 'degraded') {
        out.push({
          id: `svc-${svc.name}`,
          level: svc.status === 'down' ? 'bad' : 'warn',
          code: `SERVICE_${svc.status.toUpperCase()}`,
          text: `${svc.name}${svc.detail ? ` — ${svc.detail}` : ''}`,
          section: 'INFRASTRUCTURE',
        });
      }
    }
    for (const s of overdueSecrets) {
      out.push({
        id: `secret-${s.id}`,
        level: 'warn',
        code: 'SECRET_ROTATION_OVERDUE',
        text: `${s.key_name} quá hạn xoay vòng từ ${new Date(s.rotation_due_at as string).toLocaleDateString('vi-VN')}`,
        section: 'SECRETS',
      });
    }
    if (score !== null && score < COMPLIANCE_ALERT_THRESHOLD) {
      out.push({
        id: 'compliance',
        level: 'warn',
        code: 'COMPLIANCE_SCORE_LOW',
        text: `Điểm tuân thủ ${score}/100 dưới ngưỡng ${COMPLIANCE_ALERT_THRESHOLD}`,
        section: 'SECURITY',
      });
    }
    for (const h of llm.health) {
      if (h.status === 'down') {
        out.push({ id: `llm-${h.model_name}`, level: 'bad', code: 'LLM_DOWN', text: `${h.model_name}${h.error ? ` — ${h.error}` : ''}`, section: 'AI_PROVIDERS' });
      }
    }
    return out;
  }, [services.data, overdueSecrets, score, llm.health]);

  const reloadAll = () => {
    compliance.reload();
    secrets.refetch();
    llm.reload();
    tenants.reload();
    metrics.reload();
    services.reload();
  };

  const overall = alerts.some(a => a.level === 'bad') ? 'CRITICAL' : alerts.length > 0 ? 'WARNING' : 'STABLE';

  return (
    <div className="space-y-8">
      <OperationalHeader
        title="Platform Pulse"
        subtitle="Trạng thái vận hành tổng hợp từ các dịch vụ thật của nền tảng"
        breadcrumbs={[{ label: 'Settings' }, { label: 'Overview' }]}
        status={<StatusBadge status={overall} size="lg" />}
        actions={<Button variant="secondary" icon={RefreshCw} onClick={reloadAll}>Tải lại</Button>}
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
        <StatTile
          label="Compliance score"
          value={score === null ? '—' : `${score}/100`}
          hint={compliance.error ? 'lỗi' : undefined}
          icon={ShieldCheck}
          tone={score === null ? 'neutral' : score >= COMPLIANCE_ALERT_THRESHOLD ? 'ok' : 'warn'}
          loading={compliance.loading}
        />
        <StatTile
          label="LLM providers"
          value={llm.providers.length}
          hint={llm.healthAvailable ? `${llm.health.filter(h => h.status === 'ok').length} ok` : undefined}
          icon={Cpu}
          tone={llm.health.some(h => h.status === 'down') ? 'bad' : 'info'}
          loading={llm.loading}
        />
        <StatTile
          label="Secrets đang hoạt động"
          value={activeSecrets.length}
          hint={overdueSecrets.length ? `${overdueSecrets.length} quá hạn` : undefined}
          icon={Key}
          tone={overdueSecrets.length ? 'warn' : 'neutral'}
          loading={secrets.loading}
        />
        <StatTile
          label="Tenants"
          value={tenants.data?.length ?? 0}
          icon={Building2}
          loading={tenants.loading}
        />
        <StatTile
          label="Hội thoại 24h"
          value={metrics.data?.conversations.last_24h ?? '—'}
          hint={metrics.data ? `err ${pct(metrics.data.error_rate)}` : undefined}
          icon={MessagesSquare}
          tone={metrics.data && metrics.data.error_rate > 0.05 ? 'warn' : 'neutral'}
          loading={metrics.loading}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 space-y-6">
          <Card
            title="Tenants"
            subtitle="GET /api/auth/me/tenants"
            actions={<Button size="sm" variant="ghost" icon={ArrowRight} onClick={() => goTo('ORGANIZATION')}>Organization</Button>}
            padding="none"
          >
            {tenants.loading ? <LoadingState /> : tenants.error ? (
              <div className="p-4"><ErrorState message={tenants.error} onRetry={tenants.reload} /></div>
            ) : (tenants.data?.length ?? 0) === 0 ? (
              <EmptyState icon={Building2} title="Chưa có tenant" description="Chưa có tenant nào đang hoạt động trong hệ thống." />
            ) : (
              <ul className="divide-y" style={{ borderColor: 'var(--border-soft)' }}>
                {tenants.data!.map(t => (
                  <li key={t.id} className="flex items-center justify-between px-5 py-3">
                    <div className="min-w-0">
                      <div className="text-sm font-bold text-[#111111] truncate">{t.name}</div>
                      <div className="text-[11px] font-mono text-[#5F5F5F]">{t.slug}</div>
                    </div>
                    <span className="text-[10px] font-mono text-[#5F5F5F]">{t.id}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <Card title="Compliance score" subtitle="GET /api/auth/compliance/score">
              {compliance.loading ? <LoadingState /> : compliance.error ? (
                <ErrorState message={compliance.error} onRetry={compliance.reload} />
              ) : !compliance.score ? (
                <EmptyState title="Chưa có dữ liệu tuân thủ" />
              ) : (
                <div>
                  <div className="flex items-end gap-2 mb-4">
                    <span className="text-4xl font-bold text-[#111111] font-display leading-none">{compliance.score.score}</span>
                    <span className="text-sm text-[#5F5F5F] font-bold mb-0.5">/ 100</span>
                  </div>
                  <div className="space-y-2">
                    {(compliance.score.breakdown ?? []).map(b => (
                      <div key={b.label}>
                        <div className="flex justify-between text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1">
                          <span>{b.label}</span><span>{b.val}%</span>
                        </div>
                        <div className="h-1.5 rounded-full bg-[#F4E8C3] overflow-hidden">
                          <div className="h-full bg-[#B88719] rounded-full" style={{ width: `${Math.min(100, Math.max(0, b.val))}%` }} />
                        </div>
                      </div>
                    ))}
                  </div>
                  <Button size="sm" variant="ghost" className="mt-4" icon={ArrowRight} onClick={() => goTo('SECURITY')}>Security &amp; Compliance</Button>
                </div>
              )}
            </Card>

            <Card title="AI provider health" subtitle="GET /api/llm-providers/health">
              {llm.loading ? <LoadingState /> : llm.errors.health ? (
                <ErrorState message={llm.errors.health} onRetry={llm.reload} />
              ) : !llm.healthAvailable || (llm.health ?? []).length === 0 ? (
                <EmptyState icon={Cpu} title="Chưa có health check" description="Runtime chưa cấu hình nguồn health cho LLM." />
              ) : (
                <ul className="space-y-2">
                  {(llm.health ?? []).map(h => (
                    <li key={h.model_name} className="flex items-center justify-between gap-3">
                      <span className="text-xs font-semibold text-[#111111] truncate">{h.model_name}</span>
                      <div className="flex items-center gap-2 shrink-0">
                        {h.latency_ms !== null && <span className="text-[10px] font-mono text-[#5F5F5F]">{Math.round(h.latency_ms)} ms</span>}
                        <Badge tone={toneFor(h.status)}>{h.status}</Badge>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              <Button size="sm" variant="ghost" className="mt-4" icon={ArrowRight} onClick={() => goTo('AI_PROVIDERS')}>Provider center</Button>
            </Card>
          </div>
        </div>

        <div className="space-y-6">
          <Card title="Cảnh báo" subtitle="Dịch vụ down/degraded · secrets quá hạn · compliance thấp">
            {services.loading && secrets.loading ? <LoadingState /> : alerts.length === 0 ? (
              <EmptyState icon={ShieldCheck} title="Không có cảnh báo" description={services.error ? `Không kiểm tra được dịch vụ: ${services.error}` : 'Tất cả nguồn đang trong ngưỡng.'} />
            ) : (
              <ul className="space-y-2">
                {alerts.map(a => (
                  <li key={a.id}>
                    <button
                      type="button"
                      onClick={() => goTo(a.section)}
                      className={`w-full text-left p-3 rounded-xl border transition-colors ${a.level === 'bad' ? 'border-[#9F1D1D] bg-[#FAD7D7] hover:bg-[#F6C9C9]' : 'border-[#B88719] bg-[#FFF1BF] hover:bg-[#FBE7A6]'}`}
                    >
                      <div className={`flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-wide mb-1 ${a.level === 'bad' ? 'text-[#7A1010]' : 'text-[#5C3900]'}`}>
                        <AlertTriangle className="w-3 h-3" /> {a.code}
                      </div>
                      <p className="text-xs text-[#111111]">{a.text}</p>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Nền tảng 7 ngày" subtitle="GET /api/platform/metrics?days=7">
            {metrics.loading ? <LoadingState /> : metrics.error ? (
              <ErrorState message={metrics.error} onRetry={metrics.reload} />
            ) : !metrics.data ? <EmptyState title="Chưa có số liệu" /> : (
              <dl className="grid grid-cols-2 gap-3 text-xs">
                {[
                  ['Agents', `${metrics.data.agents.published}/${metrics.data.agents.total} published`],
                  ['Hội thoại', `${metrics.data.conversations.total} (${metrics.data.conversations.active} active)`],
                  ['Messages 24h', metrics.data.messages.last_24h],
                  ['Tokens 24h', metrics.data.tokens.last_24h.toLocaleString('vi-VN')],
                  ['Latency avg/p95', `${Math.round(metrics.data.latency_ms.avg)} / ${Math.round(metrics.data.latency_ms.p95)} ms`],
                  ['Error rate', pct(metrics.data.error_rate)],
                ].map(([k, v]) => (
                  <div key={String(k)} className="p-2.5 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                    <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">{k}</dt>
                    <dd className="text-sm font-bold text-[#111111] mt-0.5">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
