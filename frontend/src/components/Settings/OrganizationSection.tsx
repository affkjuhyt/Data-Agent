/**
 * Settings → Organization & Brand.
 *
 *   org profile  usePlatformSetting('org_profile')  {name, legal_name, region, contact_email, brand_color, logo_url}
 *   tenants      usePlatformTenants()               GET /api/auth/me/tenants
 *   residency    useCompliance().settings           GET /api/auth/compliance/settings
 */
import React, { useEffect, useState } from 'react';
import { Building2, Layers, MapPin, Save, RefreshCw, ArrowRight } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState } from '../ui';
import { usePlatformSetting } from '../../lib/usePlatformSettings';
import { usePlatformTenants } from '../../lib/usePlatformData';
import { useCompliance } from '../../lib/useCompliance';
import { useAppState } from '../../AppStateContext';

interface OrgProfile {
  name: string;
  legal_name: string;
  region: string;
  contact_email: string;
  brand_color: string;
  logo_url: string;
}
const EMPTY_PROFILE: OrgProfile = { name: '', legal_name: '', region: '', contact_email: '', brand_color: '#B88719', logo_url: '' };

export const OrganizationSection = () => {
  const { navigate } = useAppState();
  const profile = usePlatformSetting<OrgProfile>('org_profile', EMPTY_PROFILE);
  const tenants = usePlatformTenants();
  const compliance = useCompliance();

  const [form, setForm] = useState<OrgProfile>(EMPTY_PROFILE);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!profile.loading) { setForm({ ...EMPTY_PROFILE, ...profile.value }); setDirty(false); } }, [profile.loading, profile.value]);

  const set = (k: keyof OrgProfile, v: string) => { setForm(f => ({ ...f, [k]: v })); setDirty(true); };
  const submit = (e: React.FormEvent) => { e.preventDefault(); profile.save(form).then(() => setDirty(false)).catch(() => {}); };
  const goTo = (section: string) => navigate('settings', section);

  return (
    <div className="space-y-8 pb-10">
      <OperationalHeader
        title="Organization & Brand"
        subtitle="Hồ sơ tổ chức, danh sách tenant và khu vực lưu trữ dữ liệu."
        breadcrumbs={[{ label: 'Settings' }, { label: 'General' }, { label: 'Organization' }]}
        status={<StatusBadge status={profile.persisted ? 'ACTIVE' : 'DRAFT'} size="lg" />}
        actions={<Button icon={RefreshCw} onClick={() => { profile.reload(); tenants.reload(); compliance.reload(); }}>Tải lại</Button>}
      />

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-6">
          <Card
            title="Hồ sơ tổ chức"
            subtitle={<>Lưu tại <code className="font-mono">platform/settings/org_profile</code>{profile.updatedAt ? ` · cập nhật ${new Date(profile.updatedAt).toLocaleString('vi-VN')}` : ' · chưa lưu lần nào'}</>}
          >
            {profile.error && <div className="mb-4"><ErrorState message={profile.error} onRetry={profile.reload} /></div>}
            {profile.loading ? <LoadingState /> : (
              <form onSubmit={submit} className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <Field label="Tên hiển thị"><Input value={form.name} onChange={e => set('name', e.target.value)} placeholder="Tên nền tảng / tổ chức" /></Field>
                <Field label="Tên pháp lý"><Input value={form.legal_name} onChange={e => set('legal_name', e.target.value)} placeholder="Công ty ..." /></Field>
                <Field label="Email liên hệ"><Input type="email" value={form.contact_email} onChange={e => set('contact_email', e.target.value)} placeholder="ops@example.com" /></Field>
                <Field label="Khu vực" hint="Mô tả tự do; khu vực lưu trữ dữ liệu thực tế nằm ở Security & Compliance."><Input value={form.region} onChange={e => set('region', e.target.value)} placeholder="vd. VN" /></Field>
                <Field label="Màu thương hiệu">
                  <div className="flex items-center gap-2">
                    <input type="color" value={/^#[0-9a-fA-F]{6}$/.test(form.brand_color) ? form.brand_color : '#B88719'} onChange={e => set('brand_color', e.target.value)} className="w-10 h-10 rounded-lg border border-[#BFA66A] bg-white p-0.5" aria-label="Chọn màu" />
                    <Input value={form.brand_color} onChange={e => set('brand_color', e.target.value)} placeholder="#B88719" className="font-mono" />
                  </div>
                </Field>
                <Field label="Logo URL">
                  <div className="flex items-center gap-2">
                    {form.logo_url && <img src={form.logo_url} alt="" className="w-10 h-10 rounded-lg border border-[#BFA66A] object-contain bg-white" onError={e => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />}
                    <Input type="url" value={form.logo_url} onChange={e => set('logo_url', e.target.value)} placeholder="https://…/logo.svg" />
                  </div>
                </Field>
                <div className="md:col-span-2 flex justify-end gap-2 pt-2">
                  <Button type="button" variant="ghost" disabled={!dirty} onClick={() => { setForm({ ...EMPTY_PROFILE, ...profile.value }); setDirty(false); }}>Hoàn tác</Button>
                  <Button type="submit" variant="primary" icon={Save} loading={profile.saving} disabled={!dirty}>Lưu hồ sơ</Button>
                </div>
              </form>
            )}
          </Card>

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
