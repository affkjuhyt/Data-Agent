/**
 * Settings → API & Developers.
 *
 *   api keys  <APIKeysPanel />   (Settings/Auth/APIKeysPanel — real useAPIKeys)
 *   webhooks  usePlatformSetting('webhooks')  [{id, url, events[], enabled, secret_ref}]
 *
 * Webhook *delivery* is not wired server-side; this page only persists the
 * registrations. No SDK block: there is no published SDK to link to.
 */
import React, { useState } from 'react';
import { Webhook, Plus, Trash2, Pencil, Check, X, RefreshCw } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState } from '../ui';
import { APIKeysPanel } from './Auth/APIKeysPanel';
import { usePlatformSetting, newSettingId } from '../../lib/usePlatformSettings';

export interface WebhookReg { id: string; url: string; events: string[]; enabled: boolean; secret_ref: string }

/** Event names emitted by the platform (documentation of the contract, not data). */
const EVENT_OPTIONS = ['conversation.created', 'conversation.completed', 'agent.published', 'document.ingested', 'review.requested', 'secret.rotated'];
const EMPTY_HOOK: Omit<WebhookReg, 'id'> = { url: '', events: [], enabled: true, secret_ref: '' };

export const APISection = () => (
  <div className="space-y-8 pb-10">
    <OperationalHeader
      title="API & Developers"
      subtitle="API keys cho truy cập lập trình và đăng ký webhook."
      breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'API' }]}
      status={<StatusBadge status="ACTIVE" size="lg" />}
    />

    <Card padding="sm" className="bg-[#111111] border-[#111111]">
      {/* APIKeysPanel is styled for a dark surface (Auth section) — keep it on one. */}
      <div className="p-3"><APIKeysPanel /></div>
    </Card>

    <Webhooks />
  </div>
);

const Webhooks = () => {
  const setting = usePlatformSetting<WebhookReg[]>('webhooks', []);
  const hooks = Array.isArray(setting.value) ? setting.value : [];
  const [editing, setEditing] = useState<WebhookReg | null>(null);
  const [draft, setDraft] = useState<Omit<WebhookReg, 'id'>>(EMPTY_HOOK);
  const [showForm, setShowForm] = useState(false);

  const startAdd = () => { setEditing(null); setDraft(EMPTY_HOOK); setShowForm(true); };
  const startEdit = (h: WebhookReg) => { setEditing(h); setDraft({ url: h.url, events: h.events, enabled: h.enabled, secret_ref: h.secret_ref }); setShowForm(true); };
  const cancel = () => { setShowForm(false); setEditing(null); };
  const toggleEvent = (ev: string) => setDraft(d => ({ ...d, events: d.events.includes(ev) ? d.events.filter(x => x !== ev) : [...d.events, ev] }));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.url.trim()) return;
    const next = editing ? hooks.map(h => (h.id === editing.id ? { ...editing, ...draft } : h)) : [...hooks, { id: newSettingId('wh'), ...draft }];
    await setting.save(next).then(cancel).catch(() => {});
  };
  const toggle = (h: WebhookReg) => setting.save(hooks.map(x => (x.id === h.id ? { ...x, enabled: !x.enabled } : x))).catch(() => {});
  const remove = (h: WebhookReg) => {
    if (!window.confirm(`Xoá webhook ${h.url}?`)) return;
    setting.save(hooks.filter(x => x.id !== h.id)).catch(() => {});
  };

  return (
    <Card
      title="Webhooks"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/webhooks</code>. Việc gửi sự kiện chưa được wire ở server — đây là đăng ký cấu hình.</>}
      actions={
        <div className="flex gap-2">
          <Button size="sm" icon={RefreshCw} onClick={setting.reload} loading={setting.loading}>Tải lại</Button>
          <Button size="sm" variant="primary" icon={Plus} onClick={startAdd} disabled={setting.loading}>Thêm webhook</Button>
        </div>
      }
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}
      {showForm && (
        <form onSubmit={submit} className="mb-5 p-4 rounded-xl border border-[#BFA66A] bg-[#FFF9E8] grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="URL"><Input type="url" value={draft.url} onChange={e => setDraft({ ...draft, url: e.target.value })} placeholder="https://…/hooks" required /></Field>
          <Field label="Secret ref" hint="Tham chiếu secret trong Secrets Vault (không dán secret thô)."><Input value={draft.secret_ref} onChange={e => setDraft({ ...draft, secret_ref: e.target.value })} placeholder="vault/kv/webhooks/…" className="font-mono" /></Field>
          <div className="md:col-span-2">
            <span className="block text-[11px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1.5">Sự kiện</span>
            <div className="flex flex-wrap gap-2">
              {EVENT_OPTIONS.map(ev => {
                const on = draft.events.includes(ev);
                return (
                  <button key={ev} type="button" onClick={() => toggleEvent(ev)} className={`px-2.5 py-1 rounded-full text-[11px] font-mono border transition-colors ${on ? 'bg-[#F4E8C3] border-[#8A5A00] text-[#111111]' : 'bg-white border-[#BFA66A] text-[#3F3F3F] hover:border-[#8A5A00]'}`} aria-pressed={on}>{ev}</button>
                );
              })}
            </div>
          </div>
          <label className="flex items-center gap-2 text-xs font-semibold text-[#3F3F3F]">
            <input type="checkbox" checked={draft.enabled} onChange={e => setDraft({ ...draft, enabled: e.target.checked })} /> Bật
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" icon={X} onClick={cancel}>Huỷ</Button>
            <Button type="submit" variant="primary" size="sm" icon={Check} loading={setting.saving}>{editing ? 'Lưu' : 'Thêm'}</Button>
          </div>
        </form>
      )}
      {setting.loading ? <LoadingState /> : hooks.length === 0 ? (
        <EmptyState icon={Webhook} title="Chưa có webhook" description="Đăng ký URL nhận sự kiện của nền tảng." />
      ) : (
        <ul className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {hooks.map(h => (
            <li key={h.id} className="p-4 rounded-xl border border-[#BFA66A] bg-white">
              <div className="flex items-start justify-between gap-3 mb-2">
                <span className="text-[12px] font-mono text-[#111111] break-all">{h.url}</span>
                <Badge tone={h.enabled ? 'healthy' : 'draft'}>{h.enabled ? 'enabled' : 'disabled'}</Badge>
              </div>
              <div className="flex flex-wrap gap-1.5 mb-3">
                {h.events.length === 0 ? <span className="text-[11px] text-[#5F5F5F]">Không có sự kiện</span> : h.events.map(ev => <span key={ev} className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-[#BFA66A] bg-[#FFF9E8] text-[#3F3F3F]">{ev}</span>)}
              </div>
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] text-[#5F5F5F] font-mono truncate">{h.secret_ref || 'no secret ref'}</span>
                <div className="flex gap-1 shrink-0">
                  <Button size="sm" variant="ghost" onClick={() => toggle(h)} disabled={setting.saving}>{h.enabled ? 'Tắt' : 'Bật'}</Button>
                  <Button size="sm" variant="ghost" icon={Pencil} onClick={() => startEdit(h)} aria-label="Sửa" />
                  <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(h)} aria-label="Xoá" />
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};
