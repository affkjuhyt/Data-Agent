import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Composer } from '../../design-system/Composer';
import { EmptyState } from '../../design-system/EmptyState';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import { askDocuments, type Citation, type DocumentAnswer } from '../../lib/v1/documents';
import { listCollections } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { ReportDialog } from '../ask/components/ReportDialog';

/*
 * "P05 · Trả lời từ tài liệu" (2300:19144, UC-04): a question answered only from documents the
 * reader may read, each claim cited with document, clause, page and validity; the cited passage
 * shows on the right and "Mở đoạn gốc" opens it in the viewer. A superseded source is only cited
 * to say it was superseded, never as the basis.
 */
const ALL_COLLECTIONS = '__all';

export default function DocumentAnswerPage() {
  const toast = useToast();
  const collections = useV1(listCollections, []);
  const [collection, setCollection] = useState<string>(ALL_COLLECTIONS);
  const [answers, setAnswers] = useState<DocumentAnswer[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [selected, setSelected] = useState<Citation | null>(null);
  const [reporting, setReporting] = useState<DocumentAnswer | null>(null);

  const ask = async (question: string) => {
    setPendingQuestion(question);
    try {
      const answer = await askDocuments({
        question,
        collection_id: collection === ALL_COLLECTIONS ? null : collection,
      });
      setAnswers((list) => [...list, answer]);
      setSelected(answer.citations[0] ?? null);
    } catch (e) {
      toast({
        tone: 'error',
        message: `Agent Tài liệu chưa trả lời được. ${(e as Error).message}`,
      });
    } finally {
      setPendingQuestion(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Phiên', 'Hỏi đáp tài liệu']}
        title="Hỏi đáp tài liệu"
        actions={
          <div className="w-60">
            <Select
              label="Chọn bộ sưu tập"
              options={[
                { value: ALL_COLLECTIONS, label: 'Mọi bộ sưu tập được đọc' },
                ...(collections.data?.items ?? [])
                  .filter((c) => !c.archived)
                  .map((c) => ({ value: c.id, label: c.name })),
              ]}
              value={collection}
              onChange={setCollection}
            />
          </div>
        }
      />
      <div className="flex min-h-0 flex-1">
        <section aria-label="Hội thoại" className="flex min-w-0 flex-1 flex-col px-da-4xl py-da-xl">
          <div className="mx-auto flex w-full max-w-[680px] flex-1 flex-col gap-da-2xl">
            {answers.length === 0 && !pendingQuestion && (
              <EmptyState
                icon="file"
                title="Hỏi về quy định, hợp đồng, biên bản…"
                description="Agent Tài liệu chỉ trả lời từ đoạn bạn có quyền đọc, kèm điều khoản, trang và hiệu lực của từng nguồn."
              />
            )}
            {answers.map((a) => (
              <AnswerTurn
                key={a.id}
                answer={a}
                selected={selected}
                onSelect={setSelected}
                onReport={() => setReporting(a)}
              />
            ))}
            {pendingQuestion && (
              <div className="flex flex-col gap-da-md" aria-busy>
                <Question text={pendingQuestion} />
                <StatusDot tone="info">Agent Tài liệu đang tìm trong các văn bản…</StatusDot>
              </div>
            )}
          </div>
          <Composer
            className="mx-auto mt-da-xl w-full max-w-[680px]"
            placeholder="Hỏi về nội dung tài liệu…"
            sending={pendingQuestion !== null}
            onSubmit={(text) => void ask(text)}
          />
        </section>
        <CitedPassage citation={selected} />
      </div>
      <ReportDialog
        open={reporting !== null}
        target={
          reporting
            ? { messageId: reporting.id, question: reporting.question, answer: reporting.answer }
            : null
        }
        onClose={() => setReporting(null)}
        onReported={() => {
          setReporting(null);
          toast({ tone: 'success', message: 'Đã gửi báo sai. Người phụ trách sẽ xem lại.' });
        }}
      />
    </div>
  );
}

function Question({ text }: { text: string }) {
  return (
    <p className="max-w-[80%] self-end rounded-lg bg-subtle px-da-lg py-da-md text-foreground da-body">
      {text}
    </p>
  );
}

function AnswerTurn({
  answer,
  selected,
  onSelect,
  onReport,
}: {
  answer: DocumentAnswer;
  selected: Citation | null;
  onSelect: (c: Citation) => void;
  onReport: () => void;
}) {
  const { navigate } = useAppState();
  const open = selected && answer.citations.includes(selected) ? selected : answer.citations[0];
  return (
    <article className="flex flex-col gap-da-md">
      <Question text={answer.question} />
      <p className="text-muted-foreground da-caption">{answer.scope_text}</p>
      <p className="text-foreground da-body">{answer.answer}</p>
      {answer.citations.length > 0 && (
        <ul className="flex flex-col gap-da-md">
          {answer.citations.map((c) => (
            <li key={c.n}>
              <button
                type="button"
                aria-pressed={selected === c}
                onClick={() => onSelect(c)}
                className={cn(
                  'flex w-full items-center gap-da-md rounded-md border px-da-lg py-da-md text-left hover:bg-subtle',
                  selected === c ? 'border-border-strong' : 'border-border',
                )}
              >
                <span className="shrink-0 text-muted-foreground da-mono">[{c.n}]</span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-foreground da-body-strong">{c.title}</span>
                  <span className="text-muted-foreground da-caption">{c.locator}</span>
                </span>
                <StatusDot tone={c.validity.tone} className="shrink-0">
                  {c.validity.text}
                </StatusDot>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-da-lg">
        <span className="flex-1 text-muted-foreground da-caption">{answer.meta_text}</span>
        {open && (
          <button
            type="button"
            className="text-primary da-label hover:underline"
            onClick={() => navigate('data', 'DOCUMENT', open.document_id)}
          >
            Mở đoạn gốc
          </button>
        )}
        <button type="button" className="text-primary da-label hover:underline" onClick={onReport}>
          Báo sai
        </button>
      </div>
    </article>
  );
}

function CitedPassage({ citation }: { citation: Citation | null }) {
  return (
    <aside
      aria-label="Đoạn gốc"
      className="flex w-[420px] shrink-0 flex-col gap-da-md border-l border-border bg-subtle p-da-xl max-[1023px]:hidden"
    >
      {citation ? (
        <>
          <h2 className="text-foreground da-heading-3">
            [{citation.n}] {citation.title} · {citation.locator.split(' · ')[0]}
          </h2>
          <blockquote className="border-l-2 border-border-strong bg-card px-da-lg py-da-md text-foreground da-body">
            {citation.quote}
          </blockquote>
          {citation.superseded && (
            <p className="text-warning da-body-small">
              Văn bản đã bị thay thế: chỉ trích để nói điều đó, không dùng làm căn cứ.
            </p>
          )}
          <p className="text-muted-foreground da-caption">
            Đoạn được tô là phần agent dùng để trả lời. Bấm “Mở đoạn gốc” để xem trong trình xem tài
            liệu, đúng trang.
          </p>
        </>
      ) : (
        <p className="text-muted-foreground da-body-small">
          Đoạn gốc của nguồn được trích hiện ở đây.
        </p>
      )}
      <p className="text-muted-foreground da-caption">
        UC-05-04 · văn bản bị thay thế chỉ được trích để nói “đã bị thay thế”, không dùng làm căn cứ
      </p>
    </aside>
  );
}
