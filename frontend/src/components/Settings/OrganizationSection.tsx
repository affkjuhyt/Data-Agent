/**
 * Settings → Organization.
 *
 *   tenants      usePlatformTenants()               GET /api/auth/me/tenants
 *   residency    useCompliance().settings           GET /api/auth/compliance/settings
 *
 * No editable org profile / brand: nothing server-side read one, so the form
 * saved a blob that configured nothing.
 */
import { Building2, Layers, MapPin, RefreshCw, ArrowRight } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState } from '../ui';
import { usePlatformTenants } from '../../lib/usePlatformData';
import { useCompliance } from '../../lib/useCompliance';
import { useAppState } from '../../AppStateContext';

export const OrganizationSection = () => {
  const { navigate } = useAppState();
  const tenants = usePlatformTenants();
  const compliance = useCompliance();

  const goTo = (section: string) => navigate('settings', section);

  return (
    <div className="space-y-8 pb-10">
      <OperationalHeader
        title="Organization"
        subtitle="Danh sách tenant và khu vực lưu trữ dữ liệu."
        breadcrumbs={[{ label: 'Settings' }, { label: 'General' }, { label: 'Organization' }]}
        actions={<Button icon={RefreshCw} onClick={() => { tenants.reload(); compliance.reload(); }}>Tải lại</Button>}
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-6">
          <Card title="Tenants" subtitle="GET /api/auth/me/tenants" padding="none">
            {tenants.error ? <div className="p-4"><ErrorState message={tenants.error} onRetry={tenants.reload} /></div> : tenants.loading ? <LoadingState /> : (tenants.data?.length ?? 0) === 0 ? (
              <EmptyState icon={Layers} title="Chưa có tenant" description="Chưa có tenant nào đang hoạt động." />
            ) : (
              <ul className="divide-y" style={{ borderColor: 'var(--border-soft)' }}>
                {tenants.data!.map(t => (
                  <li key={t.id} className="flex items-center justify-between gap-3 px-5 py-3">
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="w-9 h-9 rounded-xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center text-[#8A5A00]"><Building2 className="w-4 h-4" /></span>
                      <div className="min-w-0">
                        <div className="text-sm font-bold text-[#111111] truncate">{t.name}</div>
                        <div className="text-[11px] font-mono text-[#5F5F5F]">{t.slug} · {t.id}</div>
                      </div>
                    </div>
                    <Badge tone="healthy">active</Badge>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Khu vực dữ liệu" subtitle="GET /api/auth/compliance/settings">
            {compliance.error ? <ErrorState message={compliance.error} onRetry={compliance.reload} /> : compliance.loading ? <LoadingState /> : !compliance.settings ? (
              <EmptyState icon={MapPin} title="Chưa có cấu hình residency" />
            ) : (
              <dl className="space-y-3 text-xs">
                <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                  <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Residency region</dt>
                  <dd className="text-sm font-bold text-[#111111] mt-0.5">{compliance.settings.residency_region || '—'}</dd>
                </div>
                <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                  <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Standards</dt>
                  <dd className="text-sm font-bold text-[#111111] mt-0.5">{compliance.settings.residency_standards || '—'}</dd>
                </div>
                <div className="p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                  <dt className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wide">Retention</dt>
                  <dd className="text-sm font-bold text-[#111111] mt-0.5">{compliance.settings.retention_days} ngày {compliance.settings.retention_lock && <Badge tone="warning" className="ml-1">locked</Badge>}</dd>
                </div>
              </dl>
            )}
            <Button size="sm" variant="ghost" className="mt-4" icon={ArrowRight} onClick={() => goTo('SECURITY')}>Chỉnh ở Security &amp; Compliance</Button>
          </Card>
        </div>
      </div>
    </div>
  );
};
