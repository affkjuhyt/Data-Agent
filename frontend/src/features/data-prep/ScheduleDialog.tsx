import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import {
  getPipelineSchedule,
  previewSchedule,
  savePipelineSchedule,
  type PipelineSchedule,
  type ScheduleKind,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "v3.1 · Hộp thoại · Lịch chạy" (2239:15581, UC-06-03): when a pipeline runs, plus three
 * guards — skip bank holidays, never overlap runs, retry then tell someone. "Dự kiến" is the
 * server's reading of the schedule against the business calendar, refreshed on every change,
 * so the screen never guesses a date.
 */
export function ScheduleDialog({
  pipelineId,
  tableName,
  onClose,
  onSaved,
}: {
  /** null = closed. */
  pipelineId: string | null;
  tableName: string;
  onClose: () => void;
  onSaved: (scheduleText: string) => void;
}) {
  const loaded = useV1(
    ['getPipelineSchedule', pipelineId],
    pipelineId ? () => getPipelineSchedule(pipelineId) : null,
  );
  const [draft, setDraft] = useState<PipelineSchedule>();
  const [preview, setPreview] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setDraft(loaded.data);
    setError(undefined);
  }, [loaded.data]);

  useEffect(() => {
    if (!pipelineId || !draft) return;
    let alive = true;
    previewSchedule(pipelineId, draft)
      .then((r) => alive && setPreview(r.text))
      .catch(() => alive && setPreview(undefined));
    return () => {
      alive = false;
    };
  }, [pipelineId, draft]);

  const set = (patch: Partial<PipelineSchedule>) => draft && setDraft({ ...draft, ...patch });

  const save = async () => {
    if (!pipelineId || !draft) return;
    setBusy(true);
    setError(undefined);
    try {
      const { schedule_text } = await savePipelineSchedule(pipelineId, draft);
      onSaved(schedule_text);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const kinds: { value: ScheduleKind; label: string }[] = [
    { value: 'daily', label: 'Hằng ngày lúc 06:30' },
    {
      value: 'after_source',
      label: `Khi nguồn có dữ liệu mới (${draft?.after_source_label ?? 'sau job nạp'})`,
    },
    { value: 'business_hours', label: 'Hằng giờ trong giờ làm việc' },
    { value: 'cron', label: 'Biểu thức cron' },
  ];

  return (
    <Dialog
      open={pipelineId !== null}
      title={`Lịch chạy · ${tableName}`}
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} disabled={!draft} onClick={save}>
            Lưu lịch
          </Button>
        </>
      }
    >
      <p className="-mt-[10px] text-muted-foreground da-caption">
        Cùng hộp thoại này dùng để đặt lịch quét schema cho nguồn và lịch chạy pipeline.
      </p>
      {loaded.error && <Notice tone="error">Không tải được lịch. {loaded.error.message}</Notice>}
      {!draft && !loaded.error && <SkeletonRows count={4} label="Đang tải lịch…" />}
      {error && <Notice tone="error">{error}</Notice>}
      {draft && (
        <>
          <div role="radiogroup" aria-label="Khi nào chạy" className="flex flex-col gap-da-md">
            {kinds.map((k) => (
              <Radio
                key={k.value}
                name="schedule-kind"
                value={k.value}
                checked={draft.kind === k.value}
                onChange={() => set({ kind: k.value })}
              >
                {k.label}
              </Radio>
            ))}
            {draft.kind === 'cron' && (
              <Input
                aria-label="Biểu thức cron"
                placeholder="0 */2 * * 1-5"
                value={draft.cron ?? ''}
                onChange={(e) => set({ cron: e.target.value })}
                className="ml-[22px]"
              />
            )}
          </div>
          <Checkbox checked={draft.skip_holidays} onChange={(v) => set({ skip_holidays: v })}>
            Bỏ qua ngày nghỉ theo lịch của ngân hàng
          </Checkbox>
          <Checkbox checked={draft.no_overlap} onChange={(v) => set({ no_overlap: v })}>
            Không chạy chồng lượt (lượt trước chưa xong thì bỏ lượt này)
          </Checkbox>
          <Checkbox checked={draft.retry_and_notify} onChange={(v) => set({ retry_and_notify: v })}>
            Khi lỗi: thử lại 3 lần cách 10 phút, rồi báo {draft.notify_label}
          </Checkbox>
          {preview && (
            <div className="flex flex-col gap-[2px] border-b border-border p-[10px]">
              <p className="text-muted-foreground da-overline">Dự kiến</p>
              <p className="text-foreground da-caption">{preview}</p>
            </div>
          )}
        </>
      )}
    </Dialog>
  );
}
