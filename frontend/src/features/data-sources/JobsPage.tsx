import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Menu } from '../../design-system/Menu';
import { Segmented } from '../../design-system/Segmented';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import { controlJob, listJobs, type IngestionJob } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import {
  formatLag,
  formatRows,
  jobStatus,
  jobsSummary,
  matchesJobFilter,
  methodLabel,
  type JobFilter,
} from './sourceFormat';

/*
 * "P04 · Job nạp · Giám sát" (2281:16917, UC-04-09): every ingestion job with a 24-hour strip
 * (one cell per hour: success, error, no run), lag, rows loaded today and status. "Tạo job" opens
 * Job nạp · Thiết lập; "Chạy bù" has no flow in Figma yet; a row's "…" runs, pauses or resumes it.
 */
const CELL: Record<IngestionJob['hours'][number], string> = {
  ok: 'bg-success opacity-55',
  error: 'bg-error',
  none: 'bg-subtle',
};

export default function JobsPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const jobs = useV1(listJobs, []);
  const [filter, setFilter] = useState<JobFilter>('all');
  const items = jobs.data?.items ?? [];
  const count = (f: JobFilter) => items.filter((j) => matchesJobFilter(j, f)).length;
  const visible = useMemo(() => items.filter((j) => matchesJobFilter(j, filter)), [items, filter]);

  const act = async (job: IngestionJob, verb: 'run' | 'pause' | 'resume') => {
    const done = { run: 'Đã chạy lại', pause: 'Đã tạm dừng', resume: 'Đã tiếp tục' }[verb];
    try {
      await controlJob(job.id, verb);
      toast({ tone: 'success', message: `${done} ${job.table_name}.` });
      jobs.reload();
    } catch (e) {
      toast({
        tone: 'error',
        message: `Không thực hiện được với ${job.table_name}. ${(e as Error).message}`,
      });
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Job nạp']}
        title="Job nạp"
        description={jobs.data ? jobsSummary(items) : undefined}
        actions={
          <>
            <Tooltip content="Chạy bù theo khoảng thời gian đang được làm.">
              <Button disabled>Chạy bù</Button>
            </Tooltip>
            <Button kind="primary" onClick={() => navigate('data', 'JOB_NEW')}>
              Tạo job
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        <div className="flex items-center gap-da-md">
          <Segmented<JobFilter>
            label="Lọc job"
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'all', label: `Tất cả ${items.length}` },
              { value: 'problem', label: `Có vấn đề ${count('problem')}` },
              { value: 'running', label: `Đang chạy ${count('running')}` },
              { value: 'paused', label: `Tạm dừng ${count('paused')}` },
            ]}
          />
          <span className="flex-1" />
          <span className="text-muted-foreground da-body-small">24 giờ qua</span>
        </div>
        <AsyncRegion
          state={jobs}
          what="job nạp"
          skeletonRows={6}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="refresh"
              title="Chưa có job nạp nào"
              description="Job nạp được tạo khi bạn kết nối một nguồn ở chế độ nạp về hoặc tải tệp lên."
            />
          }
        >
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">Job</th>
                  <th className="w-[120px] px-da-md py-da-md font-normal">Cách nạp</th>
                  <th className="w-[316px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    24 giờ qua
                  </th>
                  <th className="w-[110px] px-da-md py-da-md font-normal">Độ trễ</th>
                  <th className="w-[110px] px-da-md py-da-md font-normal">Dòng hôm nay</th>
                  <th className="w-[200px] px-da-md py-da-md font-normal">Trạng thái</th>
                  <th className="w-[44px] px-da-md py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((job) => {
                  const status = jobStatus(job);
                  return (
                    <tr key={job.id} className="border-b border-border">
                      <td className="px-da-lg py-[9px]">
                        <div className="flex min-w-0 flex-col gap-[2px]">
                          <span className="truncate text-foreground da-mono">{job.table_name}</span>
                          <span className="truncate text-muted-foreground da-caption">
                            {job.source_name}
                          </span>
                        </div>
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-body-small">
                        {methodLabel(job)}
                      </td>
                      <td className="px-da-md py-[9px] max-[1279px]:hidden">
                        <span
                          className="flex h-[14px] items-center gap-[2px]"
                          role="img"
                          aria-label={hoursLabel(job)}
                        >
                          {job.hours.map((h, i) => (
                            <span
                              key={i}
                              className={cn('h-[14px] w-[10px] rounded-[2px]', CELL[h])}
                            />
                          ))}
                        </span>
                      </td>
                      <td
                        className={cn(
                          'px-da-md py-[9px] da-mono',
                          job.late ? 'text-warning' : 'text-foreground',
                        )}
                      >
                        {job.lag_seconds === null ? '—' : formatLag(job.lag_seconds)}
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-mono">
                        {formatRows(job.rows_today)}
                      </td>
                      <td className="px-da-md py-[9px]">
                        <StatusDot tone={status.tone}>{status.label}</StatusDot>
                      </td>
                      <td className="px-da-md py-[9px]">
                        <Menu
                          label={`Thao tác với ${job.table_name}`}
                          items={[
                            {
                              label: 'Chạy lại ngay',
                              icon: 'play',
                              onSelect: () => void act(job, 'run'),
                            },
                            job.status === 'paused'
                              ? {
                                  label: 'Tiếp tục',
                                  icon: 'play',
                                  onSelect: () => void act(job, 'resume'),
                                }
                              : {
                                  label: 'Tạm dừng',
                                  icon: 'clock',
                                  onSelect: () => void act(job, 'pause'),
                                },
                          ]}
                        />
                      </td>
                    </tr>
                  );
                })}
                {visible.length === 0 && (
                  <tr>
                    <td
                      colSpan={7}
                      className="px-da-lg py-da-xl text-muted-foreground da-body-small"
                    >
                      Không có job nào khớp bộ lọc.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          Mỗi ô là một giờ: xanh là chạy thành công, đỏ là lỗi, xám là không có lần chạy. Job trễ
          quá hạn độ mới sẽ báo người phụ trách nguồn và đánh dấu số liệu liên quan là “có thể cũ”.
        </p>
      </div>
    </div>
  );
}

function hoursLabel(job: IngestionJob): string {
  const ok = job.hours.filter((h) => h === 'ok').length;
  const errors = job.hours.filter((h) => h === 'error').length;
  return `24 giờ qua: ${ok} giờ chạy thành công, ${errors} giờ lỗi`;
}
