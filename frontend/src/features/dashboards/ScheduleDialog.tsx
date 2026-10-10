import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Input } from '../../design-system/Input';
import { Radio } from '../../design-system/Radio';
import { useToast } from '../../design-system/Toast';
import {
  previewDashboardSchedule,
  saveDashboardSchedule,
  type Dashboard,
  type ScheduleInput,
  type ScheduleKind,
} from '../../lib/v1/dashboards';
import { FormDialog } from '../sessions/FormDialog';

/*
 * "v3.1 · Hộp thoại · Lịch chạy" (2239:15781, UC-06-03, UC-08-03) for a dashboard's send
 * schedule: when to send, three safety options, and the next runs in words ("Dự kiến"),
 * computed by the server for the schedule as edited, before it is saved.
 */
const WEEKDAY = ['', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy', 'Chủ Nhật'];

const DEFAULT: ScheduleInput = {
  kind: 'weekly',
  time: '07:00',
  weekday: 1,
  cron: null,
  skip_holidays: true,
  no_overlap: true,
  retry_on_error: true,
};

export function ScheduleDialog({
  open,
  dashboard,
  onClose,
  onSaved,
}: {
  open: boolean;
  dashboard: Dashboard;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [draft, setDraft] = useState<ScheduleInput>(DEFAULT);
  const [preview, setPreview] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    const { text: _text, ...saved } = dashboard.schedule ?? { ...DEFAULT, text: '' };
    setDraft(saved);
    setError(undefined);
  }, [open, dashboard.schedule]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    previewDashboardSchedule(dashboard.id, draft)
      .then((r) => alive && setPreview(r.text))
      .catch(() => alive && setPreview(''));
    return () => {
      alive = false;
    };
  }, [open, dashboard.id, draft]);

  const time = draft.time ?? '07:00';
  const kinds: { value: ScheduleKind; label: string }[] = [
    { value: 'weekly', label: `Hằng tuần, ${WEEKDAY[draft.weekday ?? 1]} lúc ${time}` },
    { value: 'daily', label: `Hằng ngày lúc ${time}` },
    { value: 'on_new_data', label: 'Khi nguồn có dữ liệu mới' },
    { value: 'hourly_business', label: 'Hằng giờ trong giờ làm việc' },
    { value: 'cron', label: 'Biểu thức cron' },
  ];

  const set = (patch: Partial<ScheduleInput>) => setDraft((prev) => ({ ...prev, ...patch }));

  const save = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const saved = await saveDashboardSchedule(dashboard.id, draft);
      toast({ tone: 'success', message: `Đã lưu lịch gửi: ${saved.text}` });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <FormDialog
      open={open}
      title={`Lịch gửi · ${dashboard.title}`}
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={saving} onClick={save}>
            Lưu lịch
          </Button>
        </>
      }
    >
      <fieldset className="flex flex-col gap-da-lg">
        <legend className="sr-only">Khi nào gửi</legend>
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
            placeholder="0 7 * * 1"
            value={draft.cron ?? ''}
            onChange={(e) => set({ cron: e.target.value })}
            className="da-mono"
          />
        )}
      </fieldset>
      <div className="flex flex-col gap-da-lg">
        <Checkbox checked={draft.skip_holidays} onChange={(v) => set({ skip_holidays: v })}>
          Bỏ qua ngày nghỉ theo lịch của ngân hàng
        </Checkbox>
        <Checkbox checked={draft.no_overlap} onChange={(v) => set({ no_overlap: v })}>
          Không chạy chồng lượt (lượt trước chưa xong thì bỏ lượt này)
        </Checkbox>
        <Checkbox checked={draft.retry_on_error} onChange={(v) => set({ retry_on_error: v })}>
          Khi lỗi: thử lại 3 lần cách 10 phút, rồi báo Kỹ sư dữ liệu
        </Checkbox>
      </div>
      {preview && (
        <div className="flex flex-col gap-[2px] border-b border-border p-[10px]">
          <span className="text-muted-foreground da-overline">Dự kiến</span>
          <p aria-live="polite" className="text-foreground da-caption">
            {preview}
          </p>
        </div>
      )}
      {error && <p className="text-error da-caption">{error}</p>}
    </FormDialog>
  );
}
