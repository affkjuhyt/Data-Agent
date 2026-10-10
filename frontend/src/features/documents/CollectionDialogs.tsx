import { useEffect, useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { listUsers } from '../../lib/v1/api';
import {
  createCollection,
  listDocumentSources,
  listSchemas,
  suggestCollections,
  type CollectionSuggestion,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../admin-access/FormDialog';

/*
 * The two ways to start a collection on "v3.1 · Phi cấu trúc · Bộ sưu tập" (UC-05-06):
 * "Hộp thoại · createCollection" (2095:8589) and "Hộp thoại · suggestCollection" (2095:8645), where
 * the agent scans the connected file sources and proposes how to group them.
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
    <FormDialog
      open={open}
      title="Tạo bộ sưu tập"
      description="Gom các tệp cùng loại để agent đọc, trích xuất và tìm kiếm."
      submitLabel="Tạo"
      loading={busy}
      error={error}
      onCancel={onClose}
      onSubmit={() => void submit()}
    >
      <Field label="Tên">
        <Input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Loại tệp">
        <Input
          value={types}
          placeholder="PDF, Word, ảnh"
          onChange={(e) => setTypes(e.target.value)}
        />
      </Field>
      <Field label="Nguồn nạp">
        <Select
          label="Nguồn nạp"
          placeholder="Chọn nguồn"
          options={(sources.data?.items ?? []).map((s) => ({ value: s.id, label: s.label }))}
          value={source}
          onChange={setSource}
        />
      </Field>
      <Field label="Lược đồ trích xuất">
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
      <Field label="Chủ sở hữu">
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
    </FormDialog>
  );
}

export function SuggestCollectionDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (created: number) => void;
}) {
  const [suggestions, setSuggestions] = useState<CollectionSuggestion[] | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setSuggestions(null);
    setError(undefined);
    suggestCollections()
      .then((r) => {
        if (!alive) return;
        setSuggestions(r.suggestions);
        setNote(r.note);
        setPicked(new Set(r.suggestions.map((s) => s.id)));
      })
      .catch((e: Error) => alive && setError(`Agent chưa đề xuất được. ${e.message}`));
    return () => {
      alive = false;
    };
  }, [open]);

  const toggle = (id: string, on: boolean) =>
    setPicked((current) => {
      const next = new Set(current);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const submit = async () => {
    const chosen = (suggestions ?? []).filter((s) => picked.has(s.id));
    if (chosen.length === 0) return setError('Chọn ít nhất một đề xuất.');
    setBusy(true);
    setError(undefined);
    try {
      // The agent only proposes the grouping: the owner defaults to the caller and the schema
      // is created after the first samples.
      for (const s of chosen)
        await createCollection({
          name: s.name,
          file_types: '',
          source_id: s.source_id,
          schema_id: null,
        });
      onCreated(chosen.length);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <FormDialog
      open={open}
      title="Agent đề xuất bộ sưu tập"
      description="Agent quét các nguồn tệp đã kết nối và gợi ý cách tổ chức."
      submitLabel="Tạo bộ sưu tập đã chọn"
      loading={busy}
      error={error}
      onCancel={onClose}
      onSubmit={() => void submit()}
    >
      {suggestions === null && !error ? (
        <SkeletonRows count={3} label="Agent đang quét các nguồn tệp…" />
      ) : (
        (suggestions ?? []).map((s, i) => (
          <div key={s.id} className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Đề xuất {i + 1}</span>
            <div className="flex h-9 items-center rounded-md border border-border-strong px-da-lg">
              <Checkbox checked={picked.has(s.id)} onChange={(on) => toggle(s.id, on)}>
                {s.label}
              </Checkbox>
            </div>
          </div>
        ))
      )}
      {note && (
        <p className="flex items-start gap-da-md rounded-md border border-border px-da-lg py-da-md text-foreground da-body-small">
          <Icon name="agent" className="mt-px shrink-0 text-primary" />
          {note}
        </p>
      )}
    </FormDialog>
  );
}
