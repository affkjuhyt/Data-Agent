import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import {
  listQualityIssues,
  resolveQualityIssue,
  type QualityIssue,
  type ResolveIssue,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P05 · Hàng chờ chất lượng" (2300:19017, UC-05-03): one uncertain page at a time, the original
 * next to what the agent read (personal data already masked), then confirm, correct or drop it.
 * A page not confirmed is never used to answer; dropping it asks for the reason.
 */
export default function QualityQueuePage() {
  const issues = useV1(listQualityIssues, []);
  return (
    <AsyncRegion
      state={issues}
      what="hàng chờ chất lượng"
      skeletonRows={6}
      isEmpty={(p) => p.items.length === 0}
      empty={
        <EmptyState
          className="m-da-3xl"
          icon="check"
          title="Hàng chờ trống"
          description="Mọi trang agent không chắc đã được người xem lại."
        />
      }
    >
      {(p) => (
        <IssueView
          key={p.items[0].id}
          issue={p.items[0]}
          total={p.items.length}
          onResolved={issues.reload}
        />
      )}
    </AsyncRegion>
  );
}

function IssueView({
  issue,
  total,
  onResolved,
}: {
  issue: QualityIssue;
  total: number;
  onResolved: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(issue.read.paragraphs.join('\n\n'));
  const [dropping, setDropping] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<ResolveIssue['action'] | null>(null);
  const [dropError, setDropError] = useState<string>();

  const resolve = async (body: ResolveIssue, done: string) => {
    setBusy(body.action);
    setDropError(undefined);
    try {
      await resolveQualityIssue(issue.id, body);
      toast({ tone: 'success', message: done });
      onResolved();
    } catch (e) {
      const message = (e as Error).message;
      if (body.action === 'drop') setDropError(message);
      else toast({ tone: 'error', message: `Không lưu được. ${message}` });
      setBusy(null);
    }
  };

  const title = `${issue.document.title} · trang ${issue.page}`;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Tài liệu', `Hàng chờ chất lượng · 1/${total}`]}
        title={title}
        description={issue.reason_text}
        actions={
          <>
            <Button kind="ghost" disabled={busy !== null} onClick={() => setDropping(true)}>
              Bỏ trang này
            </Button>
            {editing ? (
              <Button disabled={busy !== null} onClick={() => setEditing(false)}>
                Huỷ sửa
              </Button>
            ) : (
              <Button disabled={busy !== null} onClick={() => setEditing(true)}>
                Sửa văn bản
              </Button>
            )}
            <Button
              kind="primary"
              loading={busy === 'confirm' || busy === 'edit'}
              disabled={busy === 'drop'}
              onClick={() =>
                void (editing
                  ? resolve({ action: 'edit', text }, `Đã lưu văn bản sửa của ${title}.`)
                  : resolve({ action: 'confirm' }, `Đã xác nhận ${title}.`))
              }
            >
              {editing ? 'Lưu và tiếp' : 'Xác nhận và tiếp'}
            </Button>
          </>
        }
      />
      <div className="grid flex-1 grid-cols-2 max-[1023px]:grid-cols-1">
        <section
          aria-label="Ảnh trang gốc"
          className="flex flex-col gap-da-md bg-subtle px-da-3xl py-da-xl"
        >
          <h2 className="text-muted-foreground da-caption">Ảnh trang gốc</h2>
          {issue.image_url ? (
            <img
              src={issue.image_url}
              alt={`Trang ${issue.page} của ${issue.document.title}`}
              className="w-full rounded-lg border border-border bg-card"
            />
          ) : (
            <div className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-2xl py-da-xl text-foreground da-body-small">
              {issue.original_lines.map((line, i) => (
                <p key={i}>{line}</p>
              ))}
            </div>
          )}
          <button
            type="button"
            className="self-start text-primary da-label hover:underline"
            onClick={() => navigate('data', 'DOCUMENT', issue.document.id)}
          >
            Mở trong trình xem tài liệu
          </button>
        </section>
        <section
          aria-label="Văn bản agent đọc được"
          className="flex flex-col gap-da-md border-l border-border bg-card px-da-3xl py-da-xl"
        >
          <h2 className="text-muted-foreground da-caption">Văn bản agent đọc được</h2>
          <div className="flex flex-col gap-da-sm rounded-lg border border-border px-da-lg py-da-md">
            {issue.read.heading && (
              <p className="text-foreground da-body-strong">{issue.read.heading}</p>
            )}
            {editing ? (
              <Textarea
                aria-label="Sửa văn bản agent đọc được"
                value={text}
                onChange={(e) => setText(e.target.value)}
                rows={6}
                className="w-full"
              />
            ) : (
              issue.read.paragraphs.map((p, i) => (
                <p key={i} className="text-foreground da-body">
                  {p}
                </p>
              ))
            )}
            {issue.unreadable.map((u) => (
              <p
                key={u}
                className="rounded-md bg-warning-subtle px-da-md py-da-sm text-foreground da-body-small"
              >
                {u}
              </p>
            ))}
          </div>
          <h3 className="pt-da-xs text-foreground da-heading-3">Agent đã làm</h3>
          <ul className="flex flex-col gap-da-md text-foreground da-caption">
            {issue.agent_actions.map((a) => (
              <li key={a}>· {a}</li>
            ))}
          </ul>
          <p className="text-muted-foreground da-caption">
            UC-05-03 · trang chưa xác nhận không dùng để trả lời
          </p>
        </section>
      </div>
      <Dialog
        open={dropping}
        title="Bỏ trang này?"
        description="Trang bị bỏ không dùng để trả lời. Lý do được ghi vào lịch sử tài liệu."
        submitLabel="Bỏ trang"
        submitting={busy === 'drop'}
        error={dropError}
        onClose={() => setDropping(false)}
        onSubmit={() => {
          if (!reason.trim()) setDropError('Ghi lý do bỏ trang.');
          else void resolve({ action: 'drop', reason: reason.trim() }, `Đã bỏ ${title}.`);
        }}
      >
        <Field labelTone="muted" label="Lý do">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </Dialog>
    </div>
  );
}
