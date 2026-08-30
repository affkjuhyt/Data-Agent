/**
 * Settings → Storage & Retention.
 *
 *   buckets      usePlatformBuckets()            GET /api/storage/buckets (workflow-runtime)
 *   objects      usePlatformBucketObjects(b)     GET /api/storage/buckets/{b}/objects
 *   collections  usePlatformVectorCollections()  GET /api/knowledge/qdrant/collections
 *   retention    usePlatformSetting('retention_policies')  [{id, scope, days, action}]
 */
import React, { useState } from 'react';
import { Database, HardDrive, Archive, Plus, RefreshCw, Trash2, Pencil, Check, X, Layers } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, Field, Input, LoadingState, Select, StatTile, Table } from '../ui';
import { usePlatformBucketObjects, usePlatformBuckets, usePlatformVectorCollections } from '../../lib/usePlatformData';
import { usePlatformSetting, newSettingId } from '../../lib/usePlatformSettings';

export interface RetentionPolicy { id: string; scope: string; days: number; action: 'delete' | 'archive' | 'anonymize' }

const SCOPES = ['conversations', 'messages', 'audit_logs', 'documents', 'traces', 'sandbox_jobs'];
const ACTIONS: RetentionPolicy['action'][] = ['delete', 'archive', 'anonymize'];
const EMPTY: Omit<RetentionPolicy, 'id'> = { scope: SCOPES[0], days: 90, action: 'delete' };

