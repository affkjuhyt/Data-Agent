import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Menu } from '../../design-system/Menu';
import { Segmented } from '../../design-system/Segmented';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
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
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "P04 · Job nạp · Giám sát" (2281:16917, UC-04-09): every ingestion job with a 24-hour strip
 * (one cell per hour: success, error, no run), lag, rows loaded today and status. "Tạo job" opens
 * Job nạp · Thiết lập; "Chạy bù" has no flow in Figma yet; a row's "…" runs today's data again,
 * pauses (with the reason BR-106 asks for) or resumes it.
 */
const CELL: Record<IngestionJob['hours'][number], string> = {
  ok: 'bg-success opacity-55',
  error: 'bg-error',
  none: 'bg-subtle',
};

export default function JobsPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const jobs = useV1(['listJobs'], listJobs);
  const [filter, setFilter] = useState<JobFilter>('all');
  const items = jobs.data?.items ?? [];
  const count = (f: JobFilter) => items.filter((j) => matchesJobFilter(j, f)).length;
  const view = useTableView(items, {
    search: (job) => `${job.table_name} ${job.source_name} ${methodLabel(job)} ${jobStatus(job).label}`,
    predicate: (job) => matchesJobFilter(job, filter),
    filters: [{ id: 'method', label: 'Cách nạp', value: (job) => methodLabel(job) }],
    sorts: [
      { id: 'name', value: (job) => job.table_name },
      { id: 'method', value: (job) => methodLabel(job) },
      { id: 'lag', value: (job) => job.lag_seconds },
      { id: 'rows', value: (job) => job.rows_today },
      { id: 'status', value: (job) => jobStatus(job).label },
    ],
  });

  const [pausing, setPausing] = useState<IngestionJob | null>(null);
  const [reason, setReason] = useState('');
  const [pauseError, setPauseError] = useState<string>();

  const act = async (job: IngestionJob, verb: 'run' | 'pause' | 'resume', why?: string) => {
    const done = { run: 'Đã chạy lại', pause: 'Đã tạm dừng', resume: 'Đã tiếp tục' }[verb];
    try {
      await controlJob(job.id, verb, why);
      toast({ tone: 'success', message: `${done} ${job.table_name}.` });
      jobs.reload();
      return true;
    } catch (e) {
      toast({
        tone: 'error',
        message: `Không thực hiện được với ${job.table_name}. ${(e as Error).message}`,
      });
      return false;
    }
  };

  const pause = async () => {
    if (!pausing) return;
    if (!reason.trim()) return setPauseError('Ghi lý do tạm dừng.');
    if (await act(pausing, 'pause', reason.trim())) setPausing(null);
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
            <LinkButton area="data" page="JOB_NEW" kind="primary">
              Tạo job
            </LinkButton>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl max-[767px]:px-da-lg">
        {/* On a phone the filter scrolls sideways instead of pushing the page wider. */}
        <div className="flex flex-wrap items-center gap-da-md max-[639px]:overflow-x-auto">
          <TableToolbar view={view} placeholder="Tìm job, nguồn…" searchLabel="Tìm job" />
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
          <span className="whitespace-nowrap text-muted-foreground da-body-small max-[639px]:hidden">
            24 giờ qua
          </span>
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
                  {/* Narrower screens drop columns from the right; what a dropped column said
                      moves under the job's name, so the name column never collapses. */}
                  <SortableTh
                    view={view}
                    column="name"
                    className="px-da-lg py-da-md font-normal max-[639px]:px-da-sm"
                  >
                    Job
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="method"
                    className="w-[120px] px-da-md py-da-md font-normal max-[1023px]:hidden"
                  >
                    Cách nạp
                  </SortableTh>
                  <th className="w-[316px] px-da-md py-da-md font-normal max-[1279px]:hidden">
                    24 giờ qua
                  </th>
                  <SortableTh
                    view={view}
                    column="lag"
                    className="w-[110px] px-da-md py-da-md font-normal max-[639px]:hidden"
                  >
                    Độ trễ
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="rows"
                    className="w-[110px] px-da-md py-da-md font-normal max-[767px]:hidden"
                  >
                    Dòng hôm nay
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="status"
                    className="w-[200px] px-da-md py-da-md font-normal max-[639px]:w-[132px]"
                  >
                    Trạng thái
                  </SortableTh>
                  <SortableTh view={view} className="w-[44px] px-da-md py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((job) => {
                  const status = jobStatus(job);
                  return (
                    <tr key={job.id} className="border-b border-border">
                      <td className="px-da-lg py-[9px] max-[639px]:px-da-sm">
                        <div className="flex min-w-0 flex-col gap-[2px]">
                          <span className="truncate text-foreground da-mono">{job.table_name}</span>
                          <span className="truncate text-muted-foreground da-caption">
                            {job.source_name}
                          </span>
                          <span className="hidden truncate text-muted-foreground da-caption max-[1023px]:block">
                            {methodLabel(job)}
                            <span className="hidden max-[767px]:inline">
                              {' · '}
                              {formatRows(job.rows_today)} dòng
                            </span>
                            <span className="hidden max-[639px]:inline">
                              {' · trễ '}
                              {job.lag_seconds === null ? '—' : formatLag(job.lag_seconds)}
                            </span>
                          </span>
                        </div>
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-body-small max-[1023px]:hidden">
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
                          'px-da-md py-[9px] da-mono max-[639px]:hidden',
                          job.late ? 'text-warning' : 'text-foreground',
                        )}
                      >
                        {job.lag_seconds === null ? '—' : formatLag(job.lag_seconds)}
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-mono max-[767px]:hidden">
                        {formatRows(job.rows_today)}
                      </td>
                      <td className="px-da-md py-[9px]">
                        <StatusDot
                          tone={status.tone}
                          className="max-[639px]:items-start max-[639px]:whitespace-normal"
                        >
                          {status.label}
                        </StatusDot>
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
                                  onSelect: () => {
                                    setReason('');
                                    setPauseError(undefined);
                                    setPausing(job);
                                  },
                                },
                          ]}
                        />
                      </td>
                    </tr>
                  );
                })}
                {view.total === 0 && (
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
        <TablePager view={view} />
        <p className="text-muted-foreground da-caption">
          Mỗi ô là một giờ: xanh là chạy thành công, đỏ là lỗi, xám là không có lần chạy. Job trễ
          quá hạn độ mới sẽ báo người phụ trách nguồn và đánh dấu số liệu liên quan là “có thể cũ”.
        </p>
        <ConfirmDialog
          open={pausing !== null}
          size="md"
          tone="primary"
          title={`Tạm dừng ${pausing?.table_name ?? ''}?`}
          confirmLabel="Tạm dừng"
          error={pauseError}
          onConfirm={() => void pause()}
          onCancel={() => setPausing(null)}
        >
          <div className="flex flex-col gap-da-md">
            <p>Job không chạy cho tới khi được tiếp tục. Lý do được ghi vào nhật ký kiểm toán.</p>
            <Field label="Lý do">
              <Input
                className="w-full"
                value={reason}
                placeholder="Ví dụ: nguồn đang bảo trì"
                onChange={(e) => setReason(e.target.value)}
              />
            </Field>
          </div>
        </ConfirmDialog>
      </div>
    </div>
  );
}

function hoursLabel(job: IngestionJob): string {
  const ok = job.hours.filter((h) => h === 'ok').length;
  const errors = job.hours.filter((h) => h === 'error').length;
  return `24 giờ qua: ${ok} giờ chạy thành công, ${errors} giờ lỗi`;
}
