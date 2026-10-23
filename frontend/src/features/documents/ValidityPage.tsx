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
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
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
import { Dialog } from '../../design-system/Dialog';
import { HEAD_ROW, ROW, SidePanel } from './parts';

/*
 * "P05 · Hiệu lực văn bản" (2300:19248, UC-05-05): the validity timeline of one document, what
 * a supersession changes for answers, and the agent's finding. Only the person in charge of the
 * document confirms the relation. From the rail without an id it opens the first document whose
 * relation waits for confirmation.
 */
export default function ValidityPage() {
  const { routeId } = useAppState();
  const pending = useV1(['listLibrary', { validity_review: true }], routeId ? null : () => listLibrary({ validity_review: true }));
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
  const validity = useV1(['getValidity', id], () => getValidity(id));
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
            <span className="flex flex-wrap items-center gap-[10px]">
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
          description={v.owner_text ?? undefined}
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
        <div className="flex flex-col gap-da-xl pb-da-xl pl-da-3xl pr-da-2xl pt-[20px]">
          <h2 className="text-foreground da-heading-3">Dòng thời gian</h2>
          <ol className="flex flex-col">
            {v.timeline.map((e) => (
              <li
                key={`${e.date_text}-${e.text}`}
                className="flex items-start gap-[14px] border-b border-border py-[10px]"
              >
                <span className="w-[100px] shrink-0 text-muted-foreground da-mono">
                  {e.date_text}
                </span>
                <span
                  aria-hidden
                  className={cn(
                    'mt-[4px] size-[7px] shrink-0 rounded-full',
                    e.change ? 'bg-warning' : 'bg-border-strong',
                  )}
                />
                <span className="min-w-0 flex-1 text-foreground da-body-small">{e.text}</span>
              </li>
            ))}
          </ol>
          {/* What the change does to answers is the agent's (PROPOSAL): hidden without it. */}
          {v.impact.length > 0 && (
            <>
              <h2 className="text-foreground da-heading-3">Ảnh hưởng tới câu trả lời</h2>
              <ImpactTable rows={v.impact} />
            </>
          )}
        </div>
      </div>
      {v.finding && (
        <SidePanel
          label="Agent phát hiện"
          width="w-[340px]"
          className="gap-[10px] bg-background px-da-2xl pt-[20px]"
        >
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
  const docs = useV1(['listLibrary', {}], () => listLibrary({}));
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
    <Dialog
      open
      title="Sửa quan hệ hiệu lực"
      description={`${v.title} bị thay thế bởi văn bản nào, từ ngày nào.`}
      submitLabel="Lưu"
      submitting={busy}
      error={error}
      onClose={onClose}
      onSubmit={() => void save()}
    >
      <Field labelTone="muted" label="Văn bản thay thế">
        <Select label="Văn bản thay thế" options={options} value={by} onChange={setBy} />
      </Field>
      <Field labelTone="muted" label="Hết hiệu lực từ">
        <Input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </Field>
    </Dialog>
  );
}

function ImpactTable({ rows }: { rows: DocumentValidity['impact'] }) {
  const view = useTableView(rows, {
    search: (row) => `${row.subject} ${row.effect}`,
    filters: [{ id: 'subject', label: 'Đối tượng', value: (row) => row.subject }],
    sorts: [
      { id: 'subject', value: (row) => row.subject },
      { id: 'effect', value: (row) => row.effect },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm ảnh hưởng…" searchLabel="Tìm ảnh hưởng" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className={cn(HEAD_ROW, 'bg-transparent')}>
            <SortableTh view={view} column="subject" className="px-da-lg py-da-md font-normal">
              Đối tượng
            </SortableTh>
            <SortableTh view={view} column="effect" className="w-[384px] px-da-lg py-da-md font-normal">
              Ảnh hưởng
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.subject} className={cn(ROW, 'last:border-b')}>
              <td className="px-da-lg py-da-md text-foreground da-body-strong">{row.subject}</td>
              <td className="px-da-lg py-da-md text-foreground da-body-small">{row.effect}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
