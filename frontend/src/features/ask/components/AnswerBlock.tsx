/**
 * One assistant answer, in the fixed order a reader scans:
 *   1 text (with citation markers) → 2 clarification card (if asking)
 *   → 3 data table → 4 charts → 5 "why trust this" strip → 6 sources
 *   → 7 actions (export, copy) → 8 follow-up chips.
 * Used by the Ask screen (light) and the Playground (dark) through AnswerTheme.
 */
import { Bookmark, Download, Sparkles } from 'lucide-react';
import { formatTime } from '../../../lib/formatVN';
import { csvFileName, downloadCsv, toCsv } from '../exportCsv';
import type { AskMessage } from '../types';
import { ChartRenderer } from './ChartRenderer';
import { CitationPanel } from './CitationPanel';
import { ClarificationCard } from './ClarificationCard';
import { DataGrid } from './DataGrid';
import { EvidenceStrip, CopyButton } from './EvidenceStrip';
import { hasEvidence, primaryRun } from './evidenceSummary';
import { MarkdownContent } from './MarkdownContent';

interface AnswerBlockProps {
  msg: AskMessage;
  /** the user question this answers — used for the export file name */
  question?: string;
  busy?: boolean;
  onSuggestion?: (text: string) => void;
  onAnswerClarification?: (answer: string) => void;
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
  const run = primaryRun(msg.evidence);
  const rows = run?.rows ?? [];
  const streaming = msg.status === 'streaming';

  const exportCsv = () => {
    if (!run || !rows.length) return;
    downloadCsv(csvFileName(question ?? msg.content.slice(0, 40)), toCsv(run.columns, rows));
  };

  return (
    <div data-testid="answer-block" style={{ display: 'flex', flexDirection: 'column' }}>
      {msg.content ? <MarkdownContent text={msg.content} citations={msg.citations} /> : streaming ? <TypingDots /> : null}

      {msg.clarification && onAnswerClarification && (
        <ClarificationCard clarification={msg.clarification} disabled={busy} onAnswer={onAnswerClarification} />
      )}

      {run && rows.length > 0 && (
        <DataGrid
          columns={run.columns}
          rows={rows}
          rowCount={run.row_count}
          capped={run.rows_capped}
          truncation={run.truncation}
          onExport={exportCsv}
        />
      )}

      {msg.chartSpecs?.map((spec, i) => <ChartRenderer key={i} spec={spec} />)}

      {showEvidenceStrip && msg.evidence && hasEvidence(msg.evidence) && (
        <EvidenceStrip evidence={msg.evidence} onOpenPanel={onOpenEvidence} />
      )}

      {msg.citations && msg.citations.length > 0 && <CitationPanel citations={msg.citations} />}

      {!streaming && msg.content && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
          {run && rows.length > 0 && (
            <button type="button" className="ab-btn" onClick={exportCsv} title="Tải bảng dữ liệu (CSV, mở bằng Excel)">
              <Download size={11} /> Xuất CSV
            </button>
          )}
          {run?.sql && <CopyButton text={run.sql} label="Sao chép SQL" />}
          <CopyButton text={msg.content} label="Sao chép câu trả lời" />
          {onSave && (
            <button type="button" className="ab-btn" onClick={onSave} title="Lưu câu hỏi này để hỏi lại nhanh">
              <Bookmark size={11} /> Lưu câu hỏi
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
