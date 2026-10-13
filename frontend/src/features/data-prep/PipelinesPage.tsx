import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Menu } from '../../design-system/Menu';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatDate, formatTime } from '../../lib/formatVN';
import {
  createPipeline,
  listPipelines,
  runPipeline,
  type Pipeline,
  type PipelineStatus,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';
import { ScheduleDialog } from './ScheduleDialog';

/*
 * "P06 · Pipeline · Danh sách" (2288:17612, UC-06-03): every transformation pipeline with its
 * inputs and output, schedule, last run and state. Pipelines run on Temporal after their inputs
 * load; when an input job has not finished they wait instead of running on stale data. A row's
 * name opens the conversation that builds it; its menu runs it now or edits its schedule.
 */
const STATUS: Record<
  PipelineStatus,
  { tone: 'success' | 'error' | 'info' | 'warning' | 'idle'; label: string }
> = {
  succeeded: { tone: 'success', label: 'Thành công' },
  failed: { tone: 'error', label: 'Lỗi' },
  running: { tone: 'info', label: 'Đang chạy' },
  waiting_input: { tone: 'warning', label: 'Chờ job nạp' },
  draft: { tone: 'idle', label: 'Bản nháp' },
  active: { tone: 'success', label: 'Đang bật' },
  paused: { tone: 'warning', label: 'Tạm dừng' },
};

const isToday = (iso: string) => new Date(iso).toDateString() === new Date().toDateString();

function lastRunText(p: Pipeline): string {
  if (!p.last_run) return '—';
  const { started_at, duration_s } = p.last_run;
  // Runs before today show the day only, as in the design ("01/10").
  if (!isToday(started_at)) return formatDate(started_at).slice(0, 5);
  return `${formatTime(started_at)} · ${Math.max(1, Math.round(duration_s / 60))} phút`;
}

export default function PipelinesPage() {
  const { navigate } = useAppState();
  const pipelines = useV1(listPipelines, []);
  const toast = useToast();
  const [scheduling, setScheduling] = useState<Pipeline | null>(null);
  const [creating, setCreating] = useState(false);

  const items = pipelines.data?.items ?? [];
  const count = (s: PipelineStatus) => items.filter((p) => p.status === s).length;

  const runNow = async (p: Pipeline) => {
    try {
      await runPipeline(p.id);
      toast({ tone: 'info', message: `Đang chạy ${p.name}` });
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy được: ${(e as Error).message}` });
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Pipeline']}
        title="Pipeline"
        description={
          pipelines.data
            ? [
                `${items.length} pipeline`,
                'chạy bằng Temporal',
                `${count('succeeded')} thành công lần gần nhất`,
                `${count('waiting_input')} đang chờ job nạp`,
              ].join(' · ')
            : undefined
        }
        actions={
          <Button kind="primary" onClick={() => setCreating(true)}>
            Pipeline mới
          </Button>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pb-da-2xl pt-da-xl">
        <AsyncRegion
          state={pipelines}
          what="pipeline"
          skeletonRows={5}
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState
              icon="layers"
              title="Chưa có pipeline"
              description="Mô tả việc cần làm với một bảng, Agent Chuẩn bị dữ liệu sẽ dựng các bước."
              action={
                <Button kind="primary" onClick={() => setCreating(true)}>
                  Pipeline mới
                </Button>
              }
            />
          }
        >
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">Pipeline</th>
                  <th className="w-[300px] px-da-lg py-da-md font-normal">Đầu vào → đầu ra</th>
                  <th className="w-[170px] px-da-lg py-da-md font-normal">Lịch</th>
                  <th className="w-[150px] px-da-lg py-da-md font-normal">Lần chạy gần nhất</th>
                  <th className="w-[170px] px-da-lg py-da-md font-normal">Trạng thái</th>
                  <th className="w-[52px] px-da-lg py-da-md font-normal">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-b border-border da-body-small">
                    <td className="px-da-lg py-[9px]">
                      <button
                        type="button"
                        onClick={() => navigate('data', 'PIPELINE', p.id)}
                        className="text-left text-foreground da-body-strong hover:underline"
                      >
                        {p.name}
                      </button>
                      <p className="text-muted-foreground da-caption">
                        {p.owner_name} · {p.step_count} bước
                      </p>
                    </td>
                    <td className="px-da-lg py-[9px] text-foreground da-mono">
                      {p.inputs.join(' + ')} → {p.output_table ?? '—'}
                    </td>
                    <td className="px-da-lg py-[9px] text-foreground">{p.schedule_text}</td>
                    <td className="px-da-lg py-[9px] text-muted-foreground">{lastRunText(p)}</td>
                    <td className="px-da-lg py-[9px]">
                      <StatusDot tone={STATUS[p.status].tone}>{STATUS[p.status].label}</StatusDot>
                    </td>
                    <td className="px-da-lg py-[9px]">
                      <Menu
                        label={`Thao tác với ${p.name}`}
                        items={[
                          {
                            label: 'Mở',
                            icon: 'external-link',
                            onSelect: () => navigate('data', 'PIPELINE', p.id),
                          },
                          { label: 'Chạy ngay', icon: 'play', onSelect: () => void runNow(p) },
                          {
                            label: 'Lịch chạy…',
                            icon: 'calendar',
                            onSelect: () => setScheduling(p),
                          },
                          {
                            label: 'Dòng dữ liệu',
                            icon: 'lineage',
                            onSelect: () => navigate('data', 'LINEAGE'),
                          },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          Pipeline chạy theo phụ thuộc: khi job nạp đầu vào chưa xong, pipeline chờ thay vì chạy
          trên dữ liệu cũ.
        </p>
      </div>
      <ScheduleDialog
        pipelineId={scheduling?.id ?? null}
        tableName={scheduling?.output_table ?? scheduling?.name ?? ''}
        onClose={() => setScheduling(null)}
        onSaved={(text) => {
          setScheduling(null);
          toast({ tone: 'success', message: `Đã lên lịch · ${text}` });
          pipelines.reload();
        }}
      />
      <NewPipelineDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(p) => {
          setCreating(false);
          navigate('data', 'PIPELINE', p.id);
        }}
      />
    </div>
  );
}

function NewPipelineDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (p: Pipeline) => void;
}) {
  const [name, setName] = useState('');
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const create = async () => {
    if (!name.trim() || !input.trim()) {
      setError('Đặt tên và chọn bảng đầu vào.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      onCreated(await createPipeline({ name: name.trim(), input_table: input.trim() }));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Pipeline mới"
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} onClick={create}>
            Tạo và mô tả bước
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Tên pipeline">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="w-full" />
      </Field>
      <Field label="Bảng đầu vào" hint="Ví dụ raw.ld_loans. Agent chỉ đọc, không ghi vào nguồn.">
        <Input value={input} onChange={(e) => setInput(e.target.value)} className="w-full" />
      </Field>
    </Dialog>
  );
}
