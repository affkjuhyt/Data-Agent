/**
 * Settings → Advanced Platform.
 *
 *   platform defaults  usePlatformSetting('platform_defaults')  {temperature, max_context_tokens, rerank_threshold, safety_gate}
 *   runtime config     usePlatformRuntimeConfig()               GET /api/runtime-config (read-only, real values)
 *   feature flags      usePlatformSetting('feature_flags')      [{key, description, enabled, rollout_pct}]
 */
import React, { useEffect, useState } from 'react';
import { Bot, Flag, Save, RefreshCw, Plus, Trash2, Pencil, Check, X, Settings2 } from 'lucide-react';
import { cn } from '../../lib/utils';
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState, SectionTitle, Select } from '../ui';
import { usePlatformSetting } from '../../lib/usePlatformSettings';
import { usePlatformRuntimeConfig } from '../../lib/usePlatformData';

type Tab = 'AI_CONFIG' | 'FLAGS';

interface PlatformDefaults {
  temperature: number;
  max_context_tokens: number;
  rerank_threshold: number;
  safety_gate: 'off' | 'standard' | 'strict';
}
const EMPTY_DEFAULTS: PlatformDefaults = { temperature: 0.2, max_context_tokens: 32000, rerank_threshold: 0.5, safety_gate: 'standard' };

interface FeatureFlag { key: string; description: string; enabled: boolean; rollout_pct: number }
const EMPTY_FLAG: FeatureFlag = { key: '', description: '', enabled: false, rollout_pct: 0 };

export const AdvancedPlatformSection = () => {
  const [tab, setTab] = useState<Tab>('AI_CONFIG');
  return (
    <div className="space-y-6">
      <SectionTitle
        title="Advanced Platform"
        subtitle="Tham số mặc định cho AI engine và feature flags — tất cả được lưu qua platform settings."
        actions={
          <div className="flex bg-[#FFF9E8] p-1 rounded-xl border border-[#BFA66A]">
            {([{ id: 'AI_CONFIG', label: 'AI engine', icon: Bot }, { id: 'FLAGS', label: 'Feature flags', icon: Flag }] as { id: Tab; label: string; icon: typeof Bot }[]).map(t => (
              <button key={t.id} type="button" onClick={() => setTab(t.id)} className={cn('flex items-center gap-2 px-4 py-2 rounded-lg text-xs font-bold transition-all', tab === t.id ? 'bg-white text-[#111111] border border-[#BFA66A] shadow-sm' : 'text-[#3F3F3F] hover:text-[#111111]')}>
                <t.icon className={cn('w-3.5 h-3.5', tab === t.id ? 'text-[#8A5A00]' : 'text-[#5F5F5F]')} /> {t.label}
              </button>
            ))}
          </div>
        }
      />
      {tab === 'AI_CONFIG' ? <AiConfigTab /> : <FlagsTab />}
    </div>
  );
};

