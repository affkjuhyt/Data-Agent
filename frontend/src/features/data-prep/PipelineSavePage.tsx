import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { listDataSpaces } from '../../lib/v1/api';
import {
  getOutputDraft,
  getPipeline,
  publishPipeline,
  type OutputDraft,
  type ScheduleMode,
  type WriteMode,
} from '../../lib/v1/dataPrep';
import { useV1, useV1Mutation } from '../../lib/v1/useV1';

/*
 * "P06 · Lưu thành bảng & lịch chạy" (2288:17996, UC-06-02, UC-06-03): the pipeline's output
 * becomes a derived table and gets its schedule: after the input job, by the clock, or by hand,
 * retrying or not. The right column shows the table's structure (which step makes each column)
 * and who reads it, since changing the structure of a table in use notifies their owners.
 * :materialize takes only the table's name: the table lands in the pipeline's own Data Space
 * and the worker replaces it on every run, and a failure tells the pipeline's owner. Those are
 * stated, not offered as choices the api would drop.
 */
const WRITE_MODE_TEXT: Record<WriteMode, string> = {
  replace: 'Thay toàn bộ mỗi lần chạy',
  append: 'Thêm vào cuối mỗi lần chạy',
  merge: 'Gộp theo khoá',
};

const MODES: { value: ScheduleMode; label: string }[] = [
  { value: 'after_source', label: 'Sau khi nguồn cập nhật' },
  { value: 'cron', label: 'Theo giờ' },
  { value: 'manual', label: 'Thủ công' },
];

const CRONS = [
  { value: '0 */2 * * *', label: 'Mỗi 2 giờ' },
  { value: '0 * * * *', label: 'Mỗi giờ' },
  { value: '30 6 * * *', label: 'Hằng ngày 06:30' },
];

// ScheduleRequest.on_failure: retry_then_notify or notify; it carries no count or interval.
const RETRIES = [
  { value: '1', label: 'Thử lại rồi báo' },
  { value: '0', label: 'Không thử lại, chỉ báo' },
];

