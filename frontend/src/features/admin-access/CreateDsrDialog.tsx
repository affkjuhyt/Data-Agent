import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { createDsr, type DsrKind } from '../../lib/v1/adminAccess';
import { addDays, dayMonth, isoDay } from './format';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · createDSR" (2095:8857): kind, subject, the day the request was received and the
 * deadline counted from it (DsrCreate). The subject identifier is sent once and hashed by the
 * server (dsr_request.subject_hash); it is not kept here. The frame's receiving channel is not
 * kept by the api, so it is not asked.
 */
export const DSR_KINDS: { value: DsrKind; label: string }[] = [
  { value: 'access', label: 'Truy cập dữ liệu' },
  { value: 'copy', label: 'Nhận bản sao' },
  { value: 'delete', label: 'Xoá dữ liệu' },
  { value: 'correct', label: 'Sửa dữ liệu' },
];

/** Deadlines counted from the day the request was received. */
const deadlines = (received: Date) =>
  [15, 30, 72].map((days) => ({
    value: String(days),
    label: `${days} ngày · ${dayMonth(addDays(days, received).toISOString())}`,
  }));

export function CreateDsrDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (dsr: { id: string; code: string }) => void;
}) {
  const [kind, setKind] = useState<DsrKind>('access');
  const [subject, setSubject] = useState('');
  const [received, setReceived] = useState(isoDay(new Date()));
  const [deadline, setDeadline] = useState('30');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setKind('access');
    setSubject('');
    setReceived(isoDay(new Date()));
    setDeadline('30');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!subject.trim()) {
      setError('Nhập định danh đã xác minh của chủ thể (CCCD hoặc mã khách hàng).');
      return;
    }
    setSending(true);
    setError(undefined);
    try {
      const receivedAt = new Date(`${received}T00:00:00`);
      const created = await createDsr({
        request_kind: kind,
        subject_identifier: subject.trim(),
        received_at: receivedAt.toISOString(),
        due_at: addDays(Number(deadline), receivedAt).toISOString(),
      });
      onCreated(created);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Tạo yêu cầu của chủ thể dữ liệu"
      description="Agent sẽ tìm dữ liệu ở mọi nơi; thao tác xoá luôn cần duyệt."
      submitLabel="Tạo yêu cầu"
      submitting={sending}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Loại">
        <Select<DsrKind> label="Loại" options={DSR_KINDS} value={kind} onChange={setKind} />
      </Field>
      <Field label="Chủ thể">
        <Input
          className="w-full"
          value={subject}
          autoComplete="off"
          placeholder="Số CCCD hoặc mã khách hàng đã xác minh"
          onChange={(e) => setSubject(e.target.value)}
        />
      </Field>
      <Field label="Ngày nhận">
        <Input
          type="date"
          aria-label="Ngày nhận"
          className="w-full"
          value={received}
          max={isoDay(new Date())}
          onChange={(e) => setReceived(e.target.value)}
        />
      </Field>
      <Field label="Hạn">
        <Select
          label="Hạn"
          options={deadlines(new Date(`${received}T00:00:00`))}
          value={deadline}
          onChange={setDeadline}
        />
      </Field>
    </Dialog>
  );
}
