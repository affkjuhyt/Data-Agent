import { useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  createJob,
  dryRunJob,
  listJobCandidates,
  type DryRunResult,
  type JobCandidate,
  type JobMethod,
  type NewJob,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P04 · Job nạp · Thiết lập" (2280:17217, UC-04-06), opened by "Tạo job" on Job nạp:
 * how to load (CDC, schedule, incremental by a cursor column, new files), schedule and limits,
 * what to do when data has problems, the target; on the right the estimate and the result of a
 * 1,000-row dry run. /data/job_new/<dataset id> sets up that table; without an id the first
 * table that can get a job, with a picker when there are several (not in Figma, which shows one).
 */
const METHOD_HELP: Record<JobMethod, string> = {
  cdc: 'Đọc nhật ký thay đổi của nguồn, gần như tức thời; cần nguồn bật CDC.',
  schedule: 'Mỗi lần chạy đọc lại toàn bảng theo lịch.',
  incremental: 'Mỗi lần chạy chỉ lấy các dòng có giá trị con trỏ lớn hơn lần trước.',
  continuous: 'Đọc liên tục từ luồng sự kiện.',
  new_files: 'Mỗi lần chạy chỉ đọc các tệp mới xuất hiện trong thư mục.',
};

const SCHEDULES: { value: NewJob['schedule']; label: string }[] = [
  { value: 'every_2h_daytime', label: 'Mỗi 2 giờ, 06:00 – 22:00' },
  { value: 'hourly', label: 'Mỗi giờ' },
  { value: 'daily_night', label: 'Hằng ngày lúc 02:00' },
];

const SLAS = [2, 4, 8, 24].map((h) => ({
  value: String(h),
  label: `Trễ quá ${h} giờ thì cảnh báo`,
}));

const RETRIES: { value: NewJob['retry']; label: string }[] = [
  { value: 'backoff_3', label: '3 lần · cách 5, 15, 60 phút' },
  { value: 'once', label: '1 lần sau 15 phút' },
  { value: 'none', label: 'Không thử lại' },
];

const ON_SCHEMA: { value: NewJob['on_schema_change']; label: string }[] = [
  {
    value: 'accept_new_columns',
    label: 'Chấp nhận cột mới, tạm dừng nếu đổi kiểu hoặc xoá cột',
  },
  { value: 'pause_and_ask', label: 'Tạm dừng với mọi thay đổi và hỏi người phụ trách' },
  { value: 'continue_and_notify', label: 'Nạp tiếp và báo người phụ trách' },
];

const ON_INVALID: { value: NewJob['on_invalid_record']; label: string }[] = [
  { value: 'quarantine_30d', label: 'Đưa vào hàng bản ghi lỗi, giữ 30 ngày' },
  { value: 'skip_and_log', label: 'Bỏ qua và ghi nhật ký' },
  { value: 'stop', label: 'Dừng job' },
];

export default function JobSetupPage() {
  const { routeId } = useAppState();
  const candidates = useV1(listJobCandidates, []);
  return (
    <AsyncRegion
      state={candidates}
      what="bảng có thể tạo job"
      skeletonRows={8}
      isEmpty={(page) => page.items.length === 0}
      empty={
        <div className="flex flex-col">
          <PageHeader bordered breadcrumb={['Dữ liệu', 'Job nạp', 'Mới']} title="Job nạp mới" />
          <EmptyState
            className="m-da-3xl"
            icon="refresh"
            title="Không còn bảng nào cần job nạp"
            description="Mọi bảng của các nguồn nạp về đã có job. Kết nối thêm nguồn ở chế độ nạp về để tạo job mới."
          />
        </div>
      }
    >
      {(page) => (
        <JobForm
          candidates={page.items}
          initial={page.items.find((c) => c.dataset_id === routeId) ?? page.items[0]}
        />
      )}
    </AsyncRegion>
  );
}

function draftFor(candidate: JobCandidate): NewJob {
  return {
    dataset_id: candidate.dataset_id,
    method: candidate.suggested_method,
    cursor_column: candidate.cursor_columns[0]?.name ?? null,
    schedule: 'every_2h_daytime',
    freshness_sla_hours: 4,
    retry: 'backoff_3',
    no_overlap: true,
    on_schema_change: 'accept_new_columns',
    on_invalid_record: 'quarantine_30d',
  };
}

function JobForm({ candidates, initial }: { candidates: JobCandidate[]; initial: JobCandidate }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [candidate, setCandidate] = useState(initial);
  const [job, setJob] = useState<NewJob>(() => draftFor(initial));
  const [dryRun, setDryRun] = useState<DryRunResult | null>(null);
  const [busy, setBusy] = useState<'dry' | 'save' | null>(null);
  const checksView = useTableView(dryRun?.checks ?? [], {
    search: (check) => `${check.title} ${check.result}`,
    filters: [
      { id: 'result', label: 'Kết quả', value: (check) => (check.ok ? 'Đạt' : 'Chưa đạt') },
    ],
    sorts: [
      { id: 'title', value: (check) => check.title },
      { id: 'result', value: (check) => check.result },
    ],
    pageSize: 50,
  });

  // Any change makes the last dry run stale: it was read with another config.
  const update = (patch: Partial<NewJob>) => {
    setJob((j) => ({ ...j, ...patch }));
    setDryRun(null);
  };
  const pickTable = (id: string) => {
    const next = candidates.find((c) => c.dataset_id === id);
    if (!next) return;
    setCandidate(next);
    setJob(draftFor(next));
    setDryRun(null);
  };

  const incremental = job.method === 'incremental';
  const cursor = candidate.cursor_columns.find((c) => c.name === job.cursor_column);
  const ready = !incremental || Boolean(job.cursor_column);
  const canDryRun = true;

  const run = async () => {
    setBusy('dry');
    try {
      setDryRun(await dryRunJob(job, candidate));
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy thử được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    setBusy('save');
    try {
      await createJob(job, candidate);
      toast({ tone: 'success', message: `Đã tạo job nạp ${candidate.table_name}.` });
      navigate('data', 'JOBS');
    } catch (e) {
      toast({ tone: 'error', message: `Không lưu được job. ${(e as Error).message}` });
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Job nạp', 'Mới']}
        title={`Job nạp · ${candidate.table_name}`}
        actions={
          <>
            <Button kind="ghost" onClick={() => navigate('data', 'JOBS')}>
              Huỷ
            </Button>
            {canDryRun && (
              <Button
                loading={busy === 'dry'}
                disabled={!ready || busy !== null}
                onClick={() => void run()}
              >
                Chạy thử 1.000 dòng
              </Button>
            )}
            <Button
              kind="primary"
              loading={busy === 'save'}
              disabled={!ready || busy !== null}
              onClick={() => void save()}
            >
              Lưu job
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1023px]:flex-col">
        <form
          aria-label="Thiết lập job nạp"
          onSubmit={(e) => e.preventDefault()}
          className="w-[640px] shrink-0 border-r border-border pl-da-3xl pr-da-2xl max-[1023px]:w-full max-[1023px]:border-r-0"
        >
          {candidates.length > 1 && (
            <Section title="Bảng nguồn">
              <Select
                label="Bảng nguồn"
                value={candidate.dataset_id}
                onChange={pickTable}
                options={candidates.map((c) => ({
                  value: c.dataset_id,
                  label: `${c.table_name} · ${c.source_name}`,
                }))}
              />
            </Section>
          )}
          <Section title="Cách nạp">
            <Segmented<JobMethod>
              variant="outline"
              label="Cách nạp"
              value={job.method}
              onChange={(method) => update({ method })}
              items={[
                { value: 'cdc', label: 'Liên tục (CDC)' },
                { value: 'schedule', label: 'Theo lịch' },
                { value: 'incremental', label: 'Tăng dần' },
                { value: 'new_files', label: 'Tệp mới' },
              ]}
            />
            <p className="text-muted-foreground da-caption">{METHOD_HELP[job.method]}</p>
            {incremental && (
              <Row>
                <Field labelTone="muted" label="Cột con trỏ" hint={cursor?.hint ?? undefined}>
                  <Select
                    label="Cột con trỏ"
                    value={job.cursor_column}
                    onChange={(cursor_column) => update({ cursor_column })}
                    options={candidate.cursor_columns.map((c) => ({
                      value: c.name,
                      label: `${c.name} · ${c.data_type}`,
                    }))}
                  />
                </Field>
                <Field labelTone="muted" label="Giá trị hiện tại">
                  <Input mono className="w-full" readOnly value={candidate.cursor_value ?? '—'} />
                </Field>
              </Row>
            )}
          </Section>
          <Section title="Lịch và giới hạn">
            <Row>
              <Field labelTone="muted" label="Lịch">
                <Select
                  label="Lịch"
                  value={job.schedule}
                  onChange={(schedule) => update({ schedule })}
                  options={SCHEDULES}
                />
              </Field>
              <Field labelTone="muted" label="Hạn độ mới">
                <Select
                  label="Hạn độ mới"
                  value={String(job.freshness_sla_hours)}
                  onChange={(v) => update({ freshness_sla_hours: Number(v) })}
                  options={SLAS}
                />
              </Field>
            </Row>
            <Row>
              <Field labelTone="muted" label="Thử lại khi lỗi">
                <Select
                  label="Thử lại khi lỗi"
                  value={job.retry}
                  onChange={(retry) => update({ retry })}
                  options={RETRIES}
                />
              </Field>
              <Field
                labelTone="muted"
                label="Không chạy chồng"
                hint="Luôn bật: lần chạy sau chờ lần trước xong."
              >
                <Select
                  label="Không chạy chồng"
                  value="on"
                  onChange={() => undefined}
                  options={[{ value: 'on', label: 'Bật' }]}
                  disabled
                />
              </Field>
            </Row>
          </Section>
          <Section title="Khi dữ liệu có vấn đề">
            <Field labelTone="muted" label="Cấu trúc nguồn thay đổi">
              <Select
                label="Cấu trúc nguồn thay đổi"
                value={job.on_schema_change}
                onChange={(on_schema_change) => update({ on_schema_change })}
                options={ON_SCHEMA}
              />
            </Field>
            <Field labelTone="muted" label="Bản ghi không hợp lệ">
              <Select
                label="Bản ghi không hợp lệ"
                value={job.on_invalid_record}
                onChange={(on_invalid_record) => update({ on_invalid_record })}
                options={ON_INVALID}
              />
            </Field>
          </Section>
          <Section title="Đích">
            <Field labelTone="muted" label="Bảng đích">
              <Input mono className="w-full" readOnly value={candidate.target_text} />
            </Field>
          </Section>
        </form>
        <aside
          aria-label="Ước tính và chạy thử"
          className="flex min-w-0 flex-1 flex-col gap-[10px] bg-background px-da-3xl pt-[20px] max-[1023px]:border-t max-[1023px]:border-border max-[1023px]:pb-da-xl"
        >
          {candidate.estimate && (
            <>
              <h2 className="text-foreground da-heading-3">Ước tính</h2>
              <dl className="grid grid-cols-3 overflow-hidden rounded-lg border border-border bg-card">
                {[
                  ['Mỗi lần chạy', candidate.estimate.rows_per_run],
                  ['Thời gian', candidate.estimate.duration],
                  ['Tải lên nguồn', candidate.estimate.source_load],
                ].map(([label, value], i) => (
                  <div
                    key={label}
                    className={cn(
                      'flex flex-col gap-[2px] px-[14px] py-[10px]',
                      i < 2 && 'border-r border-border',
                    )}
                  >
                    <dt className="text-muted-foreground da-caption">{label}</dt>
                    <dd className="text-foreground da-body-strong">{value}</dd>
                  </div>
                ))}
              </dl>
            </>
          )}
          {canDryRun && (
            <>
              <h2 className="text-foreground da-heading-3">Chạy thử 1.000 dòng</h2>
              {dryRun ? (
                <>
                <TableToolbar
                  view={checksView}
                  placeholder="Tìm kiểm tra…"
                  searchLabel="Tìm kiểm tra"
                />
                <table className="w-full table-fixed border-collapse">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground da-caption">
                      <SortableTh view={checksView} column="title" className="px-da-lg py-da-md font-normal">
                        Kiểm tra
                      </SortableTh>
                      <SortableTh
                        view={checksView}
                        column="result"
                        className="w-[216px] px-da-md py-da-md font-normal"
                      >
                        Kết quả
                      </SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {checksView.rows.map((check) => (
                      <tr key={check.title} className="border-b border-border">
                        <td className="px-da-lg py-[9px] text-foreground da-body-small">
                          {check.title}
                        </td>
                        <td
                          className={cn(
                            'px-da-md py-[9px] da-body-small',
                            check.ok ? 'text-foreground' : 'text-warning',
                          )}
                        >
                          {check.result}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <TablePager view={checksView} />
                </>
              ) : (
                <p className="text-muted-foreground da-body-small">
                  Chưa chạy thử. Chạy thử đọc 1.000 dòng theo cấu hình bên trái và không ghi gì vào
                  bảng đích; dòng lỗi được đếm trước khi bạn lưu.
                </p>
              )}
            </>
          )}
          <p className="text-muted-foreground da-caption">
            UC-04-06 · job chạy bằng tài khoản chỉ đọc; không bao giờ xoá dữ liệu ở nguồn
          </p>
        </aside>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-[10px] border-b border-border py-da-xl">
      <legend className="float-left mb-[2px] w-full text-foreground da-heading-3">{title}</legend>
      {children}
    </fieldset>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="grid grid-cols-2 items-start gap-da-lg">{children}</div>;
}
