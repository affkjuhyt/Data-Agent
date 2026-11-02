import { StatusDot } from '../../../design-system/StatusDot';
import { hasReceipt } from '../answerMessage';
import { ClarificationCard } from '../components/ClarificationCard';
import { DecompositionCard } from '../components/DecompositionCard';
import { MarkdownContent } from '../components/MarkdownContent';
import { RefusalCard } from '../components/RefusalCard';
import type { AskMessage, Citation, ClarificationReply } from '../types';
import { AnswerBlocks } from './blocks/AnswerBlocks';
import { StepTimeline } from './StepTimeline';
import { TurnFooter } from './TurnFooter';

/*
 * One turn of the agent, top to bottom: what it did (StepTimeline), then its answer — a refusal
 * card, a question back, or the answer's blocks — the "why" decomposition it proposes, the
 * trust label every finished answer carries (invariant 14), the next questions it offers, and
 * the footer.
 */

const TRUST_DOT = { success: 'success', primary: 'primary', warning: 'warning', info: 'info', neutral: 'neutral' } as const;

export function AssistantTurn({
  message,
  busy,
  onRetry,
  onReport,
  onReceipt,
  onSaveKnowledge,
  onSaveSession,
  onAsk,
  onAnswerClarification,
  onRetryRefusal,
  onRephrase,
  onOpenCitation,
  onExportCsv,
}: {
  message: AskMessage;
  busy: boolean;
  onRetry: (id: string) => void;
  onReport: (message: AskMessage) => void;
  onReceipt: (answerId: string) => void;
  onSaveKnowledge?: (answerId: string) => void;
  onSaveSession?: (message: AskMessage) => void;
  onAsk: (text: string) => void;
  onAnswerClarification: (id: string, reply: ClarificationReply) => void;
  onRetryRefusal?: () => void;
  onRephrase?: () => void;
  onOpenCitation?: (citation: Citation) => void;
  onExportCsv?: (message: AskMessage) => void;
}) {
  const running = message.status === 'streaming';
  const finished = !running && message.status !== 'error';
  const hasBlocks = (message.blocks?.length ?? 0) > 0;

  return (
    <article className="flex flex-col gap-da-md" aria-label="Câu trả lời của agent">
      <StepTimeline
        steps={message.steps ?? []}
        running={running}
        durationMs={message.durationMs}
        recorded={message.stepsRecorded !== false}
      />

      {message.refusal ? (
        <RefusalCard refusal={message.refusal} onRetry={onRetryRefusal} onRephrase={onRephrase} disabled={busy} />
      ) : message.clarification ? (
        <>
          {message.content && <MarkdownContent text={message.content} />}
          <ClarificationCard
            clarification={message.clarification}
            disabled={busy}
            onAnswer={(reply) => onAnswerClarification(message.id, reply)}
          />
        </>
      ) : hasBlocks ? (
        <AnswerBlocks
          message={message}
          onOpenCitation={onOpenCitation}
          onExportCsv={onExportCsv ? () => onExportCsv(message) : undefined}
          onSaveToSession={onSaveSession ? () => onSaveSession(message) : undefined}
        />
      ) : (
        message.content && (
          <p className={message.status === 'error' ? 'm-0 text-error da-body' : 'm-0 text-foreground da-body'}>
            {message.content}
          </p>
        )
      )}

      {message.proposal && finished && <DecompositionCard proposal={message.proposal} />}

      {finished && message.trust && !message.refusal && !message.clarification && (
        <span data-testid="trust-label" title={message.trust.meaning} className="inline-flex items-center gap-da-sm">
          <StatusDot tone={TRUST_DOT[message.trust.tone]}>{message.trust.label}</StatusDot>
          {message.trust.breakdown && <span className="text-muted-foreground da-caption">({message.trust.breakdown})</span>}
        </span>
      )}

      {finished && !!message.suggestions?.length && (
        <div className="flex flex-wrap gap-da-sm" aria-label="Hỏi tiếp">
          {message.suggestions.map((q) => (
            <button
              key={q}
              type="button"
              disabled={busy}
              onClick={() => onAsk(q)}
              className="inline-flex items-center gap-da-xs rounded-md border border-border px-da-md py-[5px] text-foreground da-body-small hover:bg-subtle disabled:opacity-50"
            >
              {q}
            </button>
          ))}
        </div>
      )}

      {!running && message.answerId && (
        <TurnFooter
          timestamp={message.timestamp}
          text={message.content}
          receipt={hasReceipt(message)}
          onRetry={() => onRetry(message.id)}
          onReport={() => onReport(message)}
          onReceipt={() => onReceipt(message.answerId!)}
          onSaveKnowledge={onSaveKnowledge ? () => onSaveKnowledge(message.answerId!) : undefined}
          onSaveSession={onSaveSession ? () => onSaveSession(message) : undefined}
        />
      )}
    </article>
  );
}