const fmtBytes = (n: number) => {
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024; let i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1)} ${u[i]}`;
};

export const StorageSection = () => {
  const buckets = usePlatformBuckets();
  const collections = usePlatformVectorCollections();
  const [bucket, setBucket] = useState<string | null>(null);
  const objects = usePlatformBucketObjects(bucket);

  const bucketList = buckets.data?.buckets ?? [];
  const cols = collections.data ?? [];
  const objs = objects.data?.objects ?? [];
  const objBytes = objs.reduce((a, o) => a + (o.size ?? 0), 0);

  return (
    <div className="space-y-8 pb-10">
      <OperationalHeader
        title="Storage & Retention"
        subtitle="Object storage (MinIO), vector collections (Qdrant) và chính sách lưu trữ."
        breadcrumbs={[{ label: 'Settings' }, { label: 'Platform' }, { label: 'Storage' }]}
        status={<StatusBadge status={buckets.error || collections.error ? 'DEGRADED' : 'ACTIVE'} size="lg" />}
        actions={<Button icon={RefreshCw} loading={buckets.loading || collections.loading} onClick={() => { buckets.reload(); collections.reload(); if (bucket) objects.reload(); }}>Tải lại</Button>}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Buckets" value={bucketList.length} icon={HardDrive} loading={buckets.loading} />
        <StatTile label="Vector collections" value={cols.length} icon={Database} loading={collections.loading} />
        <StatTile label="Tổng vector points" value={cols.reduce((a, c) => a + (c.total_points ?? 0), 0).toLocaleString('en-US')} icon={Layers} loading={collections.loading} />
        <StatTile label={bucket ? `Objects · ${bucket}` : 'Objects'} value={bucket ? objs.length : '—'} hint={bucket && objs.length ? fmtBytes(objBytes) : undefined} icon={Archive} loading={!!bucket && objects.loading} />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-6">
          <Card title="Object storage buckets" subtitle="GET /api/storage/buckets" padding="none">
            {buckets.error ? <div className="p-4"><ErrorState message={buckets.error} onRetry={buckets.reload} /></div> : buckets.loading ? <LoadingState /> : bucketList.length === 0 ? (
              <EmptyState icon={HardDrive} title="Chưa có bucket" description="MinIO chưa có bucket nào." />
            ) : (
              <ul className="divide-y" style={{ borderColor: 'var(--border-soft)' }}>
                {bucketList.map(b => (
                  <li key={b}>
                    <button
                      type="button"
                      onClick={() => setBucket(bucket === b ? null : b)}
                      className={`w-full flex items-center justify-between px-5 py-3 text-left hover:bg-[#FFF9E8] transition-colors ${bucket === b ? 'bg-[#FFF9E8]' : ''}`}
                    >
                      <span className="flex items-center gap-2.5">
                        <HardDrive className="w-4 h-4 text-[#8A5A00]" />
                        <span className="font-mono text-sm font-bold text-[#111111]">{b}</span>
                      </span>
                      <span className="text-[11px] text-[#5F5F5F]">{bucket === b ? 'Ẩn objects' : 'Xem objects'}</span>
                    </button>
                    {bucket === b && (
                      <div className="px-5 pb-4">
                        {objects.error ? <ErrorState message={objects.error} onRetry={objects.reload} /> : objects.loading ? <LoadingState /> : (
                          <Table
                            rows={objs.slice(0, 200)}
                            rowKey={o => o.key}
                            empty={<EmptyState title="Bucket trống" />}
                            columns={[
                              { header: 'Key', cell: o => <span className="font-mono text-[11px] break-all">{o.key}</span> },
                              { header: 'Kích thước', align: 'right', cell: o => <span className="font-mono">{fmtBytes(o.size)}</span> },
                              { header: 'Sửa đổi', align: 'right', cell: o => <span className="font-mono text-[11px]">{new Date(o.last_modified).toLocaleString('vi-VN')}</span> },
                            ]}
                          />
                        )}
                        {objs.length > 200 && <p className="text-[11px] text-[#5F5F5F] mt-2">Hiển thị 200/{objs.length} objects.</p>}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="Vector collections (Qdrant)" subtitle="GET /api/knowledge/qdrant/collections" padding="none">
            {collections.error ? <div className="p-4"><ErrorState message={collections.error} onRetry={collections.reload} /></div> : collections.loading ? <LoadingState /> : (
              <Table
                rows={cols}
                rowKey={c => c.id}
                empty={<EmptyState icon={Database} title="Chưa có collection" description="Qdrant chưa có collection nào cho tenant này." />}
                columns={[
                  { header: 'Collection', cell: c => <span className="font-mono font-bold">{c.name}</span> },
                  { header: 'Embedding', cell: c => c.embedding_model || '—' },
                  { header: 'Dim / distance', cell: c => <span className="font-mono">{c.dimensions} · {c.distance}</span> },
                  { header: 'Points (tenant / total)', align: 'right', cell: c => <span className="font-mono">{c.points.toLocaleString('en-US')} / {c.total_points.toLocaleString('en-US')}</span> },
                  { header: 'Indexed', align: 'right', cell: c => <span className="font-mono">{c.indexed}%</span> },
                  { header: 'Trạng thái', cell: c => <Badge tone={c.active ? 'healthy' : 'draft'}>{c.active ? 'active' : 'inactive'}</Badge> },
                ]}
              />
            )}
          </Card>
        </div>

        <div>
          <RetentionPolicies />
        </div>
      </div>
    </div>
  );
};

/* ─── Retention policies (persisted) ─────────────────────── */
const RetentionPolicies = () => {
  const setting = usePlatformSetting<RetentionPolicy[]>('retention_policies', []);
  const policies = Array.isArray(setting.value) ? setting.value : [];
  const [editing, setEditing] = useState<RetentionPolicy | null>(null);
  const [draft, setDraft] = useState<Omit<RetentionPolicy, 'id'>>(EMPTY);
  const [showForm, setShowForm] = useState(false);

  const startAdd = () => { setEditing(null); setDraft(EMPTY); setShowForm(true); };
  const startEdit = (p: RetentionPolicy) => { setEditing(p); setDraft({ scope: p.scope, days: p.days, action: p.action }); setShowForm(true); };
  const cancel = () => { setShowForm(false); setEditing(null); };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.scope || draft.days < 0) return;
    const next = editing ? policies.map(p => (p.id === editing.id ? { ...editing, ...draft } : p)) : [...policies, { id: newSettingId('ret'), ...draft }];
    await setting.save(next).then(cancel).catch(() => {});
  };
  const remove = (p: RetentionPolicy) => {
    if (!window.confirm(`Xoá policy cho "${p.scope}"?`)) return;
    setting.save(policies.filter(x => x.id !== p.id)).catch(() => {});
  };

  return (
    <Card
      title="Retention policies"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/retention_policies</code>{setting.updatedAt ? ` · ${new Date(setting.updatedAt).toLocaleString('vi-VN')}` : ''}</>}
      actions={<Button size="sm" variant="primary" icon={Plus} onClick={startAdd} disabled={setting.loading}>Thêm</Button>}
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}
      {showForm && (
        <form onSubmit={submit} className="mb-4 p-4 rounded-xl border border-[#BFA66A] bg-[#FFF9E8] space-y-3">
          <Field label="Phạm vi">
            <Select value={draft.scope} onChange={e => setDraft({ ...draft, scope: e.target.value })}>
              {SCOPES.map(s => <option key={s} value={s}>{s}</option>)}
            </Select>
          </Field>
          <Field label="Giữ (ngày)" hint="0 = không tự động xử lý">
            <Input type="number" min={0} value={draft.days} onChange={e => setDraft({ ...draft, days: Number(e.target.value) })} required />
          </Field>
          <Field label="Hành động sau hạn">
            <Select value={draft.action} onChange={e => setDraft({ ...draft, action: e.target.value as RetentionPolicy['action'] })}>
              {ACTIONS.map(a => <option key={a} value={a}>{a}</option>)}
            </Select>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" icon={X} onClick={cancel}>Huỷ</Button>
            <Button type="submit" variant="primary" size="sm" icon={Check} loading={setting.saving}>{editing ? 'Lưu' : 'Thêm'}</Button>
          </div>
        </form>
      )}
      {setting.loading ? <LoadingState /> : policies.length === 0 ? (
        <EmptyState icon={Archive} title="Chưa có retention policy" description="Thêm policy để định nghĩa thời gian lưu và hành động sau hạn cho từng loại dữ liệu." />
      ) : (
        <ul className="space-y-2">
          {policies.map(p => (
            <li key={p.id} className="flex items-center justify-between gap-3 p-3 rounded-xl border border-[#BFA66A] bg-white">
              <div className="min-w-0">
                <div className="text-sm font-bold text-[#111111] font-mono">{p.scope}</div>
                <div className="text-[11px] text-[#5F5F5F]">{p.days} ngày → <span className="font-bold text-[#3F3F3F]">{p.action}</span></div>
              </div>
              <div className="flex gap-1 shrink-0">
                <Button size="sm" variant="ghost" icon={Pencil} onClick={() => startEdit(p)} aria-label="Sửa" />
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(p)} aria-label="Xoá" />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};