/* ─── AI engine defaults + runtime config ────────────────── */
const AiConfigTab = () => {
  const defaults = usePlatformSetting<PlatformDefaults>('platform_defaults', EMPTY_DEFAULTS);
  const runtime = usePlatformRuntimeConfig();
  const [form, setForm] = useState<PlatformDefaults>(EMPTY_DEFAULTS);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!defaults.loading) { setForm({ ...EMPTY_DEFAULTS, ...defaults.value }); setDirty(false); } }, [defaults.loading, defaults.value]);

  const set = <K extends keyof PlatformDefaults>(k: K, v: PlatformDefaults[K]) => { setForm(f => ({ ...f, [k]: v })); setDirty(true); };
  const submit = (e: React.FormEvent) => { e.preventDefault(); defaults.save(form).then(() => setDirty(false)).catch(() => {}); };

  const runtimeEntries = Object.entries(runtime.data ?? {});

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <Card
        title="Platform defaults"
        subtitle={<>Lưu tại <code className="font-mono">platform/settings/platform_defaults</code>{defaults.updatedAt ? ` · ${new Date(defaults.updatedAt).toLocaleString('vi-VN')}` : ' · chưa lưu lần nào'}</>}
      >
        {defaults.error && <div className="mb-4"><ErrorState message={defaults.error} onRetry={defaults.reload} /></div>}
        {defaults.loading ? <LoadingState /> : (
          <form onSubmit={submit} className="space-y-4">
            <Field label="Temperature mặc định" hint="0 – 2; áp dụng khi node không đặt riêng.">
              <Input type="number" step={0.05} min={0} max={2} value={form.temperature} onChange={e => set('temperature', Number(e.target.value))} />
            </Field>
            <Field label="Max context tokens" hint="Trần an toàn cho một request.">
              <Input type="number" min={1} step={1000} value={form.max_context_tokens} onChange={e => set('max_context_tokens', Number(e.target.value))} />
            </Field>
            <Field label="Rerank threshold" hint="Điểm ngữ nghĩa tối thiểu để giữ lại chunk (0 – 1).">
              <Input type="number" step={0.01} min={0} max={1} value={form.rerank_threshold} onChange={e => set('rerank_threshold', Number(e.target.value))} />
            </Field>
            <Field label="Safety gate">
              <Select value={form.safety_gate} onChange={e => set('safety_gate', e.target.value as PlatformDefaults['safety_gate'])}>
                <option value="off">off</option>
                <option value="standard">standard</option>
                <option value="strict">strict</option>
              </Select>
            </Field>
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="ghost" disabled={!dirty} onClick={() => { setForm({ ...EMPTY_DEFAULTS, ...defaults.value }); setDirty(false); }}>Hoàn tác</Button>
              <Button type="submit" variant="primary" icon={Save} loading={defaults.saving} disabled={!dirty}>Lưu</Button>
            </div>
          </form>
        )}
      </Card>

      <Card
        title="Runtime config (read-only)"
        subtitle="GET /api/runtime-config — giá trị workflow_runtime đang dùng"
        actions={<Button size="sm" icon={RefreshCw} loading={runtime.loading} onClick={runtime.reload}>Tải lại</Button>}
      >
        {runtime.error ? <ErrorState message={runtime.error} onRetry={runtime.reload} /> : runtime.loading ? <LoadingState /> : runtimeEntries.length === 0 ? (
          <EmptyState icon={Settings2} title="Runtime không expose cấu hình" />
        ) : (
          <dl className="space-y-2">
            {runtimeEntries.map(([k, v]) => (
              <div key={k} className="flex items-center justify-between gap-3 p-3 rounded-lg border border-[#BFA66A] bg-[#FFF9E8]">
                <dt className="text-xs font-mono font-bold text-[#3F3F3F]">{k}</dt>
                <dd className="text-xs font-mono text-[#111111] break-all text-right">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
              </div>
            ))}
          </dl>
        )}
      </Card>
    </div>
  );
};

