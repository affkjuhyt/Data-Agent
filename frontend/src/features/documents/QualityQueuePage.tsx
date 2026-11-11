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
import { useV1, useV1Mutation } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P05 · Hàng chờ chất lượng" (2300:19017, UC-05-03): one uncertain page at a time, the original
 * next to what the agent read (personal data already masked), then confirm, correct or drop it.
 * A page not confirmed is never used to answer; dropping it asks for the reason.
 */
export default function QualityQueuePage() {
  const issues = useV1(['listQualityIssues'], listQualityIssues);
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
      {(p) => <IssueView key={p.items[0].id} issue={p.items[0]} total={p.items.length} />}
    </AsyncRegion>
  );
}

function IssueView({ issue, total }: { issue: QualityIssue; total: number }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(issue.read.paragraphs.join('\n\n'));
  const [dropping, setDropping] = useState(false);
  const [reason, setReason] = useState('');
  // A missing reason is caught here; a failed drop shows in the dialog, other failures toast.
  const [invalid, setInvalid] = useState<string>();
  // Once resolved, the page stays busy until the reloaded queue replaces it (keyed on the id).
  const [resolvedAs, setResolvedAs] = useState<ResolveIssue['action'] | null>(null);
  const resolve = useV1Mutation(
    ({ body }: { body: ResolveIssue; done: string }) => resolveQualityIssue(issue.id, body),
    {
      invalidates: [['listQualityIssues']],
      onSuccess: (_, { body, done }) => {
        setResolvedAs(body.action);
        toast({ tone: 'success', message: done });
      },
      onError: (e, { body }) => {
        if (body.action !== 'drop')
          toast({ tone: 'error', message: `Không lưu được. ${e.message}` });
      },
    },
  );
  const busy = resolve.pending ? (resolve.variables?.body.action ?? null) : resolvedAs;
  const dropError =
    invalid ?? (resolve.variables?.body.action === 'drop' ? resolve.error?.message : undefined);
  const run = (body: ResolveIssue, done: string) => {
    setInvalid(undefined);
    resolve.run({ body, done });
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
                editing
                  ? run({ action: 'edit', text }, `Đã lưu văn bản sửa của ${title}.`)
                  : run({ action: 'confirm' }, `Đã xác nhận ${title}.`)
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
          className="flex flex-col gap-da-md bg-background px-da-3xl py-da-xl"
        >
          <h2 className="text-muted-foreground da-caption">Ảnh trang gốc</h2>
          {issue.image_url ? (
            <img
              src={issue.image_url}
              alt={`Trang ${issue.page} của ${issue.document.title}`}
              className="w-full rounded-[4px] border border-border bg-card"
            />
          ) : (
            <div className="flex flex-col gap-[10px] rounded-[4px] border border-border bg-card px-[28px] py-da-2xl text-muted-foreground da-body">
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
          className="flex flex-col gap-[10px] border-l border-border bg-card px-da-3xl py-da-xl"
        >
          <h2 className="text-muted-foreground da-caption">Văn bản agent đọc được</h2>
          <div className="flex flex-col gap-da-md rounded-md border border-border px-da-xl py-[14px]">
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
                className="rounded-sm bg-warning-subtle px-[10px] py-da-sm text-foreground da-body-small"
              >
                {u}
              </p>
            ))}
          </div>
          <h3 className="text-foreground da-heading-3">Agent đã làm</h3>
          <ul className="flex flex-col gap-[10px] text-foreground da-body-small">
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
          if (!reason.trim()) setInvalid('Ghi lý do bỏ trang.');
          else run({ action: 'drop', reason: reason.trim() }, `Đã bỏ ${title}.`);
        }}
      >
        <Field labelTone="muted" label="Lý do">
          <Input value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </Dialog>
    </div>
  );
}
