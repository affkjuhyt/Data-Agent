import { useEffect, useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { createCollection } from '../../lib/v1/documents';
import { useV1Mutation } from '../../lib/v1/useV1';
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
  // A missing name or purpose is caught here; the server's answer lands in the mutation.
  const [invalid, setInvalid] = useState<string>();
  const create = useV1Mutation(
    () =>
      createCollection({
        name: name.trim(),
        file_types: types.trim(),
        purpose: purpose.trim(),
        pii_policy: piiPolicy,
        ...(retentionDays.trim() ? { retention_days: Number(retentionDays) } : {}),
      }),
    { onSuccess: (collection) => onCreated(collection) },
  );

  useEffect(() => {
    if (!open) return;
    setName('');
    setTypes('');
    setPurpose('');
    setPiiPolicy('mask');
    setRetentionDays('');
    setInvalid(undefined);
    create.reset();
  }, [open]);

  const submit = () => {
    if (!name.trim()) return setInvalid('Đặt tên bộ sưu tập.');
    if (!purpose.trim()) return setInvalid('Đặt mục đích sử dụng.');
    setInvalid(undefined);
    create.run();
  };

  return (
    <Dialog
      open={open}
      title="Tạo bộ sưu tập"
      description="Gom các tệp cùng loại để agent đọc, trích xuất và tìm kiếm."
      submitLabel="Tạo"
      submitting={create.pending}
      error={invalid ?? create.error?.message}
      onClose={onClose}
      onSubmit={submit}
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
