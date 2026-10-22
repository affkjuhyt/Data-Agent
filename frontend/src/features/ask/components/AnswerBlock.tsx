/**
 * One assistant answer, in the order of "P01 · Hỏi đáp" (2296:18531…18620); a refused turn is
 * one RefusalCard instead — it has no answer, table or sources to show:
 *   1 "Hệ thống hiểu là" chips → 2 text (with citation markers) → 3 clarification card
 *   → 4 charts → 5 data tables → 6 "Vì sao tin được" strip → 7 sources
 *   → 8 meta row: trust label, timing, Biên nhận · Cách tính · Xuất · Sao chép · Lưu câu hỏi · Lưu thành tri thức · Báo sai
 *   → 9 follow-up chips.
 * A reading the agent wants checked comes first (InterpretationCheck). While streaming, the agent's steps lead and a result still arriving follows the text
 * (AgentProgress). The trust label is on every finished answer (invariant 14). Used by the Ask screen and the
 * agent Playground.
 */
import { lazy, Suspense, useMemo, useState } from 'react';
import { Badge } from '../../../design-system/Badge';
import { StatusDot } from '../../../design-system/StatusDot';
import { SuggestionChip } from '../../../design-system/SuggestionChip';
import { formatDuration, formatTime } from '../../../lib/formatVN';
import { exportAnswer } from '../../../lib/v1/exports';
import { csvFileName, downloadCsv, toCsv } from '../exportCsv';
import { interpretationChips } from '../interpretation';
import { resultSetToEvidenceRun } from '../resultSets';
import { answerTrust } from '../trust';
import type { AskMessage, Citation, ClarificationReply, EvidenceRun } from '../types';
import { CitationPanel } from './CitationPanel';
import { ClarificationCard } from './ClarificationCard';
import { DecompositionCard } from './DecompositionCard';
import { DrillPicker } from './DrillPicker';
import { DataGrid } from './DataGrid';
import { EvidenceStrip } from './EvidenceStrip';
import { hasEvidence, primaryRun } from './evidenceSummary';
import { ExportDialog } from './ExportDialog';
import { MarkdownContent } from './MarkdownContent';
import { RefusalCard } from './RefusalCard';
import { AgentSteps, PartialResult } from './AgentProgress';
import { InterpretationCheck } from './InterpretationCheck';
import { VerifierBadge } from './VerifierBadge';

const ChartRenderer = lazy(() =>
  import('./ChartRenderer').then(module => ({ default: module.ChartRenderer })),
);

interface AnswerBlockProps {
  msg: AskMessage;
  /** the user question this answers — used for the export file name */
  question?: string;
  busy?: boolean;
  onSuggestion?: (text: string) => void;
  onAnswerClarification?: (reply: ClarificationReply) => void;
  onSave?: () => void;
  /** "Cách tính": open the side panel for this answer */
  onOpenEvidence?: () => void;
  /** hide the inline strip when a side panel already shows the details */
  showEvidenceStrip?: boolean;
  showTimestamp?: boolean;
  /** refused turn, next_action "retry": send the same question again */
  onRetry?: () => void;
  /** refused turn, next_action "rephrase": put the question back in the input */
  onRephrase?: () => void;
  /** Playground: show the refusal's code and lookup id */
  showRefusalDetail?: boolean;
  /** "Báo sai": open the report dialog for this answer */
  onReport?: () => void;
  /** the reader reported this answer; it shows "Đang kiểm tra" until review */
  reported?: boolean;
  /** "Biên nhận": open P01 Biên nhận câu trả lời (UC-01-07) for this answer */
  onOpenReceipt?: () => void;
  /** opens the document a citation points at, at its page */
  onOpenCitation?: (citation: Citation) => void;
  /** "Lưu thành tri thức": open P07 Lưu câu trả lời thành tri thức for this answer */
  onSaveKnowledge?: () => void;
  /** "Thêm vào phiên": keep this answer as a workspace tile */
  onSaveToSession?: () => void;
  /** already saved to a session this visit — clear UX instead of duplicating */
  savedToSession?: boolean;
  /** "Dừng" on a result still arriving */
  onStop?: () => void;
  /** "Đúng rồi, chạy" on the agent's reading of the question */
  onConfirmUnderstanding?: (choice: { meaning?: string; remember: boolean }) => void;
  /** "Sửa cách hiểu": back to the question to change it */
  onEditUnderstanding?: () => void;
}

const TRUST_DOT = { success: 'success', primary: 'primary', warning: 'warning', info: 'info', neutral: 'neutral' } as const;
const LINK = 'shrink-0 whitespace-nowrap text-primary hover:underline da-body-strong disabled:cursor-not-allowed disabled:text-muted-foreground disabled:no-underline';

