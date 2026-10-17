import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { Button } from '../../design-system/Button';
import { Composer } from '../../design-system/Composer';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import { askDocuments, type Citation, type DocumentAnswer } from '../../lib/v1/documents';
import { ReportDialog } from '../ask/components/ReportDialog';

/*
 * "P05 · Trả lời từ tài liệu" (2300:19144, UC-04): a question answered only from documents the
 * reader may read, each claim cited with document, clause, page and validity; the cited passage
 * shows on the right and "Mở đoạn gốc" opens it in the viewer. A superseded source is only cited
 * to say it was superseded, never as the basis.
 *
 * Figma also draws a "Chọn bộ sưu tập" menu; it is left out because a question cannot be limited
 * to a collection (A2's QuestionCreate has no scope): the agent reads every collection the reader
 * may read. "Thêm vào phiên" has no session API yet: shown disabled.
 */

export default function DocumentAnswerPage() {
  const toast = useToast();
  const [answers, setAnswers] = useState<DocumentAnswer[]>([]);
  const [pendingQuestion, setPendingQuestion] = useState<string | null>(null);
  const [selected, setSelected] = useState<Citation | null>(null);
  const [reporting, setReporting] = useState<DocumentAnswer | null>(null);

  const share = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast({ tone: 'success', message: 'Đã sao chép liên kết.' });
    } catch {
      toast({ tone: 'error', message: 'Không sao chép được liên kết.' });
    }
  };

  const ask = async (question: string) => {
    setPendingQuestion(question);
    try {
      const answer = await askDocuments({ question });
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
        actions={<Button onClick={() => void share()}>Chia sẻ</Button>}
      />
      <div className="flex min-h-0 flex-1">
        <section
          aria-label="Hội thoại"
          className="flex min-w-0 flex-1 flex-col px-da-5xl pb-da-xl pt-da-2xl"
        >
          <div className="flex w-full flex-1 flex-col gap-da-2xl">
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
              <div className="flex flex-col gap-[14px]" aria-busy>
                <Question text={pendingQuestion} />
                <StatusDot tone="info">Agent Tài liệu đang tìm trong các văn bản…</StatusDot>
              </div>
            )}
          </div>
          <Composer
            className="mt-da-xl w-full"
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
            ? { answerId: reporting.id, question: reporting.question, answer: reporting.answer }
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
    <p className="max-w-[80%] self-end rounded-md bg-subtle px-[14px] py-[10px] text-foreground da-body">
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
    <article className="flex flex-col gap-[14px]">
      <Question text={answer.question} />
      <p className="text-muted-foreground da-caption">{answer.scope_text}</p>
      <div className="flex flex-col gap-[10px]">
        <p className="text-foreground da-reading">{answer.answer}</p>
        {answer.citations.length > 0 && (
          <ul className="flex flex-col gap-[10px]">
            {answer.citations.map((c) => (
              <li key={c.n}>
                <button
                  type="button"
                  aria-pressed={selected === c}
                  onClick={() => onSelect(c)}
                  className={cn(
                    'flex w-full items-center gap-[10px] rounded-sm border px-da-lg py-da-md text-left hover:bg-subtle',
                    selected === c ? 'border-border-strong' : 'border-border',
                  )}
                >
                  <span className="w-6 shrink-0 text-muted-foreground da-mono">[{c.n}]</span>
                  <span className="flex min-w-0 flex-1 flex-col gap-[2px]">
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
        <div className="flex flex-wrap items-center gap-da-xl">
          <span className="flex-1 text-muted-foreground da-caption">{answer.meta_text}</span>
          {open && (
            <button
              type="button"
              className="text-primary da-body-strong hover:underline"
              onClick={() => navigate('data', 'DOCUMENT', open.document_id)}
            >
              Mở đoạn gốc
            </button>
          )}
          <Tooltip content="Thêm vào phiên cần API Phiên làm việc.">
            <button type="button" disabled className="text-primary opacity-50 da-body-strong">
              Thêm vào phiên
            </button>
          </Tooltip>
          <button
            type="button"
            className="text-primary da-body-strong hover:underline"
            onClick={onReport}
          >
            Báo sai
          </button>
        </div>
      </div>
    </article>
  );
}

function CitedPassage({ citation }: { citation: Citation | null }) {
  return (
    <aside
      aria-label="Đoạn gốc"
      className="flex w-[420px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pb-da-xl pt-[20px] max-[1023px]:hidden"
    >
      {citation ? (
        <>
          <h2 className="text-foreground da-heading-3">
            [{citation.n}] {citation.title} · {citation.locator.split(' · ')[0]}
          </h2>
          <blockquote className="border-l-2 border-border bg-card px-[14px] py-[10px] text-foreground da-body">
            {citation.quote}
          </blockquote>
          {citation.superseded && (
            <p className="text-warning da-body-small">
              Văn bản đã bị thay thế: chỉ trích để nói điều đó, không dùng làm căn cứ.
            </p>
          )}
          <p className="text-muted-foreground da-body-small">
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
