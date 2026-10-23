import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Avatar } from '../../design-system/Avatar';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { formatDateTime, formatTime } from '../../lib/formatVN';
import { decideProposal, listUsers, resolveHitl } from '../../lib/v1/api';
import { getCollaboration, type Collaboration } from '../../lib/v1/collaboration';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Cộng tác · Duyệt nhiều người" (2084:7085, UC-07-03): a proposal several people review in
 * turn. Across the top the chain — the agent that proposed it, who agreed, who is reviewing now
 * (outlined when it is you) and who waits; the deadline; on the right the decision log. The
 * reviewer approves, asks for changes with a note, or hands the review to someone else (only
 * while the review request is theirs). UC-07-03 has no discussion thread on a proposal.
 */
type DialogKind = 'changes' | 'reassign' | null;

const STEP = {
  sent: null,
  agreed: { label: 'Đồng ý', tone: 'success' },
  viewing: { label: 'Đang xem', tone: 'info' },
  waiting: { label: 'Chờ', tone: 'warning' },
  changes_requested: { label: 'Yêu cầu sửa', tone: 'error' },
} as const;

export default function ApprovalChainPage() {
  const { routeId } = useAppState();
  const collaboration = useV1(['getCollaboration', routeId], routeId ? () => getCollaboration(routeId) : null);
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Mở luồng duyệt từ một đề xuất trong Cần bạn.
      </p>
    );
  return (
    <AsyncRegion state={collaboration} what="luồng duyệt">
      {(c) => (
        <ChainBody
          collaboration={c}
          open={c.status === 'pending_review'}
          onChanged={collaboration.reload}
        />
      )}
    </AsyncRegion>
  );
}

function ChainBody({
  collaboration,
  open,
  onChanged,
}: {
  collaboration: Collaboration;
  open: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [dialog, setDialog] = useState<DialogKind>(null);
  const [note, setNote] = useState('');
  const [assignee, setAssignee] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const users = useV1(['listUsers', dialog === 'reassign'], dialog === 'reassign' ? listUsers : null);

  const act = async (key: string, call: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      setDialog(null);
      setNote('');
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const openDialog = (d: DialogKind) => {
    setError(undefined);
    setNote('');
    setDialog(d);
  };

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl pb-da-2xl pt-da-lg">
        <PageHeader
          breadcrumb={['Cần bạn', 'Duyệt']}
          title={collaboration.title}
          actions={
            open && (
              <>
                {collaboration.hitl_id && (
                  <Button
                    kind="ghost"
                    disabled={busy !== null}
                    onClick={() => openDialog('reassign')}
                  >
                    Chuyển người duyệt
                  </Button>
                )}
                <Button disabled={busy !== null} onClick={() => openDialog('changes')}>
                  Yêu cầu sửa
                </Button>
                <Button
                  kind="primary"
                  icon="approve"
                  loading={busy === 'approve'}
                  disabled={busy !== null}
                  onClick={() =>
                    act(
                      'approve',
                      () => decideProposal(collaboration.proposal_id, { decision: 'accept' }),
                      'Đã duyệt; chuyển bước tiếp theo',
                    )
                  }
                >
                  Duyệt
                </Button>
              </>
            )
          }
        />
        {!open && <Notice tone="info">Đề xuất này đã được xử lý.</Notice>}
        {error && !dialog && <Notice tone="error">Không gửi được: {error}</Notice>}
        <ol
          aria-label="Người duyệt"
          className="grid grid-cols-4 gap-da-sm max-[1279px]:grid-cols-2"
        >
          {collaboration.steps.map((s, i) => {
            const look = STEP[s.state];
            return (
              <li
                key={`${s.name}-${i}`}
                aria-current={s.me ? 'step' : undefined}
                className={cn(
                  'flex items-start gap-da-sm rounded-md border bg-card px-da-sm py-da-sm',
                  s.me ? 'border-primary' : 'border-border',
                )}
              >
                {s.person ? (
                  <Avatar name={s.name} />
                ) : (
                  <Icon name="agent" className="mt-[2px] text-primary" />
                )}
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-foreground da-body-small">{s.name}</span>
                  <span className="text-muted-foreground da-caption">
                    {[s.role_text, s.at && formatTime(s.at)].filter(Boolean).join(' · ')}
                  </span>
                </span>
                {look ? (
                  <StatusDot tone={look.tone}>{look.label}</StatusDot>
                ) : (
                  <Badge tone="neutral">Đã gửi</Badge>
                )}
              </li>
            );
          })}
        </ol>
        {collaboration.deadline_at && (
          <p className="flex items-center gap-da-sm rounded-md border border-border px-da-md py-da-sm text-foreground da-body-small">
            <Icon name="clock" className="text-warning" />
            Hạn duyệt {formatDateTime(collaboration.deadline_at)}
          </p>
        )}
      </div>
      <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border px-da-lg pt-da-lg max-[1023px]:w-full max-[1023px]:border-l-0">
        <h2 className="text-foreground da-heading-3">Nhật ký quyết định</h2>
        <ol className="flex flex-col gap-da-sm">
          {collaboration.log.map((l, i) => (
            <li key={`${l.at}-${i}`} className="text-foreground da-mono">
              {formatTime(l.at)} {l.text}
            </li>
          ))}
        </ol>
      </aside>
      <Dialog
        open={dialog === 'changes'}
        size="sm"
        title="Yêu cầu sửa"
        description="Người đề xuất nhận ghi chú và gửi lại bản mới; các bước đã đồng ý được hỏi lại."
        submitLabel="Gửi yêu cầu"
        submitting={busy === 'changes'}
        canSubmit={note.trim() !== ''}
        error={dialog === 'changes' ? error : undefined}
        onClose={() => setDialog(null)}
        onSubmit={() =>
          act(
            'changes',
            () =>
              decideProposal(collaboration.proposal_id, {
                decision: 'request_changes',
                note: note.trim(),
              }),
            'Đã gửi yêu cầu sửa',
          )
        }
      >
        <Textarea
          aria-label="Cần sửa gì"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
        />
      </Dialog>
      <Dialog
        open={dialog === 'reassign'}
        size="sm"
        title="Chuyển người duyệt"
        description="Người nhận thay bạn ở bước này; bạn vẫn xem được thảo luận."
        submitLabel="Chuyển"
        submitting={busy === 'reassign'}
        canSubmit={assignee !== null}
        error={dialog === 'reassign' ? error : undefined}
        onClose={() => setDialog(null)}
        onSubmit={() => {
          const user = users.data?.items.find((u) => u.id === assignee);
          const hitlId = collaboration.hitl_id;
          if (!user || !hitlId) return;
          act(
            'reassign',
            () =>
              resolveHitl(hitlId, { decision: 'reassign', reassign_to: user.id }),
            `Đã chuyển cho ${user.display_name}`,
          );
        }}
      >
        <Select
          label="Người duyệt thay"
          value={assignee}
          placeholder="Chọn người"
          onChange={setAssignee}
          options={(users.data?.items ?? [])
            .filter((u) => u.kind === 'person')
            .map((u) => ({ value: u.id, label: u.display_name }))}
        />
      </Dialog>
    </div>
  );
}
