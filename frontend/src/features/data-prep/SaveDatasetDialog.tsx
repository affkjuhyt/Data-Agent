import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { listDataSpaces } from '../../lib/v1/api';
import {
  createDerivedTable,
  type DatasetAccess,
  type DatasetRefresh,
  type DerivedTable,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · saveDataset" (2095:9207, UC-06-02): the query in the SQL editor becomes a
 * versioned derived table, reusable in sessions and pipelines. Name, Data Space, refresh and
 * access; the table inherits the Data Space's permissions unless narrowed to its owner.
 */
const REFRESH: { value: DatasetRefresh; label: string }[] = [
  { value: 'after_etl', label: 'Hằng ngày sau ETL' },
  { value: 'hourly', label: 'Mỗi giờ' },
  { value: 'manual', label: 'Thủ công' },
];

const ACCESS: { value: DatasetAccess; label: string }[] = [
  { value: 'inherit', label: 'Kế thừa Data Space' },
  { value: 'owner_only', label: 'Chỉ mình tôi' },
];

export function SaveDatasetDialog({
  open,
  sql,
  suggestedName,
  onClose,
  onSaved,
}: {
  open: boolean;
  sql: string;
  suggestedName: string;
  onClose: () => void;
  onSaved: (table: DerivedTable) => void;
}) {
  const spaces = useV1(['listDataSpaces', open], open ? listDataSpaces : null);
  const [name, setName] = useState(suggestedName);
  const [space, setSpace] = useState<string | null>(null);
  const [refresh, setRefresh] = useState<DatasetRefresh>('after_etl');
  const [access, setAccess] = useState<DatasetAccess>('inherit');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (open) {
      setName(suggestedName);
      setError(undefined);
    }
  }, [open, suggestedName]);

  const spaceOptions = (spaces.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }));
  const chosenSpace = space ?? spaceOptions[0]?.value ?? null;

  const save = async () => {
    if (!name.trim() || !chosenSpace) {
      setError('Đặt tên và chọn Data Space trước khi lưu.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const table = await createDerivedTable({
        name: name.trim(),
        data_space_id: chosenSpace,
        refresh,
        access,
        sql,
      });
      onSaved(table);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Lưu thành dataset"
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" loading={busy} onClick={save}>
            Lưu
          </Button>
        </>
      }
    >
      <p className="-mt-[10px] text-muted-foreground da-caption">
        Dataset là bảng dẫn xuất có phiên bản, dùng lại được trong phiên và pipeline.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <Field label="Tên">
        <Input value={name} onChange={(e) => setName(e.target.value)} className="w-full" />
      </Field>
      <Field label="Data Space">
        <Select
          label="Data Space"
          options={spaceOptions}
          value={chosenSpace}
          onChange={setSpace}
          placeholder={spaces.loading ? 'Đang tải…' : 'Chọn Data Space'}
        />
      </Field>
      <Field label="Làm mới">
        <Select label="Làm mới" options={REFRESH} value={refresh} onChange={(v) => setRefresh(v)} />
      </Field>
      <Field label="Quyền">
        <Select label="Quyền" options={ACCESS} value={access} onChange={(v) => setAccess(v)} />
      </Field>
    </Dialog>
  );
}
