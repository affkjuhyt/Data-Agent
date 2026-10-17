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
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P06 · Lưu thành bảng & lịch chạy" (2288:17996, UC-06-02, UC-06-03): the pipeline's output
 * becomes a derived table — name, Data Space, write mode (30 versions kept) — and gets its
 * schedule: after the input job, by the clock, or by hand, with retries and who to tell. The
 * right column shows the table's structure (which step makes each column) and who reads it,
 * since changing the structure of a table in use notifies their owners.
 */
const WRITE_MODES: { value: WriteMode; label: string }[] = [
  { value: 'replace', label: 'Thay toàn bộ mỗi lần chạy · giữ 30 phiên bản' },
  { value: 'append', label: 'Thêm vào cuối mỗi lần chạy · giữ 30 phiên bản' },
  { value: 'merge', label: 'Gộp theo khoá · giữ 30 phiên bản' },
];

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

const RETRIES = [
  { value: '2', label: '2 lần · cách 10 phút' },
  { value: '3', label: '3 lần · cách 10 phút' },
  { value: '0', label: 'Không thử lại' },
];

export default function PipelineSavePage() {
  const { navigate, routeId } = useAppState();
  const pipeline = useV1(routeId ? () => getPipeline(routeId) : null, [routeId]);
  const draft = useV1(routeId ? () => getOutputDraft(routeId) : null, [routeId]);
  const spaces = useV1(listDataSpaces, []);
  const toast = useToast();

  const [table, setTable] = useState('');
  const [space, setSpace] = useState<string | null>(null);
  const [writeMode, setWriteMode] = useState<WriteMode>('replace');
  const [mode, setMode] = useState<ScheduleMode>('after_source');
  const [afterJob, setAfterJob] = useState<string | null>(null);
  const [cron, setCron] = useState(CRONS[0].value);
  const [retries, setRetries] = useState('2');
  const [notify, setNotify] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    const d: OutputDraft | undefined = draft.data;
    if (!d) return;
    setTable(d.table_name);
    setSpace(d.data_space_id);
    setWriteMode(d.write_mode);
    setAfterJob(d.input_jobs[0]?.value ?? null);
    setNotify(d.notify_options[0]?.value ?? null);
  }, [draft.data]);

  const name = pipeline.data?.name ?? '…';

  const save = async () => {
    if (!routeId || !space) return;
    if (!table.trim()) {
      setError('Đặt tên bảng trước khi lưu.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const result = await publishPipeline(routeId, {
        table_name: table.trim(),
        data_space_id: space,
        write_mode: writeMode,
        schedule: {
          mode,
          after_job_id: mode === 'after_source' ? afterJob : null,
          cron: mode === 'cron' ? cron : null,
          retries: Number(retries),
          notify,
        },
      });
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
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
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
                      <Select
                        label="Data Space"
                        options={(spaces.data?.items ?? []).map((s) => ({
                          value: s.id,
                          label: s.name,
                        }))}
                        value={space}
                        onChange={setSpace}
                      />
                    </Field>
                  </div>
                </div>
                <Field label="Cách ghi">
                  <Select
                    label="Cách ghi"
                    options={WRITE_MODES}
                    value={writeMode}
                    onChange={(v) => setWriteMode(v)}
                  />
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
                    <div className="min-w-0 flex-1">
                      <Field label="Báo khi lỗi">
                        <Select
                          label="Báo khi lỗi"
                          options={d.notify_options}
                          value={notify}
                          onChange={setNotify}
                        />
                      </Field>
                    </div>
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
