import { useEffect, useState } from 'react';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { createDsr, type DsrKind, type DsrRequest } from '../../lib/v1/adminAccess';
import { addDays, dayMonth } from './format';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · createDSR" (2095:8857): kind, subject, receiving channel, deadline. The subject
 * identifier is sent once and hashed by the server (dsr_request.subject_hash); it is not kept here.
 */
export const DSR_KINDS: { value: DsrKind; label: string }[] = [
  { value: 'access', label: 'Truy cập dữ liệu' },
  { value: 'copy', label: 'Nhận bản sao' },
  { value: 'delete', label: 'Xoá dữ liệu' },
  { value: 'correct', label: 'Sửa dữ liệu' },
];

const CHANNELS = [
  { value: 'Quầy giao dịch Hà Nội', label: 'Quầy giao dịch Hà Nội' },
  { value: 'Quầy giao dịch TP.HCM', label: 'Quầy giao dịch TP.HCM' },
  { value: 'kênh pháp chế', label: 'Kênh pháp chế' },
  { value: 'email', label: 'Email' },
  { value: 'tổng đài', label: 'Tổng đài' },
];

const DEADLINES = [15, 30, 72].map((days) => ({
  value: String(days),
  label: `${days} ngày · ${dayMonth(addDays(days).toISOString())}`,
}));

export function CreateDsrDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (dsr: DsrRequest) => void;
}) {
  const [kind, setKind] = useState<DsrKind>('access');
  const [subject, setSubject] = useState('');
  const [channel, setChannel] = useState(CHANNELS[0].value);
  const [deadline, setDeadline] = useState('30');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setKind('access');
    setSubject('');
    setChannel(CHANNELS[0].value);
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
      const created = await createDsr({
        request_kind: kind,
        subject_identifier: subject.trim(),
        channel,
        due_at: addDays(Number(deadline)).toISOString(),
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
      <Field label="Kênh nhận">
        <Select label="Kênh nhận" options={CHANNELS} value={channel} onChange={setChannel} />
      </Field>
      <Field label="Hạn">
        <Select label="Hạn" options={DEADLINES} value={deadline} onChange={setDeadline} />
      </Field>
    </Dialog>
  );
}
