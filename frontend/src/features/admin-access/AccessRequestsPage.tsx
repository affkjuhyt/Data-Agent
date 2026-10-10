import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { formatTime } from '../../lib/formatVN';
import {
  decideAccessRequest,
  getAccessRequest,
  listAccessRequests,
  type AccessRequest,
  type DecideAccess,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { dayMonth, daysUntil } from './format';

/*
 * "P11 · Yêu cầu quyền · Người duyệt" (2305:19774, UC-11-07): one pending request with what is
 * asked, why, for how long and the current scope; the agent's suggestion; "Nếu cấp" before and
 * after. On the right, the approver's queue. Without an id the first request of the queue opens.
 * Từ chối needs a reason; Cấp hẹp hơn needs the narrower scope (prefilled with the suggestion).
 */
const STATUS_LABEL: Record<AccessRequest['status'], string> = {
  pending: 'Chờ duyệt',
  approved: 'Đã cấp',
  narrowed: 'Đã cấp hẹp hơn',
  rejected: 'Đã từ chối',
  expired: 'Hết hạn',
  cancelled: 'Đã huỷ',
};

type Dialog = 'reject' | 'narrow' | null;

export default function AccessRequestsPage() {
  const { routeId, navigate } = useAppState();
  const queue = useV1(() => listAccessRequests({ status: 'pending' }), []);
  const queueItems = queue.data?.items ?? [];
  const currentId = routeId ?? queueItems[0]?.id;
  const request = useV1(currentId ? () => getAccessRequest(currentId) : null, [currentId]);

  if (!routeId && queue.data && queueItems.length === 0) {
    return (
      <div className="flex flex-col">
        <PageHeader bordered breadcrumb={['Quản trị', 'Yêu cầu quyền']} title="Yêu cầu quyền" />
        <div className="px-da-3xl pt-da-xl">
          <EmptyState
            icon="lock"
            title="Không có yêu cầu quyền nào đang chờ"
            description="Khi ai đó xin thêm quyền vào Data Space bạn phụ trách, yêu cầu sẽ hiện ở đây."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-full flex-col">
      {!currentId ? (
        <div className="px-da-3xl pt-da-xl">
          <AsyncRegion state={queue} what="yêu cầu quyền">
            {() => null}
          </AsyncRegion>
        </div>
      ) : (
        <AsyncRegion state={request} what="yêu cầu quyền" skeletonRows={5}>
          {(r) => (
            <RequestDetail
              request={r}
              queue={queueItems}
              onDecided={(next) => {
                queue.reload();
                navigate('admin', 'ACCESS_REQUESTS', next);
                if (!next || next === r.id) request.reload();
              }}
              onOpen={(id) => navigate('admin', 'ACCESS_REQUESTS', id)}
            />
          )}
        </AsyncRegion>
      )}
    </div>
  );
}

function RequestDetail({
  request: r,
  queue,
  onDecided,
  onOpen,
}: {
  request: AccessRequest;
  queue: AccessRequest[];
  onDecided: (nextId: string | undefined) => void;
  onOpen: (id: string) => void;
}) {
  const toast = useToast();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState('');
  const [scope, setScope] = useState('');
  const [missing, setMissing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const pending = r.status === 'pending';
  const asked = Math.max(1, daysUntil(r.requested_until, new Date(r.created_at)));

  const decide = async (body: DecideAccess) => {
    setSending(true);
    setError(undefined);
    try {
      await decideAccessRequest(r.id, body);
      const word = { approve: 'Đã cấp quyền', narrow: 'Đã cấp hẹp hơn', reject: 'Đã từ chối' }[body.decision];
      toast({ tone: 'success', message: `${word} · ${r.code}. Người xin đã được báo.` });
      setDialog(null);
      onDecided(queue.find((q) => q.id !== r.id)?.id);
    } catch (e) {
      setError((e as Error).message);
      setDialog(null);
    } finally {
      setSending(false);
    }
  };

  const openDialog = (kind: Dialog) => {
    setNote('');
    setScope(r.suggested_scope_text ?? '');
    setMissing(false);
    setDialog(kind);
  };

  return (
    <>
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Yêu cầu quyền', r.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {r.title}
            <StatusDot tone={pending ? 'warning' : 'neutral'}>{STATUS_LABEL[r.status]}</StatusDot>
          </span>
        }
        description={`Gửi ${dayMonth(r.created_at)} ${formatTime(r.created_at)} ${r.origin_text} · người duyệt: ${r.approver_name} (${r.approver_role})`}
        actions={
          pending && (
            <>
              <Button kind="ghost" onClick={() => openDialog('reject')}>
                Từ chối
              </Button>
              <Button onClick={() => openDialog('narrow')}>Cấp hẹp hơn</Button>
              <Button kind="primary" loading={sending && !dialog} onClick={() => decide({ decision: 'approve' })}>
                Cấp quyền
              </Button>
            </>
          )
        }
      />
      {error && (
        <div className="px-da-3xl pt-da-xl">
          <Notice tone="error">Không gửi được quyết định: {error}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl pb-da-xl">
          <h2 className="text-foreground da-heading-3">Yêu cầu</h2>
          <dl className="flex flex-col gap-[14px] da-body-small">
            {[
              ['Xin', r.scope_text],
              ['Lý do', `“${r.reason}”`],
              ['Thời hạn xin', `${asked} ngày · tới ${dayMonth(r.requested_until)}`],
              ['Phạm vi hiện tại', r.current_scope_text],
            ].map(([label, value]) => (
              <div key={label} className="flex gap-da-xl">
                <dt className="w-[140px] shrink-0 text-muted-foreground">{label}</dt>
                <dd className="min-w-0 flex-1 text-foreground">{value}</dd>
              </div>
            ))}
          </dl>
          {r.suggestion && (
            <>
              <h2 className="text-foreground da-heading-3">Agent gợi ý</h2>
              <p className="rounded-md bg-background px-[14px] py-da-lg text-foreground da-body-small">
                {r.suggestion}
              </p>
            </>
          )}
          <h2 className="text-foreground da-heading-3">Nếu cấp</h2>
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-lg py-da-md font-normal">Thay đổi</th>
                <th className="w-[160px] px-da-lg py-da-md font-normal">Trước</th>
                <th className="w-[220px] px-da-lg py-da-md font-normal">Sau</th>
              </tr>
            </thead>
            <tbody>
              {r.impact.map((row) => (
                <tr key={row.label} className="border-b border-border">
                  <td className="px-da-lg py-da-md text-foreground da-body-small">{row.label}</td>
                  <td className="px-da-lg py-da-md text-muted-foreground da-mono">{row.before}</td>
                  <td className="px-da-lg py-da-md text-foreground da-mono">{row.after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Hàng chờ của bạn</h2>
          <ul className="flex flex-col">
            {queue.map((q) => (
              <QueueRow key={q.id} item={q} current={q.id === r.id} onOpen={() => onOpen(q.id)} />
            ))}
          </ul>
          <p className="text-muted-foreground da-caption">
            UC-11-07 · quyền cấp tạm có hạn và tự thu hồi; người xin nhận thông báo kết quả
          </p>
        </aside>
      </div>
      <ConfirmDialog
        open={dialog !== null}
        title={dialog === 'reject' ? `Từ chối ${r.code}?` : `Cấp hẹp hơn cho ${r.code}?`}
        confirmLabel={dialog === 'reject' ? 'Từ chối' : 'Cấp hẹp hơn'}
        tone={dialog === 'reject' ? 'danger' : 'primary'}
        loading={sending}
        error={
          missing
            ? dialog === 'reject'
              ? 'Ghi lý do để người xin biết vì sao.'
              : 'Ghi phạm vi được cấp.'
            : undefined
        }
        onCancel={() => setDialog(null)}
        onConfirm={() => {
          const value = (dialog === 'reject' ? note : scope).trim();
          if (!value) {
            setMissing(true);
            return;
          }
          void decide(
            dialog === 'reject'
              ? { decision: 'reject', note: value }
              : { decision: 'narrow', granted_scope: value, note: note.trim() || undefined },
          );
        }}
      >
        <div className="flex flex-col gap-da-sm">
          <span>
            {dialog === 'reject'
              ? 'Người xin nhận lý do của bạn và có thể gửi lại yêu cầu hẹp hơn.'
              : `Quyền được cấp tới ${dayMonth(r.requested_until)} rồi tự thu hồi.`}
          </span>
          <textarea
            value={dialog === 'reject' ? note : scope}
            onChange={(e) => (dialog === 'reject' ? setNote(e.target.value) : setScope(e.target.value))}
            aria-label={dialog === 'reject' ? 'Lý do từ chối (bắt buộc)' : 'Phạm vi được cấp'}
            placeholder={dialog === 'reject' ? 'Lý do từ chối (bắt buộc)' : 'Phạm vi được cấp'}
            rows={3}
            className="w-full rounded-md border border-border-strong bg-card px-da-lg py-da-md text-foreground outline-none da-body-small focus-visible:border-ring"
          />
        </div>
      </ConfirmDialog>
    </>
  );
}

function QueueRow({ item, current, onOpen }: { item: AccessRequest; current: boolean; onOpen: () => void }) {
  const days = daysUntil(item.due_at);
  const due =
    current
      ? { tone: 'warning' as const, text: 'đang xem' }
      : days <= 0
        ? { tone: 'warning' as const, text: 'hạn hôm nay' }
        : { tone: 'idle' as const, text: `${days} ngày` };
  return (
    <li className="border-b border-border">
      <button
        type="button"
        aria-current={current || undefined}
        onClick={onOpen}
        className={cn('flex w-full items-center gap-da-md py-da-sm text-left', !current && 'hover:bg-subtle')}
      >
        <span className="min-w-0 flex-1 text-foreground da-body-small">{item.queue_label}</span>
        <StatusDot tone={due.tone}>{due.text}</StatusDot>
      </button>
    </li>
  );
}
