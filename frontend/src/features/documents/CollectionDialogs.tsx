import { useEffect, useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { createCollection } from '../../lib/v1/documents';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · createCollection" (2095:8589) on "v3.1 · Phi cấu trúc · Bộ sưu tập" (UC-05-06): a
 * person makes a collection for a purpose. Sources and extraction schemas are separate follow-up
 * steps, so this dialog never presents fields that POST /v1/collections cannot persist.
 */
export function CreateCollectionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (collection: { id: string; name: string }) => void;
}) {
  const [name, setName] = useState('');
  const [types, setTypes] = useState('');
  const [purpose, setPurpose] = useState('');
  const [piiPolicy, setPiiPolicy] = useState<'mask' | 'keep_for_authorized' | 'reject'>('mask');
  const [retentionDays, setRetentionDays] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setName('');
    setTypes('');
    setPurpose('');
    setPiiPolicy('mask');
    setRetentionDays('');
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!name.trim()) return setError('Đặt tên bộ sưu tập.');
    if (!purpose.trim()) return setError('Đặt mục đích sử dụng.');
    setBusy(true);
    setError(undefined);
    try {
      onCreated(
        await createCollection({
          name: name.trim(),
          file_types: types.trim(),
          purpose: purpose.trim(),
          pii_policy: piiPolicy,
          ...(retentionDays.trim() ? { retention_days: Number(retentionDays) } : {}),
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Tạo bộ sưu tập"
      description="Gom các tệp cùng loại để agent đọc, trích xuất và tìm kiếm."
      submitLabel="Tạo"
      submitting={busy}
      error={error}
      onClose={onClose}
      onSubmit={() => void submit()}
    >
      <Field labelTone="muted" label="Tên">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field labelTone="muted" label="Loại tệp">
        <Input
          value={types}
          placeholder="PDF, Word, ảnh"
          onChange={(e) => setTypes(e.target.value)}
        />
      </Field>
      <Field labelTone="muted" label="Mục đích sử dụng">
        <Input
          value={purpose}
          placeholder="Ví dụ: thẩm định tín dụng"
          onChange={(e) => setPurpose(e.target.value)}
        />
      </Field>
      <Field labelTone="muted" label="Chính sách dữ liệu cá nhân">
        <Select
          label="Chính sách dữ liệu cá nhân"
          options={[
            { value: 'mask', label: 'Che mặc định' },
            { value: 'keep_for_authorized', label: 'Giữ cho người có quyền' },
            { value: 'reject', label: 'Từ chối dữ liệu cá nhân' },
          ]}
          value={piiPolicy}
          onChange={(value) => setPiiPolicy(value as 'mask' | 'keep_for_authorized' | 'reject')}
        />
      </Field>
      <Field labelTone="muted" label="Thời hạn lưu (ngày)">
        <Input
          type="number"
          min={1}
          max={36500}
          value={retentionDays}
          placeholder="Không giới hạn"
          onChange={(e) => setRetentionDays(e.target.value)}
        />
      </Field>
    </Dialog>
  );
}
