import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Field } from '../../design-system/Field';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { resolveHitl } from '../../lib/v1/api';
import {
  investigateDocument,
  listLibrary,
  type DocumentRow,
  type DocumentView,
  type EmailThread,
  type ThreadReview,
} from '../../lib/v1/documents';
import { getCollection } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { Dialog } from '../../design-system/Dialog';
import { FactValue, KeyValue, SidePanel } from './parts';

/*
 * "v3.1 · Phi cấu trúc · Trình xem email" (2075:5236, UC-05-01): the threads of the collection on
 * the left (severity as the agent judged it), the open thread in the middle with the review the
 * agent asked for, and on the right what it extracted plus similar complaints it found.
 */
export function EmailViewer({
  doc,
  thread,
  onChanged,
}: {
  doc: DocumentView;
  thread: EmailThread;
  onChanged: () => void;
}) {
  return (
    <div className="flex min-h-full">
      {doc.collection && <ThreadList collectionId={doc.collection.id} selectedId={doc.id} />}
      <section
        aria-label="Thư"
        className="flex min-w-0 flex-1 flex-col gap-da-md px-da-2xl py-da-xl"
      >
        <h1 className="text-foreground da-heading-1">{thread.subject}</h1>
        {thread.messages.map((m) => (
          <article
            key={m.id}
            className="flex flex-col gap-da-sm rounded-lg border border-border bg-card px-da-xl py-da-lg"
          >
            <header className="flex items-start gap-da-md">
              <span className="min-w-0 flex-1 text-foreground da-body-strong">{m.from}</span>
              <span className="whitespace-nowrap text-muted-foreground da-mono">
                {m.sent_at_text}
              </span>
            </header>
            <p className="text-foreground da-body">{m.body}</p>
            {m.attachments.length > 0 && (
              <p className="flex items-center gap-da-sm text-primary da-caption">
                <Icon name="file" />
                {m.attachments.join(' · ')}
              </p>
            )}
          </article>
        ))}
        {thread.review && <ReviewCard review={thread.review} onChanged={onChanged} />}
      </section>
      <SidePanel label="Agent trích xuất" width="w-[360px]">
        <h2 className="text-muted-foreground da-overline">Agent trích xuất</h2>
        {thread.facts.map((fact) => (
          <KeyValue key={fact.label} label={fact.label}>
            <FactValue fact={fact} />
          </KeyValue>
        ))}
        {thread.similar && <Similar doc={doc} similar={thread.similar} />}
      </SidePanel>
    </div>
  );
}

function ThreadList({ collectionId, selectedId }: { collectionId: string; selectedId: string }) {
  const { navigate } = useAppState();
  const collection = useV1(() => getCollection(collectionId), [collectionId]);
  const threads = useV1(() => listLibrary({ collection_id: collectionId }), [collectionId]);
  return (
    <nav
      aria-label="Các thư"
      className="flex w-[320px] shrink-0 flex-col gap-da-xs border-r border-border bg-card px-da-lg py-da-xl max-[1023px]:hidden"
    >
      <h2 className="text-foreground da-heading-3">
        {collection.data
          ? `${collection.data.name} · ${formatNumber(collection.data.document_count)}`
          : 'Thư'}
      </h2>
      <AsyncRegion state={threads} what="thư" skeletonRows={4}>
        {(p) => (
          <ul className="flex flex-col gap-da-xs">
            {p.items.map((row) => (
              <li key={row.id}>
                <button
                  type="button"
                  aria-current={row.id === selectedId || undefined}
                  onClick={() => navigate('data', 'DOCUMENT', row.id)}
                  className={cn(
                    'flex w-full flex-col gap-da-xs rounded-md border p-da-md text-left hover:bg-subtle',
                    row.id === selectedId ? 'border-border' : 'border-transparent',
                  )}
                >
                  <span className="text-foreground da-body-strong">{row.title}</span>
                  {row.thread && <ThreadMeta thread={row.thread} />}
                </button>
              </li>
            ))}
          </ul>
        )}
      </AsyncRegion>
    </nav>
  );
}

function ThreadMeta({ thread }: { thread: NonNullable<DocumentRow['thread']> }) {
  const { level, text } = thread.severity;
  return (
    <span className="flex flex-wrap items-center gap-da-sm">
      <span className="text-muted-foreground da-caption">{thread.from_text}</span>
      {level === 'low' ? (
        <Badge>{text}</Badge>
      ) : (
        <StatusDot tone={level === 'high' ? 'error' : 'warning'}>{text}</StatusDot>
      )}
    </span>
  );
}

/* The review the agent asked for (HITLRequest 2040:549), answered with the ordinary :resolve. */
function ReviewCard({ review, onChanged }: { review: ThreadReview; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string>();

  const answer = async (decision: 'approve' | 'reject', text?: string) => {
    setBusy(true);
    setError(undefined);
    try {
      await resolveHitl(review.hitl_id, { decision, note: text });
      toast({
        tone: 'success',
        message:
          decision === 'approve' ? 'Đã chấp nhận bản nháp.' : 'Đã gửi yêu cầu sửa cho agent.',
      });
      setAsking(false);
      onChanged();
    } catch (e) {
      const message = (e as Error).message;
      if (decision === 'reject') setError(message);
      else toast({ tone: 'error', message: `Không gửi được. ${message}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg">
      <div className="flex items-center gap-da-sm">
        <Badge tone="accent">{review.kind_label}</Badge>
        <span className="min-w-0 flex-1 text-muted-foreground da-caption">{review.from_text}</span>
      </div>
      <p className="text-foreground da-heading-3">{review.title}</p>
      <p className="text-muted-foreground da-body-small">{review.detail}</p>
      <div className="flex flex-wrap gap-x-da-sm gap-y-da-md">
        <Button kind="ghost" disabled={busy} onClick={() => setAsking(true)}>
          Yêu cầu sửa
        </Button>
        <Button kind="primary" loading={busy && !asking} onClick={() => void answer('approve')}>
          Chấp nhận
        </Button>
      </div>
      <Dialog
        open={asking}
        title="Yêu cầu sửa bản nháp"
        description="Agent sửa theo ghi chú rồi gửi lại cho bạn duyệt."
        submitLabel="Gửi yêu cầu"
        submitting={busy}
        error={error}
        onClose={() => setAsking(false)}
        onSubmit={() => {
          if (!note.trim()) setError('Ghi rõ cần sửa gì.');
          else void answer('reject', note.trim());
        }}
      >
        <Field labelTone="muted" label="Cần sửa gì">
          <Input value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Dialog>
    </div>
  );
}

function Similar({
  doc,
  similar,
}: {
  doc: DocumentView;
  similar: NonNullable<EmailThread['similar']>;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const investigate = async () => {
    setBusy(true);
    try {
      await investigateDocument(doc.id);
      toast({ tone: 'success', message: 'Agent Phân tích đã mở lượt điều tra nguyên nhân.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không mở được lượt điều tra. ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="flex flex-col items-start gap-da-xs rounded-md border border-border px-da-lg py-da-md">
      <p className="text-warning da-body-strong">{similar.title}</p>
      <p className="text-foreground da-caption">{similar.detail}</p>
      <Button loading={busy} onClick={() => void investigate()}>
        Mở lượt điều tra
      </Button>
    </div>
  );
}