/* ─── Feature flags (persisted) ──────────────────────────── */
const FlagsTab = () => {
  const setting = usePlatformSetting<FeatureFlag[]>('feature_flags', []);
  const flags = Array.isArray(setting.value) ? setting.value : [];
  const [editing, setEditing] = useState<FeatureFlag | null>(null);
  const [draft, setDraft] = useState<FeatureFlag>(EMPTY_FLAG);
  const [showForm, setShowForm] = useState(false);

  const startAdd = () => { setEditing(null); setDraft(EMPTY_FLAG); setShowForm(true); };
  const startEdit = (f: FeatureFlag) => { setEditing(f); setDraft({ ...f }); setShowForm(true); };
  const cancel = () => { setShowForm(false); setEditing(null); };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const key = draft.key.trim();
    if (!key) return;
    if (!editing && flags.some(f => f.key === key)) { window.alert(`Flag "${key}" đã tồn tại.`); return; }
    const next = editing ? flags.map(f => (f.key === editing.key ? { ...draft, key } : f)) : [...flags, { ...draft, key }];
    await setting.save(next).then(cancel).catch(() => {});
  };
  const toggle = (f: FeatureFlag) => setting.save(flags.map(x => (x.key === f.key ? { ...x, enabled: !x.enabled } : x))).catch(() => {});
  const remove = (f: FeatureFlag) => {
    if (!window.confirm(`Xoá flag "${f.key}"?`)) return;
    setting.save(flags.filter(x => x.key !== f.key)).catch(() => {});
  };

  return (
    <Card
      title="Feature flags"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/feature_flags</code>{setting.updatedAt ? ` · ${new Date(setting.updatedAt).toLocaleString('vi-VN')}` : ''}</>}
      actions={
        <div className="flex gap-2">
          <Button size="sm" icon={RefreshCw} loading={setting.loading} onClick={setting.reload}>Tải lại</Button>
          <Button size="sm" variant="primary" icon={Plus} onClick={startAdd} disabled={setting.loading}>Thêm flag</Button>
        </div>
      }
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}
      {showForm && (
        <form onSubmit={submit} className="mb-5 p-4 rounded-xl border border-[#BFA66A] bg-[#FFF9E8] grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Key"><Input value={draft.key} onChange={e => setDraft({ ...draft, key: e.target.value })} placeholder="vd. streaming_v2" className="font-mono" disabled={!!editing} required /></Field>
          <Field label="Rollout %"><Input type="number" min={0} max={100} value={draft.rollout_pct} onChange={e => setDraft({ ...draft, rollout_pct: Math.max(0, Math.min(100, Number(e.target.value))) })} /></Field>
          <div className="md:col-span-2"><Field label="Mô tả"><Input value={draft.description} onChange={e => setDraft({ ...draft, description: e.target.value })} placeholder="Flag này bật gì?" /></Field></div>
          <label className="flex items-center gap-2 text-xs font-semibold text-[#3F3F3F]">
            <input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Bật
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" icon={X} onClick={cancel}>Huỷ</Button>
            <Button type="submit" variant="primary" size="sm" icon={Check} loading={setting.saving}>{editing ? 'Lưu' : 'Thêm'}</Button>
          </div>
        </form>
      )}
      {setting.loading ? <LoadingState /> : flags.length === 0 ? (
        <EmptyState icon={Flag} title="Chưa có feature flag" description="Thêm flag để bật/tắt tính năng theo tỷ lệ rollout." />
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
          {flags.map(f => (
            <li key={f.key} className="p-4 rounded-xl border border-[#BFA66A] bg-white">
              <div className="flex items-start justify-between gap-2 mb-1">
                <span className="text-sm font-mono font-bold text-[#111111] break-all">{f.key}</span>
                <Badge tone={f.enabled ? 'healthy' : 'draft'}>{f.enabled ? 'on' : 'off'}</Badge>
              </div>
              <p className="text-[11px] text-[#5F5F5F] min-h-[1rem]">{f.description || '—'}</p>
              <div className="mt-3">
                <div className="flex justify-between text-[10px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1"><span>Rollout</span><span>{f.rollout_pct}%</span></div>
                <div className="h-1.5 rounded-full bg-[#F4E8C3] overflow-hidden"><div className="h-full bg-[#B88719] rounded-full" style={{ width: `${f.rollout_pct}%` }} /></div>
              </div>
              <div className="flex justify-end gap-1 mt-3">
                <Button size="sm" variant="ghost" onClick={() => toggle(f)} disabled={setting.saving}>{f.enabled ? 'Tắt' : 'Bật'}</Button>
                <Button size="sm" variant="ghost" icon={Pencil} onClick={() => startEdit(f)} aria-label="Sửa" />
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(f)} aria-label="Xoá" />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};