export default function PipelineSavePage() {
  const { navigate, routeId } = useAppState();
  const pipeline = useV1(['getPipeline', routeId], routeId ? () => getPipeline(routeId) : null);
  const draft = useV1(['getOutputDraft', routeId], routeId ? () => getOutputDraft(routeId) : null);
  const spaces = useV1(['listDataSpaces'], listDataSpaces);
  const toast = useToast();

  const [table, setTable] = useState('');
  const [mode, setMode] = useState<ScheduleMode>('after_source');
  const [afterJob, setAfterJob] = useState<string | null>(null);
  const [cron, setCron] = useState(CRONS[0].value);
  const [retries, setRetries] = useState('1');
  // An empty table name is caught here; the server's answer lands in the mutation.
  const [invalid, setInvalid] = useState<string>();

  useEffect(() => {
    const d: OutputDraft | undefined = draft.data;
    if (!d) return;
    setTable(d.table_name);
    setAfterJob(d.input_jobs[0]?.value ?? null);
  }, [draft.data]);
  const spaceName = (id: string) =>
    spaces.data?.items.find((s) => s.id === id)?.name ?? 'Data Space của pipeline';

  const name = pipeline.data?.name ?? '…';

  const publish = useV1Mutation(
    ({ id }: { id: string }) =>
      publishPipeline(id, {
        table_name: table.trim(),
        schedule: {
          mode,
          after_job_id: mode === 'after_source' ? afterJob : null,
          cron: mode === 'cron' ? cron : null,
          retries: Number(retries),
        },
      }),
    {
      onSuccess: (result) => {
        toast({
          tone: 'success',
          message: `Đã tạo bảng ${result.table.name}`,
          action: { label: 'Mở', onClick: () => navigate('data', 'TABLE', result.table.id) },
        });
        toast({
          tone: 'success',
          message: `Đã lên lịch · ${result.schedule_text}`,
          action: { label: 'Xem', onClick: () => navigate('data', 'PIPELINES') },
        });
        navigate('data', 'PIPELINES');
      },
    },
  );
  const busy = publish.pending;
  const error = invalid ?? publish.error?.message;

  const save = () => {
    if (!routeId) return;
    if (!table.trim()) {
      setInvalid('Đặt tên bảng trước khi lưu.');
      return;
    }
    setInvalid(undefined);
    publish.run({ id: routeId });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Pipeline', name, 'Lưu']}
        title="Lưu thành bảng dẫn xuất"
        actions={
          <>
            <Button
              kind="ghost"
              onClick={() =>
                routeId ? navigate('data', 'PIPELINE', routeId) : navigate('data', 'PIPELINES')
              }
            >
              Quay lại
            </Button>
            <Button kind="primary" loading={busy} disabled={!draft.data} onClick={save}>
              Lưu và lên lịch
            </Button>
          </>
        }
      />
      <AsyncRegion state={draft} what="cấu trúc bảng" skeletonRows={6}>
        {(d) => (
          <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
            <div className="flex w-[620px] shrink-0 flex-col border-r border-border pl-da-3xl pr-da-2xl max-[1023px]:w-full">
              {error && (
                <Notice tone="error" className="mt-da-xl">
                  {error}
                </Notice>
              )}
              <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                <h2 className="text-foreground da-heading-3">Bảng</h2>
                <div className="flex gap-da-lg">
                  <div className="min-w-0 flex-1">
                    <Field label="Tên bảng">
                      <Input
                        value={table}
                        onChange={(e) => setTable(e.target.value)}
                        className="w-full"
                      />
                    </Field>
                  </div>
                  <div className="min-w-0 flex-1">
                    <Field label="Data Space">
                      <Input readOnly value={spaceName(d.data_space_id)} className="w-full" />
                    </Field>
                  </div>
                </div>
                <Field label="Cách ghi">
                  <Input readOnly value={WRITE_MODE_TEXT[d.write_mode]} className="w-full" />
                </Field>
              </section>
              <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                <h2 className="text-foreground da-heading-3">Lịch chạy</h2>
                <div>
                  <Segmented
                    variant="outline"
                    label="Khi nào chạy"
                    items={MODES}
                    value={mode}
                    onChange={(v) => setMode(v)}
                  />
                </div>
                {mode === 'after_source' && (
                  <Field
                    label="Chạy sau"
                    hint="Nếu job nạp lỗi hoặc trễ quá 2 giờ, pipeline không chạy và báo người phụ trách."
                  >
                    <Select
                      label="Chạy sau"
                      options={d.input_jobs}
                      value={afterJob}
                      onChange={setAfterJob}
                    />
                  </Field>
                )}
                {mode === 'cron' && (
                  <Field label="Tần suất">
                    <Select label="Tần suất" options={CRONS} value={cron} onChange={setCron} />
                  </Field>
                )}
                {mode !== 'manual' && (
                  <div className="flex gap-da-lg">
                    <div className="min-w-0 flex-1">
                      <Field label="Thử lại">
                        <Select
                          label="Thử lại"
                          options={RETRIES}
                          value={retries}
                          onChange={setRetries}
                        />
                      </Field>
                    </div>
                    {d.notify_options[0] && (
                      <div className="min-w-0 flex-1">
                        <Field label="Báo khi lỗi">
                          <Input readOnly value={d.notify_options[0].label} className="w-full" />
                        </Field>
                      </div>
                    )}
                  </div>
                )}
              </section>
            </div>
            <div className="flex min-w-0 flex-1 flex-col gap-[10px] bg-background px-da-3xl pt-[20px]">
              <h2 className="text-foreground da-heading-3">Cấu trúc bảng</h2>
              {d.columns_status === 'failed' ? (
                <Notice tone="warning">
                  Chưa đọc được cấu trúc: một bước không chạy được trên nguồn. Sửa bước rồi mở lại.
                </Notice>
              ) : d.columns_status === 'pending' ? (
                <p className="text-muted-foreground da-caption">Đang chạy các bước trên nguồn…</p>
              ) : (
                <ColumnTable columns={d.columns} />
              )}
              {d.consumers_text && (
                <>
                  <h2 className="text-foreground da-heading-3">Ai dùng bảng này</h2>
                  <p className="text-foreground da-body-small">{d.consumers_text}</p>
                </>
              )}
            </div>
          </div>
        )}
      </AsyncRegion>
    </div>
  );
}

function ColumnTable({ columns }: { columns: OutputDraft['columns'] }) {
  const view = useTableView(columns, {
    search: (c) => `${c.name} ${c.type} ${c.from_step}`,
    filters: [{ id: 'type', label: 'Kiểu', value: (c) => c.type }],
    sorts: [
      { id: 'name', value: (c) => c.name },
      { id: 'type', value: (c) => c.type },
      { id: 'from', value: (c) => c.from_step },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm cột…" searchLabel="Tìm cột" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh
              view={view}
              column="name"
              className="w-[204px] px-da-lg py-da-md font-normal"
            >
              Cột
            </SortableTh>
            <SortableTh
              view={view}
              column="type"
              className="w-[164px] px-da-lg py-da-md font-normal"
            >
              Kiểu
            </SortableTh>
            <SortableTh view={view} column="from" className="px-da-lg py-da-md font-normal">
              Từ bước
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((c) => (
            <tr key={c.name} className="border-b border-border">
              <td className="px-da-lg py-[9px] text-foreground da-mono">{c.name}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">{c.type}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">
                {c.from_step === 0 ? 'đầu vào' : c.from_step}
                {c.is_new && ' · tính mới'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
