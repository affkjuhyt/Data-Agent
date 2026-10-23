import { useEffect, useRef, useState, type DragEvent } from 'react';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listSchemas, uploadDocuments, type PiiPolicy } from '../../lib/v1/documents';
import { listCollections } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "Hộp thoại · upload" (2095:8532, UC-05-01): files into a collection, read with its extraction
 * schema, personal data masked before chunking. A file the collection already holds (same hash)
 * is answered with that document and counted as skipped after the upload (A3), so there is no
 * check beforehand.
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
  const collections = useV1(['listCollections', open], open ? listCollections : null);
  const schemas = useV1(['listSchemas', open], open ? listSchemas : null);
  const [files, setFiles] = useState<File[]>([]);
  const [collection, setCollection] = useState<string | null>(collectionId ?? null);
  const [schema, setSchema] = useState<string>(NO_SCHEMA);
  const [pii, setPii] = useState<PiiPolicy>('mask');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setFiles([]);
    setCollection(collectionId ?? null);
    setPii('mask');
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
        files,
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
    <Dialog
      open={open}
      title="Nạp tệp vào bộ sưu tập"
      description="Tệp đi qua pipeline: nhận dạng, trích trường, lập chỉ mục, che dữ liệu cá nhân."
      submitLabel="Nạp"
      submitting={busy}
      error={error}
      onClose={onClose}
      onSubmit={() => void submit()}
    >
      <div className="flex flex-col gap-da-xs">
        <span className="text-muted-foreground da-label">Tệp</span>
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
            'flex items-center rounded-md border border-dashed border-border-strong bg-subtle p-da-md text-left da-body-small',
            files.length ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          {files.length
            ? `Kéo thả hoặc chọn · ${formatNumber(files.length)} tệp · ${sizeText(total)}`
            : 'Kéo thả hoặc chọn tệp'}
        </button>
      </div>
      <Field labelTone="muted" label="Bộ sưu tập">
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
      <Field labelTone="muted" label="Lược đồ trích xuất">
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
      <Field labelTone="muted" label="Dữ liệu cá nhân">
        <Select<PiiPolicy> label="Dữ liệu cá nhân" options={PII} value={pii} onChange={setPii} />
      </Field>
    </Dialog>
  );
}