/** UC-01-06 Xuất: the server builds the file (POST /v1/answers/{id}/exports) and hands back a
 *  signed link; the browser follows it. */
async function downloadAnswerXlsx(answerId: string) {
  const job = await exportAnswer(answerId, 'xlsx');
  if (job.download_url) window.location.assign(job.download_url);
}

function TypingDots() {
  return (
    <span data-testid="typing" aria-label="Đang trả lời" className="inline-flex items-center gap-da-xs py-da-xs">
      {[0, 1, 2].map(i => (
        <span key={i} className="size-1.5 animate-pulse rounded-full bg-muted-foreground" style={{ animationDelay: `${i * 0.18}s` }} />
      ))}
    </span>
  );
}

function CopyLink({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <button type="button" className={LINK} onClick={copy}>
      {done ? 'Đã chép' : 'Sao chép'}
    </button>
  );
}

export function AnswerBlock({
  msg, question, busy, onSuggestion, onAnswerClarification, onSave, onOpenEvidence,
  showEvidenceStrip = true, showTimestamp = true, onRetry, onRephrase, showRefusalDetail,
  onReport, reported, onOpenReceipt, onOpenCitation,
  onSaveKnowledge, onSaveToSession, savedToSession, onStop, onConfirmUnderstanding, onEditUnderstanding,
}: AnswerBlockProps) {
  const [exportOpen, setExportOpen] = useState(false);
  // UC-01-04: the bar picked on the chart, to drill into by one of the metric's other cuts.
  const [picked, setPicked] = useState<string | null>(null);
  const resultRuns = useMemo(() => (msg.resultSets ?? []).map(resultSetToEvidenceRun), [msg.resultSets]);
  const run = resultRuns[0] ?? primaryRun(msg.evidence);
  const displayedRuns = resultRuns.length ? resultRuns : run ? [run] : [];
  const displayEvidence = useMemo(() => resultRuns.length
    ? { runs: resultRuns, clarifications: msg.evidence?.clarifications ?? [], warnings: msg.evidence?.warnings ?? [] }
    : msg.evidence, [msg.evidence, resultRuns]);
  const streaming = msg.status === 'streaming';
  const allRuns = displayEvidence?.runs ?? [];
  const trust = useMemo(() => msg.trust ?? answerTrust(allRuns, msg.citations ?? []), [msg.trust, allRuns, msg.citations]);
  const reading = useMemo(() => msg.reading ?? interpretationChips(run), [msg.reading, run]);

  const exportCsv = (target: EvidenceRun | null = run) => {
    if (!target || !target.rows.length) return;
    downloadCsv(csvFileName(question ?? msg.content.slice(0, 40)), toCsv(target.columns, target.rows));
  };
  const exportable = !!run && (run.rows.length > 0 || !!msg.answerId);

  if (msg.refusal) {
    return (
      <div data-testid="answer-block" className="flex flex-col">
        <RefusalCard
          refusal={msg.refusal}
          onRetry={onRetry}
          onRephrase={onRephrase}
          showDetail={showRefusalDetail}
          disabled={busy}
        />
      </div>
    );
  }

  // Asking back is not an answer yet: no label until something was answered.
  const answered = !streaming && (!!msg.content || allRuns.length > 0) && !(msg.clarification && !allRuns.length);

  return (
    <div data-testid="answer-block" className="flex flex-col gap-da-lg">
      {streaming && msg.progress && <AgentSteps progress={msg.progress} />}

      {msg.understanding && onConfirmUnderstanding && (
        <InterpretationCheck
          understanding={msg.understanding}
          disabled={busy}
          onConfirm={onConfirmUnderstanding}
          onEdit={onEditUnderstanding}
        />
      )}

      {reading.length > 0 && (
        <div className="flex flex-col gap-da-md">
          <span className="text-muted-foreground da-caption">
            Hệ thống hiểu là{msg.newQuestion ? ' · Câu hỏi mới' : ''}
          </span>
          <ul aria-label="Hệ thống hiểu là" className="m-0 flex list-none flex-wrap gap-da-sm p-0">
            {reading.map(chip => (
              <li key={chip.key} className="inline-flex items-center gap-da-sm rounded-sm border border-border px-[10px] py-[5px]">
                <span className="text-muted-foreground da-caption">{chip.label}</span>
                <span className="text-foreground da-body-strong">{chip.value}</span>
                {chip.inherited && <span className="text-muted-foreground da-caption">từ câu trước</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {msg.content ? <MarkdownContent text={msg.content} citations={msg.citations} onOpenCitation={onOpenCitation} /> : streaming ? <TypingDots /> : null}

      {streaming && msg.progress?.partial && <PartialResult partial={msg.progress.partial} onStop={onStop} />}

      {msg.clarification && onAnswerClarification && (
        <ClarificationCard clarification={msg.clarification} disabled={busy} onAnswer={onAnswerClarification} />
      )}

      {!!msg.chartSpecs?.length && (
        <Suspense fallback={<div className="min-h-[180px] rounded-md border border-border" />}>
          {msg.chartSpecs.map((spec, i) => (
            <ChartRenderer
              key={i}
              spec={spec}
              onPick={msg.drill && onSuggestion && !busy ? setPicked : undefined}
            />
          ))}
        </Suspense>
      )}

      {msg.drill && onSuggestion && !streaming && (
        <DrillPicker
          drill={msg.drill}
          members={(msg.chartSpecs?.[0]?.data ?? []).map(d => String(d[msg.chartSpecs![0].x_key] ?? ''))}
          picked={picked}
          onPick={setPicked}
          onAsk={onSuggestion}
          disabled={busy}
        />
      )}

      {displayedRuns.map((displayRun, index) => {
        return (
          <DataGrid
            key={displayRun.id ?? index}
            columns={displayRun.columns}
            columnUnits={displayRun.column_units}
            rows={displayRun.rows}
            rowCount={displayRun.row_count}
            capped={displayRun.rows_capped}
            truncation={displayRun.truncation}
            title={displayedRuns.length > 1 ? `Kết quả truy vấn ${index + 1}` : 'Bảng dữ liệu'}
            onExport={displayRun.rows.length ? () => exportCsv(displayRun) : undefined}
          />
        );
      })}

      {!streaming && msg.proposal && <DecompositionCard proposal={msg.proposal} />}

      {showEvidenceStrip && displayEvidence && hasEvidence(displayEvidence) && (
        <EvidenceStrip evidence={displayEvidence} onOpenPanel={onOpenEvidence} />
      )}

      {!streaming && msg.verification && <VerifierBadge verification={msg.verification} />}

      {msg.citations && msg.citations.length > 0 && <CitationPanel citations={msg.citations} onOpen={onOpenCitation} />}

      {answered && (
        <div className="flex flex-wrap items-center gap-x-da-xl gap-y-da-sm">
          <span data-testid="trust-label" title={trust.meaning} className="inline-flex items-center gap-da-sm">
            <StatusDot tone={TRUST_DOT[trust.tone]}>{trust.label}</StatusDot>
            {trust.breakdown && <span className="text-muted-foreground da-caption">({trust.breakdown})</span>}
          </span>
          {reported && <Badge tone="warning">Đang kiểm tra</Badge>}
          {(msg.latencyMs || showTimestamp) && (
            <span className="text-muted-foreground da-caption">
              {[msg.latencyMs ? formatDuration(msg.latencyMs) : null, showTimestamp ? formatTime(msg.timestamp) : null].filter(Boolean).join(' · ')}
            </span>
          )}
          <span className="flex-1" />
          {onOpenReceipt && (
            <button type="button" className={LINK} onClick={onOpenReceipt}>Biên nhận</button>
          )}
          {onOpenEvidence && displayEvidence && hasEvidence(displayEvidence) && (
            <button type="button" className={LINK} onClick={onOpenEvidence}>Cách tính</button>
          )}
          {exportable && (
            <button type="button" className={LINK} onClick={() => setExportOpen(true)}>Xuất</button>
          )}
          {msg.content && <CopyLink text={msg.content} />}
          {onSave && (
            <button type="button" className={LINK} onClick={onSave} title="Lưu câu hỏi để hỏi lại nhanh">Lưu câu hỏi</button>
          )}
          {onSaveKnowledge && (
            <button type="button" className={LINK} onClick={onSaveKnowledge}>Lưu thành tri thức</button>
          )}
          {onSaveToSession && (
            <button
              type="button"
              className={LINK}
              onClick={onSaveToSession}
              disabled={savedToSession}
            >
              {savedToSession ? 'Đã thêm vào phiên' : 'Thêm vào phiên'}
            </button>
          )}
          {onReport && (
            <button type="button" className={LINK} onClick={onReport} disabled={reported}>Báo sai</button>
          )}
        </div>
      )}

      {!streaming && !!msg.suggestions?.length && onSuggestion && (
        <div className="flex flex-wrap gap-da-md">
          {msg.suggestions.map((s, i) => (
            <SuggestionChip key={i} label={s} showIcon={false} onSelect={() => { if (!busy) onSuggestion(s); }} />
          ))}
        </div>
      )}

      {exportable && (
        <ExportDialog
          open={exportOpen}
          run={run}
          onClose={() => setExportOpen(false)}
          onExportCsv={run.rows.length ? () => exportCsv(run) : undefined}
          onExportXlsx={msg.answerId ? () => downloadAnswerXlsx(msg.answerId!) : undefined}
        />
      )}
    </div>
  );
}
