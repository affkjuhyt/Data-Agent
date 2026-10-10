import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { SideRail } from '../../components/shell/SideRail';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { decideProposal, getProposal, listInbox } from '../../lib/v1/api';
import type { DecideProposal, Proposal } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { decisionKindOf } from './decision';

/*
 * "P07 · Duyệt đề xuất" (2301:19369, UC-07-03): one proposed change, decided by a person.
 * The Cần bạn rail (counts by kind), a bordered header with the three decisions (Từ chối,
 * Yêu cầu sửa, Chấp nhận), the change side by side, its reason and a backtest of its effect;
 * on the right, on --background, the checks it passed and who must approve it.
 * Rejecting needs a reason (proposal_review: note required for reject).
 */
type Dialog = { decision: 'reject' | 'request_changes' } | null;

const dueText = (iso: string) => {
  const d = new Date(iso);
  const weekday = ['Chủ Nhật', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy'][
    d.getDay()
  ];
  return `hạn ${d.toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' })} ${weekday}`;
};

export default function ProposalPage() {
  const { routeId, navigate } = useAppState();
  const toast = useToast();
  const proposal = useV1(routeId ? () => getProposal(routeId) : null, [routeId]);
  const inbox = useV1(() => listInbox({ status: 'open' }), []);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState('');
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [noteMissing, setNoteMissing] = useState(false);

  const decide = async (p: Proposal, body: DecideProposal) => {
    setSending(true);
    setError(undefined);
    try {
      await decideProposal(p.id, body);
      const word = {
        accept: 'Đã chấp nhận',
        accept_with_edit: 'Đã chấp nhận',
        reject: 'Đã từ chối',
        request_changes: 'Đã yêu cầu sửa',
      }[body.decision];
      toast({ tone: 'success', message: `${word} ${p.code}` });
      navigate('needs-you');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
      setDialog(null);
    }
  };

  const items = inbox.data?.items ?? [];
  const countOf = (kind: string) => items.filter((r) => decisionKindOf(r) === kind).length;

  return (
    <div className="flex min-h-full">
      <SideRail
        label="Cần bạn"
        active="APPROVE"
        onSelect={() => navigate('needs-you')}
        counts={
          inbox.data
            ? {
                ALL: items.length,
                APPROVE: countOf('approve'),
                CHOOSE: countOf('choose'),
                COMPARE: countOf('compare'),
              }
            : undefined
        }
        groups={[
          {
            label: 'Cần bạn',
            items: [
              { id: 'ALL', label: 'Tất cả' },
              { id: 'APPROVE', label: 'Duyệt thay đổi' },
              { id: 'CHOOSE', label: 'Chọn phương án' },
              { id: 'COMPARE', label: 'So sánh nguồn' },
            ],
          },
        ]}
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <AsyncRegion state={proposal} what="đề xuất">
          {(p) => {
            const open = p.status === 'pending_review';
            return (
              <>
                <PageHeader
                  bordered
                  breadcrumb={['Cần bạn', 'Duyệt thay đổi', p.code]}
                  title={
                    <span className="flex items-center gap-[10px]">
                      {p.title}
                      {open && <StatusDot tone="warning">Chờ bạn</StatusDot>}
                    </span>
                  }
                  description={`Đề xuất bởi ${p.proposed_by_label} · ${dueText(p.expires_at)}`}
                  actions={
                    open && (
                      <>
                        <Button kind="ghost" onClick={() => setDialog({ decision: 'reject' })}>
                          Từ chối
                        </Button>
                        <Button onClick={() => setDialog({ decision: 'request_changes' })}>
                          Yêu cầu sửa
                        </Button>
                        <Button
                          kind="primary"
                          loading={sending && !dialog}
                          onClick={() => decide(p, { decision: 'accept' })}
                        >
                          Chấp nhận
                        </Button>
                      </>
                    )
                  }
                />
                {!open && (
                  <div className="px-da-3xl pt-da-xl">
                    <Notice tone="info">Đề xuất này đã được xử lý.</Notice>
                  </div>
                )}
                {error && (
                  <div className="px-da-3xl pt-da-xl">
                    <Notice tone="error">Không gửi được quyết định: {error}</Notice>
                  </div>
                )}
                <div className="flex min-h-0 flex-1">
                  <div className="flex min-w-0 flex-1 flex-col pl-da-3xl pr-da-2xl">
                    <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                      <h2 className="text-foreground da-heading-3">Thay đổi</h2>
                      <div className="flex gap-da-lg">
                        {[p.diff.before, p.diff.after].map((side, i) => (
                          <div
                            key={side.label}
                            className="flex min-w-0 flex-1 flex-col gap-da-sm rounded-md border border-border px-[14px] py-da-lg"
                          >
                            <span className="text-muted-foreground da-caption">{side.label}</span>
                            <pre
                              className={`whitespace-pre-wrap da-mono ${i === 0 ? 'text-muted-foreground' : 'text-foreground'}`}
                            >
                              {side.text}
                            </pre>
                          </div>
                        ))}
                      </div>
                      {p.reason && (
                        <p className="text-foreground da-body-small">Lý do: {p.reason}</p>
                      )}
                    </section>
                    <section className="flex flex-col gap-[10px] border-b border-border py-da-xl">
                      <div className="flex items-center gap-da-md">
                        <h2 className="flex-1 text-foreground da-heading-3">Ảnh hưởng</h2>
                        <span className="text-muted-foreground da-caption">
                          {p.impact.window_label}
                        </span>
                      </div>
                      <table className="w-full border-collapse">
                        <thead>
                          <tr className="border-b border-border text-left text-muted-foreground da-caption">
                            <th className="w-[90px] px-da-lg py-da-md font-normal">Kỳ</th>
                            <th className="w-[100px] px-da-lg py-da-md font-normal">
                              {p.impact.columns[0]}
                            </th>
                            <th className="w-[100px] px-da-lg py-da-md font-normal">
                              {p.impact.columns[1]}
                            </th>
                            <th className="px-da-lg py-da-md font-normal">Chênh</th>
                          </tr>
                        </thead>
                        <tbody>
                          {p.impact.rows.map((row) => (
                            <tr key={row.period} className="border-b border-border">
                              <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                                {row.period}
                              </td>
                              <td className="px-da-lg py-da-md text-foreground da-mono">
                                {row.before}
                              </td>
                              <td className="px-da-lg py-da-md text-foreground da-mono">
                                {row.after}
                              </td>
                              <td className="px-da-lg py-da-md text-foreground da-body-small">
                                {row.delta}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p
                        className={`da-body-small ${p.impact_level === 'low' ? 'text-foreground' : 'text-warning'}`}
                      >
                        {p.impact.summary}
                      </p>
                    </section>
                  </div>
                  <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
                    <h2 className="text-foreground da-heading-3">Kiểm thử</h2>
                    {p.checks.map((check) => (
                      <p
                        key={check.text}
                        className="flex items-center gap-da-md text-foreground da-body-small"
                      >
                        <Icon
                          name={check.ok ? 'check' : 'alert'}
                          size={14}
                          className={check.ok ? 'text-success' : 'text-warning'}
                        />
                        <span className="sr-only">{check.ok ? 'Đạt:' : 'Cần chú ý:'}</span>
                        {check.text}
                      </p>
                    ))}
                    <h2 className="text-foreground da-heading-3">Duyệt nhiều bước</h2>
                    <p className="text-foreground da-body-small">
                      {p.approval.reason}:{' '}
                      {p.approval.steps.map((s) => `${s.name} (${s.role})`).join(' → ')}.
                    </p>
                    <p className="text-muted-foreground da-caption">{p.approval.note}</p>
                  </aside>
                </div>
                <ConfirmDialog
                  open={dialog !== null}
                  title={
                    dialog?.decision === 'reject' ? `Từ chối ${p.code}?` : `Yêu cầu sửa ${p.code}?`
                  }
                  confirmLabel={dialog?.decision === 'reject' ? 'Từ chối' : 'Gửi yêu cầu sửa'}
                  tone={dialog?.decision === 'reject' ? 'danger' : 'primary'}
                  loading={sending}
                  error={noteMissing ? 'Ghi lý do từ chối để agent biết cần sửa gì.' : undefined}
                  onCancel={() => {
                    setDialog(null);
                    setNoteMissing(false);
                  }}
                  onConfirm={() => {
                    if (dialog?.decision === 'reject' && !note.trim()) {
                      setNoteMissing(true);
                      return;
                    }
                    setNoteMissing(false);
                    void decide(p, { decision: dialog!.decision, note: note.trim() || undefined });
                  }}
                >
                  <div className="flex flex-col gap-da-sm">
                    <span>
                      {dialog?.decision === 'reject'
                        ? 'Đề xuất sẽ đóng; agent đề xuất nhận lý do của bạn.'
                        : 'Đề xuất quay lại cho agent sửa theo ghi chú của bạn.'}
                    </span>
                    <textarea
                      value={note}
                      onChange={(e) => setNote(e.target.value)}
                      aria-label={
                        dialog?.decision === 'reject'
                          ? 'Lý do từ chối (bắt buộc)'
                          : 'Ghi chú cho agent'
                      }
                      placeholder={
                        dialog?.decision === 'reject' ? 'Lý do từ chối (bắt buộc)' : 'Cần sửa gì?'
                      }
                      rows={3}
                      className="w-full rounded-md border border-border-strong bg-card px-da-lg py-da-md text-foreground outline-none da-body-small focus-visible:border-ring"
                    />
                  </div>
                </ConfirmDialog>
              </>
            );
          }}
        </AsyncRegion>
      </div>
    </div>
  );
}
