import { useMemo, useState } from 'react';
import { Badge } from '../../../design-system/Badge';
import { StatusDot } from '../../../design-system/StatusDot';
import { exportAnswer } from '../../../lib/v1/exports';
import { hasReceipt } from '../answerMessage';
import { ClarificationCard } from '../components/ClarificationCard';
import { ConfidenceMeter } from '../components/ConfidenceMeter';
import { DecompositionCard } from '../components/DecompositionCard';
import { DrillPicker } from '../components/DrillPicker';
import { ExportDialog } from '../components/ExportDialog';
import { MarkdownContent } from '../components/MarkdownContent';
import { RefusalCard } from '../components/RefusalCard';
import { VerifierBadge } from '../components/VerifierBadge';
import { answerTrust } from '../trust';
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

/** UC-01-06 Xuất: the server builds the file (POST /v1/answers/{id}/exports) and hands back a
 *  signed link; the browser follows it. */
async function downloadAnswerXlsx(answerId: string) {
  const job = await exportAnswer(answerId, 'xlsx');
  if (job.download_url) window.location.assign(job.download_url);
}

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
  reported = false,
}: {
  message: AskMessage;
  busy: boolean;
  /** The reader reported this answer: it shows "Đang kiểm tra" until it is reviewed. */
  reported?: boolean;
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
  const [picked, setPicked] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const run = message.evidence?.runs[0] ?? null;
  // Every finished answer carries a trust label (invariant 14): the one the server gave, else the
  // one its runs and citations imply, down to "no evidence".
  const trust = useMemo(
    () => message.trust ?? answerTrust(message.evidence?.runs ?? [], message.citations ?? []),
    [message.trust, message.evidence, message.citations],
  );

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
          onPick={message.drill && finished && !busy ? setPicked : undefined}
        />
      ) : (
        message.content && (
          <p className={message.status === 'error' ? 'm-0 text-error da-body' : 'm-0 text-foreground da-body'}>
            {message.content}
          </p>
        )
      )}

      {message.drill && finished && (
        <DrillPicker
          drill={message.drill}
          members={(message.chartSpecs?.[0]?.data ?? []).map((d) => String(d[message.chartSpecs![0].x_key] ?? ''))}
          picked={picked}
          onPick={setPicked}
          onAsk={onAsk}
          disabled={busy}
        />
      )}

      {message.proposal && finished && <DecompositionCard proposal={message.proposal} />}

      {finished && !message.refusal && !message.clarification && (message.content || hasBlocks) && (
        <div className="flex flex-wrap items-center gap-da-md">
          <span data-testid="trust-label" title={trust.meaning} className="inline-flex items-center gap-da-sm">
            <StatusDot tone={TRUST_DOT[trust.tone]}>{trust.label}</StatusDot>
            {trust.breakdown && <span className="text-muted-foreground da-caption">({trust.breakdown})</span>}
          </span>
          {message.verification && <ConfidenceMeter verification={message.verification} />}
          {reported && <Badge tone="warning">Đang kiểm tra</Badge>}
          {message.verification && <VerifierBadge verification={message.verification} />}
        </div>
      )}

      {finished && !message.refusal && !!message.suggestions?.length && (
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
          onExport={run && (run.rows.length > 0 || message.answerId) ? () => setExportOpen(true) : undefined}
          reported={reported}
        />
      )}

      {run && (
        <ExportDialog
          open={exportOpen}
          run={run}
          onClose={() => setExportOpen(false)}
          onExportCsv={run.rows.length && onExportCsv ? () => onExportCsv(message) : undefined}
          onExportXlsx={message.answerId ? () => downloadAnswerXlsx(message.answerId!) : undefined}
        />
      )}
    </article>
  );
}
