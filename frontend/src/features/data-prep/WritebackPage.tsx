import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import {
  approveWriteback,
  getWriteback,
  listOpenWritebacks,
  rejectWriteback,
  requestWritebackChanges,
  type Writeback,
  type WritebackStatus,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P06 · Ghi ngược có duyệt" (2290:17788, UC-06-04): a proposal to write changed values back to
 * a source system. The change sample, the dry run on the staging table, and the two approvers in
 * order; data only ever lands in the staging table, which the target system's team imports on
 * their own schedule. Rejecting or asking for changes needs a reason, at either step.
 * Opened from the rail without an id, it shows the first write-back waiting for approval.
 */
const STATUS: Record<
  WritebackStatus,
  { tone: 'warning' | 'error' | 'success' | 'info'; label: string }
> = {
  pending_approval: { tone: 'warning', label: 'Chờ duyệt' },
  changes_requested: { tone: 'info', label: 'Chờ sửa' },
  rejected: { tone: 'error', label: 'Đã từ chối' },
  approved: { tone: 'success', label: 'Đã duyệt' },
  written: { tone: 'success', label: 'Đã ghi vào bảng chờ' },
};

const APPROVER: Record<
  Writeback['approvers'][number]['state'],
  { tone: 'warning' | 'idle' | 'success' | 'error'; label: string }
> = {
  pending: { tone: 'warning', label: 'Đang chờ' },
  waiting_previous: { tone: 'idle', label: 'Sau bước 1' },
  approved: { tone: 'success', label: 'Đã duyệt' },
  rejected: { tone: 'error', label: 'Đã từ chối' },
};

async function loadWriteback(id: string | undefined): Promise<Writeback | null> {
  if (id) return getWriteback(id);
  const open = await listOpenWritebacks();
  return open.items[0] ?? null;
}

export default function WritebackPage() {
  const { routeId } = useAppState();
  const loaded = useV1(() => loadWriteback(routeId), [routeId]);
  const toast = useToast();
  const [wb, setWb] = useState<Writeback | null>();
  const [asking, setAsking] = useState<'reject' | 'changes' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => setWb(loaded.data), [loaded.data]);

  const step = wb ? wb.approvers.findIndex((a) => a.state === 'pending') + 1 : 0;

  const approve = async () => {
    if (!wb) return;
    setBusy(true);
    setError(undefined);
    try {
      const next = await approveWriteback(wb.id);
      setWb(next);
      toast({
        tone: 'success',
        message:
          next.status === 'approved'
            ? `Đã duyệt đủ · ${wb.code} sẽ ghi vào ${wb.target_table}`
            : `Đã duyệt bước ${step}/${wb.approvers.length} · chờ ${next.approvers[step]?.name ?? 'người duyệt tiếp'}`,
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (loaded.data === null)
    return (
      <EmptyState
        className="m-da-3xl"
        icon="check"
        title="Không có lệnh ghi ngược chờ duyệt"
        description="Khi agent hoặc đồng nghiệp đề xuất ghi ngược về một hệ thống nguồn, lệnh sẽ hiện ở đây để duyệt."
      />
    );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Ghi ngược', wb?.code ?? '…']}
        title={
          wb ? (
            <span className="flex items-center gap-[10px]">
              {wb.title}
              <StatusDot tone={STATUS[wb.status].tone} className="da-body-small font-normal">
                {STATUS[wb.status].label}
              </StatusDot>
            </span>
          ) : (
            'Ghi ngược'
          )
        }
        description={wb?.summary_text}
        actions={
          wb?.status === 'pending_approval' && wb.can_decide ? (
            <>
              <Button kind="ghost" onClick={() => setAsking('reject')}>
                Từ chối
              </Button>
              <Button onClick={() => setAsking('changes')}>Yêu cầu sửa</Button>
              <Button kind="primary" loading={busy} onClick={approve}>
                Duyệt bước {step}/{wb.approvers.length}
              </Button>
            </>
          ) : undefined
        }
      />
      <AsyncRegion state={loaded} what="lệnh ghi ngược" skeletonRows={6}>
        {() =>
          wb ? (
            <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
              <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
                {error && (
                  <Notice tone="error" className="mt-da-xl">
                    {error}
                  </Notice>
                )}
                <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                  <div className="flex items-center gap-da-md">
                    <h2 className="flex-1 text-foreground da-heading-3">Thay đổi sẽ ghi</h2>
                    <p className="text-muted-foreground da-caption">
                      {formatNumber(wb.row_count)} dòng · hiển thị {wb.changes.length}
                    </p>
                  </div>
                  <ChangeTable changes={wb.changes} />
                </section>
                <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                  <h2 className="text-foreground da-heading-3">Chạy thử trên bảng chờ</h2>
                  <ul className="flex flex-col gap-[10px]">
                    {wb.checks.map((c) => (
                      <li
                        key={c.text}
                        className="flex items-center gap-da-md text-foreground da-body-small"
                      >
                        <Icon
                          name={c.ok ? 'check' : 'alert'}
                          size={14}
                          className={c.ok ? 'text-success' : 'text-warning'}
                          label={c.ok ? 'Đạt' : 'Cần lưu ý'}
                        />
                        {c.text}
                      </li>
                    ))}
                  </ul>
                </section>
              </div>
              <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full">
                <h2 className="text-foreground da-heading-3">Người duyệt</h2>
                <ol className="flex flex-col gap-[10px]">
                  {wb.approvers.map((a) => (
                    <li key={a.name} className="flex flex-col gap-[2px] pb-da-md">
                      <p className="text-foreground da-body-strong">{a.name}</p>
                      <p className="text-muted-foreground da-caption">{a.role_label}</p>
                      <StatusDot tone={APPROVER[a.state].tone}>{APPROVER[a.state].label}</StatusDot>
                    </li>
                  ))}
                </ol>
                <h2 className="text-foreground da-heading-3">Sau khi duyệt đủ</h2>
                <p className="text-foreground da-body-small">{wb.after_approval_text}</p>
                <h2 className="text-foreground da-heading-3">Hoàn tác</h2>
                <p className="text-foreground da-body-small">{wb.rollback_text}</p>
                <p className="text-muted-foreground da-caption">
                  Ghi ngược luôn cần 2 người duyệt.
                </p>
              </aside>
            </div>
          ) : null
        }
      </AsyncRegion>
      <ReasonDialog
        kind={asking}
        onClose={() => setAsking(null)}
        onSubmit={async (text) => {
          if (!wb || !asking) return;
          const next =
            asking === 'reject'
              ? await rejectWriteback(wb.id, text)
              : await requestWritebackChanges(wb.id, text);
          setWb(next);
          setAsking(null);
          toast({
            tone: 'info',
            message: asking === 'reject' ? `Đã từ chối ${wb.code}` : `Đã yêu cầu sửa ${wb.code}`,
          });
        }}
      />
    </div>
  );
}

function ReasonDialog({
  kind,
  onClose,
  onSubmit,
}: {
  kind: 'reject' | 'changes' | null;
  onClose: () => void;
  onSubmit: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setText('');
    setError(undefined);
  }, [kind]);

  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await onSubmit(text.trim());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reject = kind === 'reject';
  return (
    <Dialog
      open={kind !== null}
      title={reject ? 'Từ chối lệnh ghi ngược' : 'Yêu cầu sửa lệnh ghi ngược'}
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button
            kind={reject ? 'danger' : 'primary'}
            loading={busy}
            disabled={!text.trim()}
            onClick={submit}
          >
            {reject ? 'Từ chối' : 'Gửi yêu cầu sửa'}
          </Button>
        </>
      }
    >
      {error && <Notice tone="error">{error}</Notice>}
      <Field
        label={reject ? 'Lý do từ chối' : 'Cần sửa gì'}
        hint="Người đề xuất nhận được nội dung này. Không có gì được ghi vào bảng chờ."
      >
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={4}
          className="w-full"
        />
      </Field>
    </Dialog>
  );
}

function ChangeTable({ changes }: { changes: Writeback['changes'] }) {
  const view = useTableView(changes, {
    search: (c) => `${c.subject} ${c.before ?? ''} ${c.after} ${c.reason}`,
    filters: [{ id: 'after', label: 'Hạng mới', value: (c) => c.after }],
    sorts: [
      { id: 'subject', value: (c) => c.subject },
      { id: 'before', value: (c) => c.before },
      { id: 'after', value: (c) => c.after },
      { id: 'reason', value: (c) => c.reason },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm thay đổi…" searchLabel="Tìm thay đổi" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="subject" className="w-[174px] px-da-lg py-da-md font-normal">
              Khách hàng
            </SortableTh>
            <SortableTh view={view} column="before" className="w-[144px] px-da-lg py-da-md font-normal">
              Hạng hiện tại
            </SortableTh>
            <SortableTh view={view} column="after" className="w-[144px] px-da-lg py-da-md font-normal">
              Hạng mới
            </SortableTh>
            <SortableTh view={view} column="reason" className="px-da-lg py-da-md font-normal">
              Lý do
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((c) => (
            <tr key={c.subject} className="border-b border-border">
              <td className="px-da-lg py-[9px] text-foreground da-mono">{c.subject}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">{c.before ?? '—'}</td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">{c.after}</td>
              <td className="px-da-lg py-[9px] text-foreground da-body-small">{c.reason}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
