/**
 * One assistant answer, in the fixed order a reader scans:
 *   1 text (with citation markers) → 2 clarification card (if asking)
 *   → 3 data table → 4 charts → 5 "why trust this" strip → 6 sources
 *   → 7 actions (export, copy) → 8 follow-up chips.
 * Used by the Ask screen (light) and the Playground (dark) through AnswerTheme.
 */
import { lazy, Suspense, useMemo } from 'react';
import { Bookmark, Download, Sparkles } from 'lucide-react';
import { formatTime } from '../../../lib/formatVN';
import { downloadResultXlsx, fetchResultRows } from '../api';
import { csvFileName, downloadCsv, toCsv } from '../exportCsv';
import type { AskMessage, ClarificationReply } from '../types';
import { resultSetToEvidenceRun } from '../resultSets';
import { CitationPanel } from './CitationPanel';
import { ClarificationCard } from './ClarificationCard';
import { DataGrid } from './DataGrid';
import { EvidenceStrip, CopyButton } from './EvidenceStrip';
import { hasEvidence, primaryRun } from './evidenceSummary';
import { MarkdownContent } from './MarkdownContent';

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
  onOpenEvidence?: () => void;
  /** hide the inline strip when a side panel already shows the details */
  showEvidenceStrip?: boolean;
  showTimestamp?: boolean;
}

function TypingDots() {
  return (
    <span data-testid="typing" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, padding: '4px 0' }}>
      {[0, 1, 2].map(i => (
        <span key={i} className="pulse-soft" style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--ab-muted)', display: 'inline-block', animationDelay: `${i * 0.18}s` }} />
      ))}
    </span>
  );
}

export function AnswerBlock({
  msg, question, busy, onSuggestion, onAnswerClarification, onSave, onOpenEvidence,
  showEvidenceStrip = true, showTimestamp = true,
}: AnswerBlockProps) {
  const resultRuns = useMemo(() => (msg.resultSets ?? []).map(resultSetToEvidenceRun), [msg.resultSets]);
  const run = resultRuns[0] ?? primaryRun(msg.evidence);
  const displayedRuns = resultRuns.length ? resultRuns : run ? [run] : [];
  const displayEvidence = useMemo(() => resultRuns.length
    ? { runs: resultRuns, clarifications: msg.evidence?.clarifications ?? [], warnings: msg.evidence?.warnings ?? [] }
    : msg.evidence, [msg.evidence, resultRuns]);
  const streaming = msg.status === 'streaming';

  const exportCsv = (target = run) => {
    if (!target || !target.rows.length) return;
    downloadCsv(csvFileName(question ?? msg.content.slice(0, 40)), toCsv(target.columns, target.rows));
  };

  return (
    <div data-testid="answer-block" style={{ display: 'flex', flexDirection: 'column' }}>
      {msg.content ? <MarkdownContent text={msg.content} citations={msg.citations} /> : streaming ? <TypingDots /> : null}

      {msg.clarification && onAnswerClarification && (
        <ClarificationCard clarification={msg.clarification} disabled={busy} onAnswer={onAnswerClarification} />
      )}

      {displayedRuns.map((displayRun, index) => {
        const displayResultId = displayRun.export?.result_id;
        const displayFullResult = displayResultId && displayRun.export?.full_available
          ? {
              totalRows: displayRun.export.total_rows,
              loadRows: (params: { offset: number; limit: number; sort?: string; dir?: 'asc' | 'desc' }) => fetchResultRows(displayResultId, params),
              onExportXlsx: displayRun.export.xlsx_available
                ? async () => { await downloadResultXlsx(displayResultId, displayRun.export?.file_name); }
                : undefined,
            }
          : undefined;
        return (
          <DataGrid
            key={displayRun.id ?? index}
            columns={displayRun.columns}
            columnUnits={displayRun.column_units}
            rows={displayRun.rows}
            rowCount={displayRun.row_count}
            capped={displayRun.rows_capped}
            truncation={displayRun.truncation}
            title={displayedRuns.length > 1 ? `Query result ${index + 1}` : 'Data table'}
            onExport={displayRun.rows.length ? () => exportCsv(displayRun) : undefined}
            fullResult={displayFullResult}
          />
        );
      })}

      {!!msg.chartSpecs?.length && (
        <Suspense fallback={<div className="ab-card" style={{ minHeight: 180 }} />}>
          {msg.chartSpecs.map((spec, i) => <ChartRenderer key={i} spec={spec} />)}
        </Suspense>
      )}

      {showEvidenceStrip && displayEvidence && hasEvidence(displayEvidence) && (
        <EvidenceStrip evidence={displayEvidence} onOpenPanel={onOpenEvidence} />
      )}

      {msg.citations && msg.citations.length > 0 && <CitationPanel citations={msg.citations} />}

      {!streaming && msg.content && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          {run && run.rows.length > 0 && (
            <button type="button" className="ab-btn" onClick={() => exportCsv()} title="Download the data table (CSV, opens in Excel)">
              <Download size={11} /> Export CSV
            </button>
          )}
          {run?.sql && <CopyButton text={run.sql} label="Copy SQL" />}
          <CopyButton text={msg.content} label="Copy answer" />
          {onSave && (
            <button type="button" className="ab-btn" onClick={onSave} title="Save this question to ask again quickly">
              <Bookmark size={11} /> Save question
            </button>
          )}
          {showTimestamp && (
            <span style={{ marginLeft: 'auto', fontSize: 10, color: 'var(--ab-muted)' }}>{formatTime(msg.timestamp)}</span>
          )}
        </div>
      )}

      {!streaming && !!msg.suggestions?.length && onSuggestion && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
          {msg.suggestions.map((s, i) => (
            <button key={i} type="button" className="ab-chip-btn" onClick={() => onSuggestion(s)} disabled={busy}>
              <Sparkles size={9} style={{ verticalAlign: '-1px', marginRight: 4 }} />{s}
            </button>
          ))}
        </div>
      )}

    </div>
  );
}
