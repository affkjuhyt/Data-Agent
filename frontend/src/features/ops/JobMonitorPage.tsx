/**
 * Read-only job status surface (Holistics /manage/jobs-shaped). Uses GET /v1/jobs — the same
 * ingestion jobs as P04 Job nạp · Giám sát — without pause/run/backfill. Precursor to R3
 * scheduler/monitor; does not close R3-01.
 */
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { listJobs, type IngestionJob } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { formatLag, jobStatus } from '../data-sources/sourceFormat';
import { LinkButton } from '../../components/shell/AppLink';

const STATUS_ORDER: Record<string, number> = {
  running: 0,
  error: 1,
  paused: 2,
  late: 3,
  on_time: 4,
};

function sortJobs(items: IngestionJob[]) {
  return [...items].sort((a, b) => {
    const byStatus = (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9);
    if (byStatus) return byStatus;
    return a.table_name.localeCompare(b.table_name, 'vi');
  });
}

export default function JobMonitorPage() {
  const { navigate } = useAppState();
  const jobs = useV1(['listJobs'], listJobs);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Công việc']}
        title="Công việc"
        description="Trạng thái job nạp gần đây (chỉ xem). Tạm dừng hoặc chạy lại ở Job nạp."
        actions={
          <LinkButton area="data" page="JOBS">
            Mở Job nạp
          </LinkButton>
        }
      />
      <div className="px-da-3xl py-da-xl">
        <AsyncRegion
          state={jobs}
          what="công việc"
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="layers"
              title="Chưa có job nào"
              description="Khi Data Space có job nạp, trạng thái queued / running / done / failed hiện ở đây."
            />
          }
        >
          {(page) => (
            <table className="w-full border-collapse text-left">
              <caption className="sr-only">Danh sách job nạp</caption>
              <thead>
                <tr className="border-b border-border text-muted-foreground da-caption">
                  <th className="py-da-md pr-da-lg font-medium">Bảng</th>
                  <th className="py-da-md pr-da-lg font-medium">Nguồn</th>
                  <th className="py-da-md pr-da-lg font-medium">Trạng thái</th>
                  <th className="py-da-md font-medium">Độ trễ</th>
                </tr>
              </thead>
              <tbody>
                {sortJobs(page.items).map((job) => {
                  const status = jobStatus(job);
                  return (
                    <tr key={job.id} className="border-b border-border">
                      <td className="py-da-md pr-da-lg text-foreground da-body-strong">
                        {job.table_name}
                      </td>
                      <td className="py-da-md pr-da-lg text-foreground da-body-small">
                        {job.source_name}
                      </td>
                      <td className="py-da-md pr-da-lg">
                        <StatusDot tone={status.tone}>{status.label}</StatusDot>
                      </td>
                      <td className="py-da-md text-muted-foreground da-body-small">
                        {job.lag_seconds != null ? formatLag(job.lag_seconds) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}
