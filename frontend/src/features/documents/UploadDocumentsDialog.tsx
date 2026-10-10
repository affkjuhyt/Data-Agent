import { useEffect, useRef, useState, type DragEvent } from 'react';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Select } from '../../design-system/Select';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  listSchemas,
  precheckUpload,
  uploadDocuments,
  type PiiPolicy,
} from '../../lib/v1/documents';
import { listCollections } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../admin-access/FormDialog';

/*
 * "Hộp thoại · upload" (2095:8532, UC-05-01): files into a collection, read with its extraction
 * schema, personal data masked before chunking. Once files and a collection are chosen the
 * agent checks for duplicates it will skip; the note shows before anything is sent.
 */
const NO_SCHEMA = '__none';

const PII: { value: PiiPolicy; label: string }[] = [
  { value: 'mask', label: 'Tự che CCCD, số tài khoản' },
  { value: 'mask_and_review', label: 'Che và đưa vào hàng chờ chất lượng' },
];

const sizeText = (bytes: number) =>
  bytes >= 1024 * 1024
    ? `${formatNumber(Math.round(bytes / 1024 / 1024))} MB`
    : `${formatNumber(Math.max(1, Math.round(bytes / 1024)))} KB`;

export function UploadDocumentsDialog({
  open,
  collectionId,
  onClose,
  onUploaded,
}: {
  open: boolean;
  /** preselected collection, e.g. when opened from a collection. */
  collectionId?: string;
  onClose: () => void;
  onUploaded: (result: { accepted: number; skipped: number }) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const collections = useV1(open ? listCollections : null, [open]);
  const schemas = useV1(open ? listSchemas : null, [open]);
  const [files, setFiles] = useState<File[]>([]);
  const [collection, setCollection] = useState<string | null>(collectionId ?? null);
  const [schema, setSchema] = useState<string>(NO_SCHEMA);
  const [pii, setPii] = useState<PiiPolicy>('mask');
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setFiles([]);
    setCollection(collectionId ?? null);
    setPii('mask');
    setNote(null);
    setError(undefined);
  }, [open, collectionId]);

  const collectionSchemas = (schemas.data?.items ?? []).filter(
    (s) => s.collection.id === collection && s.status !== 'superseded',
  );

  // The collection's active schema (or its draft) is what new files are read with.
  useEffect(() => {
    const preferred =
      collectionSchemas.find((s) => s.status === 'active') ?? collectionSchemas[0] ?? null;
    setSchema(preferred?.id ?? NO_SCHEMA);
    // collectionSchemas is derived from these two.
  }, [collection, schemas.data]);

  useEffect(() => {
    if (!open || !collection || files.length === 0) {
      setNote(null);
      return;
    }
    let alive = true;
    precheckUpload({
      collection_id: collection,
      files: files.map((f) => ({ file_name: f.name, size_bytes: f.size })),
    })
      .then((r) => alive && setNote(r.note))
      .catch(() => alive && setNote(null));
    return () => {
      alive = false;
    };
  }, [open, collection, files]);

  const pick = (list: FileList | null) => {
    if (list && list.length) setFiles(Array.from(list));
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    pick(e.dataTransfer.files);
  };

  const submit = async () => {
    if (files.length === 0) return setError('Chọn ít nhất một tệp.');
    if (!collection) return setError('Chọn bộ sưu tập.');
    setBusy(true);
    setError(undefined);
    try {
      const result = await uploadDocuments({
        collection_id: collection,
        schema_id: schema === NO_SCHEMA ? null : schema,
        pii,
        files: files.map((f) => ({ file_name: f.name, size_bytes: f.size })),
      });
      onUploaded(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const total = files.reduce((n, f) => n + f.size, 0);

  return (
    <FormDialog
      open={open}
      title="Nạp tệp vào bộ sưu tập"
      description="Tệp đi qua pipeline: nhận dạng, trích trường, lập chỉ mục, che dữ liệu cá nhân."
      submitLabel="Nạp tệp"
      loading={busy}
      error={error}
      onCancel={onClose}
      onSubmit={() => void submit()}
    >
      <div className="flex flex-col gap-da-xs">
        <span className="text-foreground da-label">Tệp</span>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="sr-only"
          aria-label="Chọn tệp để nạp"
          onChange={(e) => {
            pick(e.target.files);
            e.target.value = '';
          }}
        />
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={onDrop}
          className={cn(
            'flex h-9 items-center gap-da-sm rounded-md border border-dashed border-border-strong px-da-lg text-left da-body-small',
            files.length ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          <Icon name="upload" />
          {files.length
            ? `Kéo thả hoặc chọn · ${formatNumber(files.length)} tệp · ${sizeText(total)}`
            : 'Kéo thả hoặc chọn tệp'}
        </button>
      </div>
      <Field label="Bộ sưu tập">
        <Select
          label="Bộ sưu tập"
          placeholder="Chọn bộ sưu tập"
          options={(collections.data?.items ?? [])
            .filter((c) => !c.archived)
            .map((c) => ({ value: c.id, label: c.name }))}
          value={collection}
          onChange={setCollection}
        />
      </Field>
      <Field label="Lược đồ trích xuất">
        <Select
          label="Lược đồ trích xuất"
          options={[
            ...collectionSchemas.map((s) => ({
              value: s.id,
              label: `${s.collection.name} · v${s.version}`,
            })),
            { value: NO_SCHEMA, label: 'Chưa dùng lược đồ · chỉ chia đoạn' },
          ]}
          value={schema}
          onChange={setSchema}
        />
      </Field>
      <Field label="Dữ liệu cá nhân">
        <Select<PiiPolicy> label="Dữ liệu cá nhân" options={PII} value={pii} onChange={setPii} />
      </Field>
      {note && (
        <p className="flex items-start gap-da-md rounded-md border border-border px-da-lg py-da-md text-foreground da-body-small">
          <Icon name="agent" className="mt-px shrink-0 text-primary" />
          {note}
        </p>
      )}
    </FormDialog>
  );
}
