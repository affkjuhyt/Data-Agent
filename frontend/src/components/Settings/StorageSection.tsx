/**
 * Settings → Storage.
 *
 *   buckets      usePlatformBuckets()            GET /api/storage/buckets (workflow_runtime)
 *   objects      usePlatformBucketObjects(b)     GET /api/storage/buckets/{b}/objects
 *   collections  usePlatformVectorCollections()  GET /api/knowledge/qdrant/collections
 *
 * Data retention lives in Security & Compliance; the per-scope retention editor
 * that used to sit here saved a blob nothing server-side read.
 */
import { useState } from 'react';
import { Database, HardDrive, Archive, RefreshCw, Layers } from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { Badge, Button, Card, EmptyState, ErrorState, LoadingState, StatTile, Table } from '../ui';
import { usePlatformBucketObjects, usePlatformBuckets, usePlatformVectorCollections } from '../../lib/usePlatformData';

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
        title="Storage"
        subtitle="Object storage (MinIO) và vector collections (Qdrant)."
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

      <div className="space-y-6">
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
    </div>
  );
};
