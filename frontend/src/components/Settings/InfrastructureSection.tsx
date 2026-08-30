/**
 * Settings → Runtime Infrastructure.
 *
 *   services   usePlatformServices()       GET /api/platform/services
 *   sandbox    usePlatformSandboxStatus()  GET /api/sandbox/status (workflow-runtime relay)
 *
 * No cluster fleet, regions or toggles: nothing on this page is invented.
 */
import React from 'react';
import { Server, Database, Cpu, Box, RefreshCw, Activity, ShieldAlert, Layers, Zap } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, StatTile, Table, toneFor } from '../ui';
import {
  usePlatformSandboxStatus, usePlatformServices, type PlatformService, type SandboxJobSummary,
} from '../../lib/usePlatformData';

const KIND_ICON: Record<PlatformService['kind'], typeof Server> = {
  database: Database, cache: Layers, queue: Activity, llm: Cpu, service: Server,
};

const formatAge = (s: number) => (s < 60 ? `${Math.round(s)}s` : s < 3600 ? `${Math.round(s / 60)}m` : `${Math.round(s / 3600)}h`);

export const InfrastructureSection = () => {
  const services = usePlatformServices();
  const sandbox = usePlatformSandboxStatus();

  const items = services.data?.items ?? [];
  const down = items.filter(s => s.status === 'down').length;
  const degraded = items.filter(s => s.status === 'degraded').length;
  const overall = down ? 'CRITICAL' : degraded ? 'DEGRADED' : services.error ? 'UNKNOWN' : 'HEALTHY';

  const jobs: SandboxJobSummary[] = [...(sandbox.data?.jobs?.python ?? []), ...(sandbox.data?.jobs?.sql ?? [])];

  return (
    <div className="space-y-8 pb-10">
      <OperationalHeader
        title="Runtime Infrastructure"
        subtitle="Trạng thái các dịch vụ nền tảng và sandbox executor — đọc từ health check thật."
        breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'Infrastructure' }]}
        status={<StatusBadge status={overall} size="lg" />}
        actions={<Button icon={RefreshCw} loading={services.loading || sandbox.loading} onClick={() => { services.reload(); sandbox.reload(); }}>Tải lại</Button>}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Dịch vụ" value={items.length} icon={Server} loading={services.loading} />
        <StatTile label="OK" value={items.filter(s => s.status === 'ok').length} icon={Activity} tone="ok" loading={services.loading} />
        <StatTile label="Degraded" value={degraded} icon={ShieldAlert} tone={degraded ? 'warn' : 'neutral'} loading={services.loading} />
        <StatTile label="Down" value={down} icon={ShieldAlert} tone={down ? 'bad' : 'neutral'} loading={services.loading} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2">
          <Card title="Platform services" subtitle="GET /api/platform/services" padding="none">
            {services.error ? <div className="p-4"><ErrorState message={services.error} onRetry={services.reload} /></div> : services.loading ? <LoadingState /> : (
              <Table<PlatformService>
                rows={items}
                rowKey={s => s.name}
                empty={<EmptyState icon={Server} title="Không có dịch vụ nào được báo cáo" description="Endpoint trả về danh sách rỗng." />}
                columns={[
                  {
                    header: 'Dịch vụ', cell: s => {
                      const Icon = KIND_ICON[s.kind] ?? Server;
                      return (
                        <div className="flex items-center gap-2.5">
                          <span className="w-7 h-7 rounded-lg bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center text-[#8A5A00]"><Icon className="w-3.5 h-3.5" /></span>
                          <span className="font-bold">{s.name}</span>
                        </div>
                      );
                    },
                  },
                  { header: 'Loại', cell: s => <span className="font-mono text-[11px]">{s.kind}</span> },
                  { header: 'Trạng thái', cell: s => <Badge tone={toneFor(s.status)}>{s.status}</Badge> },
                  { header: 'Latency', align: 'right', cell: s => <span className="font-mono">{s.latency_ms != null ? `${Math.round(s.latency_ms)} ms` : '—'}</span> },
                  { header: 'Chi tiết', cell: s => <span className="text-[#3F3F3F] text-[11px] break-all">{s.detail ?? '—'}</span> },
                ]}
              />
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Sandbox executor" subtitle="GET /api/sandbox/status">
            {sandbox.error ? <ErrorState message={sandbox.error} onRetry={sandbox.reload} /> : sandbox.loading ? <LoadingState /> : !sandbox.data ? (
              <EmptyState icon={Box} title="Không có dữ liệu sandbox" />
            ) : (
              <div className="space-y-3">
                <div className="grid grid-cols-3 gap-2">
                  {[
                    ['Pool', sandbox.data.pool.size],
                    ['Idle', sandbox.data.pool.idle],
                    ['Busy', sandbox.data.pool.busy],
                  ].map(([k, v]) => (
                    <div key={String(k)} className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8] text-center">
                      <div className="text-lg font-bold text-[#111111] font-display leading-none">{v}</div>
                      <div className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide mt-1">{k}</div>
                    </div>
                  ))}
                </div>
                <dl className="text-xs space-y-1.5">
                  <div className="flex justify-between"><dt className="text-[#5F5F5F]">Runtime</dt><dd className="font-mono text-[#111111]">{sandbox.data.pool.runtime}</dd></div>
                  <div className="flex justify-between"><dt className="text-[#5F5F5F]">Egress</dt><dd><Badge tone={sandbox.data.pool.egress_enabled ? 'warning' : 'healthy'}>{sandbox.data.pool.egress_enabled ? 'enabled' : 'blocked'}</Badge></dd></div>
                  <div className="flex justify-between"><dt className="text-[#5F5F5F]">Circuit breaker</dt><dd><Badge tone={sandbox.data.pool.circuit_breaker.state === 'closed' ? 'healthy' : sandbox.data.pool.circuit_breaker.state === 'open' ? 'failed' : 'warning'}>{sandbox.data.pool.circuit_breaker.state}{sandbox.data.pool.circuit_breaker.enforce ? '' : ' · observe'}</Badge></dd></div>
                </dl>
              </div>
            )}
          </Card>

          <Card title="Sandbox jobs gần đây" subtitle={`${jobs.length} job`} padding="none">
            {sandbox.loading ? <LoadingState /> : (
              <Table<SandboxJobSummary>
                rows={jobs}
                rowKey={j => `${j.kind}-${j.job_id}`}
                empty={<EmptyState icon={Zap} title="Chưa có job" description="Sandbox chưa chạy job nào gần đây." />}
                columns={[
                  { header: 'Job', cell: j => <span className="font-mono text-[11px]">{j.kind} · {j.job_id.slice(0, 8)}</span> },
                  { header: 'Trạng thái', cell: j => <Badge tone={toneFor(j.status)}>{j.status}</Badge> },
                  { header: 'Tuổi', align: 'right', cell: j => <span className="font-mono">{formatAge(j.age_s)}</span> },
                ]}
              />
            )}
          </Card>
        </div>
      </div>
    </div>
  );
};
