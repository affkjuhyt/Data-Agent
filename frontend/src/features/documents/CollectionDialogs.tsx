import { useEffect, useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { listUsers } from '../../lib/v1/api';
import { createCollection, listDocumentSources, listSchemas } from '../../lib/v1/documents';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · createCollection" (2095:8589) on "v3.1 · Phi cấu trúc · Bộ sưu tập" (UC-05-06): a
 * person makes a collection for a purpose. Figma's "suggestCollection" (2095:8645), where the
 * agent proposed collections, is not in the design: UC-05-06 has the person create collections and
 * the agent only propose the extraction schema.
 */
const NEW_SCHEMA = '__new';

export function CreateCollectionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (collection: { id: string; name: string }) => void;
}) {
  const sources = useV1(open ? listDocumentSources : null, [open]);
  const schemas = useV1(open ? listSchemas : null, [open]);
  const users = useV1(open ? listUsers : null, [open]);
  const [name, setName] = useState('');
  const [types, setTypes] = useState('');
  const [source, setSource] = useState<string | null>(null);
  const [schema, setSchema] = useState<string>(NEW_SCHEMA);
  const [owner, setOwner] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setName('');
    setTypes('');
    setSource(null);
    setSchema(NEW_SCHEMA);
    setOwner(null);
    setError(undefined);
  }, [open]);

  const submit = async () => {
    if (!name.trim()) return setError('Đặt tên bộ sưu tập.');
    if (!source || !owner) return setError('Chọn nguồn nạp và chủ sở hữu.');
    setBusy(true);
    setError(undefined);
    try {
      onCreated(
        await createCollection({
          name: name.trim(),
          file_types: types.trim(),
          source_id: source,
          schema_id: schema === NEW_SCHEMA ? null : schema,
          owner_id: owner,
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
      <Field labelTone="muted" label="Nguồn nạp">
        <Select
          label="Nguồn nạp"
          placeholder="Chọn nguồn"
          options={(sources.data?.items ?? []).map((s) => ({ value: s.id, label: s.label }))}
          value={source}
          onChange={setSource}
        />
      </Field>
      <Field labelTone="muted" label="Lược đồ trích xuất">
        <Select
          label="Lược đồ trích xuất"
          options={[
            { value: NEW_SCHEMA, label: 'Tạo mới sau khi có 20 tệp mẫu' },
            ...(schemas.data?.items ?? [])
              .filter((s) => s.status === 'active')
              .map((s) => ({
                value: s.id,
                label: `Dùng lại ${s.collection.name} · v${s.version}`,
              })),
          ]}
          value={schema}
          onChange={setSchema}
        />
      </Field>
      <Field labelTone="muted" label="Chủ sở hữu">
        <Select
          label="Chủ sở hữu"
          placeholder="Chọn người"
          options={(users.data?.items ?? [])
            .filter((u) => u.kind === 'person')
            .map((u) => ({ value: u.id, label: u.display_name }))}
          value={owner}
          onChange={setOwner}
        />
      </Field>
    </Dialog>
  );
}
