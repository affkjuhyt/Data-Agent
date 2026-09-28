/**
 * Settings → Advanced Platform.
 *
 *   runtime config     usePlatformRuntimeConfig()               GET /api/runtime-config (read-only, real values)
 *
 * No editable AI defaults and no feature flags: both were saved as platform
 * settings blobs that nothing server-side read, so they configured nothing.
 */
import { RefreshCw, Settings2 } from 'lucide-react';
import { Button, Card, EmptyState, ErrorState, LoadingState, SectionTitle } from '../ui';
import { usePlatformRuntimeConfig } from '../../lib/usePlatformData';

export const AdvancedPlatformSection = () => {
  const runtime = usePlatformRuntimeConfig();
  const runtimeEntries = Object.entries(runtime.data ?? {});

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Advanced Platform"
        subtitle="Cấu hình workflow_runtime đang chạy (chỉ đọc)."
      />
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
