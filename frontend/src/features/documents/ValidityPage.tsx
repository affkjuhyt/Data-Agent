import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  getValidity,
  listLibrary,
  setSupersession,
  type DocumentValidity,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../admin-access/FormDialog';
import { HEAD_ROW, ROW, SidePanel } from './parts';

/*
 * "P05 · Hiệu lực văn bản" (2300:19248, UC-05-05): the validity timeline of one document, what
 * a supersession changes for answers, and the agent's finding. Only the person in charge of the
 * document confirms the relation. From the rail without an id it opens the first document whose
 * relation waits for confirmation.
 */
export default function ValidityPage() {
  const { routeId } = useAppState();
  const pending = useV1(routeId ? null : () => listLibrary({ validity_review: true }), [routeId]);
  if (!routeId)
    return (
      <AsyncRegion
        state={pending}
        what="văn bản chờ xác nhận hiệu lực"
        isEmpty={(p) => p.items.length === 0}
        empty={
          <EmptyState
            className="m-da-3xl"
            icon="calendar"
            title="Không có văn bản chờ xác nhận hiệu lực"
            description="Khi agent phát hiện văn bản bị thay thế hoặc sửa đổi, văn bản xuất hiện ở đây."
          />
        }
      >
        {(p) => <ValidityLoader id={p.items[0].id} />}
      </AsyncRegion>
    );
  return <ValidityLoader id={routeId} />;
}

function ValidityLoader({ id }: { id: string }) {
  const validity = useV1(() => getValidity(id), [id]);
  return (
    <AsyncRegion state={validity} what="hiệu lực văn bản" skeletonRows={6}>
      {(v) => <ValidityView key={v.document_id} v={v} onChanged={validity.reload} />}
    </AsyncRegion>
  );
}

function ValidityView({ v, onChanged }: { v: DocumentValidity; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);

  const confirm = async () => {
    if (!v.pending) return;
    setBusy(true);
    try {
      await setSupersession(v.document_id, {
        superseded_by: v.pending.superseded_by,
        valid_to: v.pending.valid_to,
      });
      toast({ tone: 'success', message: `Đã xác nhận ${v.title} bị thay thế.` });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: `Không xác nhận được. ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  const notOwner = 'Chỉ người phụ trách văn bản xác nhận được quan hệ hiệu lực.';

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <PageHeader
          bordered
          breadcrumb={['Dữ liệu', 'Tài liệu', 'Hiệu lực văn bản']}
          title={
            <span className="flex flex-wrap items-center gap-da-md">
              <button
                type="button"
                className="text-left hover:underline"
                onClick={() => navigate('data', 'DOCUMENT', v.document_id)}
              >
                {v.title}
              </button>
              <StatusDot tone={v.status.tone}>{v.status.text}</StatusDot>
            </span>
          }
          description={v.owner_text}
          actions={
            v.can_confirm ? (
              <>
                <Button kind="ghost" disabled={busy} onClick={() => setEditing(true)}>
                  Sửa quan hệ
                </Button>
                {v.pending && (
                  <Button kind="primary" loading={busy} onClick={() => void confirm()}>
                    Xác nhận bị thay thế
                  </Button>
                )}
              </>
            ) : (
              v.pending && (
                <Tooltip content={notOwner}>
                  <Button kind="primary" disabled>
                    Xác nhận bị thay thế
                  </Button>
                </Tooltip>
              )
            )
          }
        />
        <div className="flex flex-col gap-da-md px-da-3xl py-da-xl">
          <h2 className="text-foreground da-heading-3">Dòng thời gian</h2>
          <ol className="flex flex-col">
            {v.timeline.map((e) => (
              <li
                key={`${e.date_text}-${e.text}`}
                className="flex items-center gap-da-lg border-b border-border px-da-md py-da-md"
              >
                <span className="w-[100px] shrink-0 text-muted-foreground da-mono">
                  {e.date_text}
                </span>
                <span
                  aria-hidden
                  className={cn(
                    'size-[7px] shrink-0 rounded-full',
                    e.change ? 'bg-warning' : 'bg-border-strong',
                  )}
                />
                <span className="text-foreground da-body-small">{e.text}</span>
              </li>
            ))}
          </ol>
          <h2 className="pt-da-md text-foreground da-heading-3">Ảnh hưởng tới câu trả lời</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className={cn(HEAD_ROW, 'bg-transparent')}>
                <th className="px-da-md py-da-sm font-normal">Đối tượng</th>
                <th className="px-da-md py-da-sm font-normal">Ảnh hưởng</th>
              </tr>
            </thead>
            <tbody>
              {v.impact.map((row) => (
                <tr key={row.subject} className={cn(ROW, 'last:border-b')}>
                  <td className="px-da-md py-da-md text-foreground da-body-strong">
                    {row.subject}
                  </td>
                  <td className="px-da-md py-da-md text-foreground da-body-small">{row.effect}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {v.finding && (
        <SidePanel label="Agent phát hiện" width="w-[340px]">
          <h2 className="text-foreground da-heading-3">Agent phát hiện</h2>
          <p className="text-foreground da-body-small">{v.finding.text}</p>
          {!v.pending && <StatusDot tone="success">Đã xác nhận</StatusDot>}
          <p className="text-muted-foreground da-caption">{v.finding.footnote}</p>
        </SidePanel>
      )}
      {editing && (
        <RelationDialog
          v={v}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            toast({ tone: 'success', message: 'Đã lưu quan hệ hiệu lực.' });
            onChanged();
          }}
        />
      )}
    </div>
  );
}

/* "Sửa quan hệ": which document supersedes this one and from when (PATCH /v1/documents/{id}). */
function RelationDialog({
  v,
  onClose,
  onSaved,
}: {
  v: DocumentValidity;
  onClose: () => void;
  onSaved: () => void;
}) {
  const docs = useV1(() => listLibrary({}), []);
  const [by, setBy] = useState<string | null>(v.pending?.superseded_by ?? null);
  const [date, setDate] = useState(v.pending?.valid_to ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const options = [
    ...(v.pending
      ? [{ value: v.pending.superseded_by, label: v.pending.superseded_by_title }]
      : []),
    ...(docs.data?.items ?? [])
      .filter((d) => d.id !== v.document_id && d.id !== v.pending?.superseded_by)
      .map((d) => ({ value: d.id, label: d.title })),
  ];

  const save = async () => {
    if (!by || !date) return setError('Chọn văn bản thay thế và ngày hết hiệu lực.');
    setBusy(true);
    setError(undefined);
    try {
      await setSupersession(v.document_id, { superseded_by: by, valid_to: date });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <FormDialog
      open
      title="Sửa quan hệ hiệu lực"
      description={`${v.title} bị thay thế bởi văn bản nào, từ ngày nào.`}
      submitLabel="Lưu"
      loading={busy}
      error={error}
      onCancel={onClose}
      onSubmit={() => void save()}
    >
      <Field label="Văn bản thay thế">
        <Select label="Văn bản thay thế" options={options} value={by} onChange={setBy} />
      </Field>
      <Field label="Hết hiệu lực từ">
        <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </Field>
    </FormDialog>
  );
}
