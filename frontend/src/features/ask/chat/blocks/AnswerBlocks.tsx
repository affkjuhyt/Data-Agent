import type { ReactNode } from 'react';
import { responseOrder, type ResponseSection } from '../../answerLayout';
import { CitationPanel } from '../../components/CitationPanel';
import { MarkdownContent } from '../../components/MarkdownContent';
import type { AnswerBlockView, AskMessage, Citation } from '../../types';
import { CaveatsBlock } from './CaveatsBlock';
import { MetricCards, type MetricBlock } from './MetricCards';
import { ResultCard } from './ResultCard';
import { SqlBlock } from './SqlBlock';

/*
 * An answer drawn block by block, in the order the server gives (GET /v1/answers/{id} blocks[]),
 * reordered only when a "/" command asked for one part first (answerLayout). The metric blocks of
 * a turn are one row of cards; its chart and table are one result card. The chart and table data
 * come from the answer as fromAnswer read it (chartSpecs, evidence), which the server block only
 * names.
 */

const SECTION: Record<string, ResponseSection> = {
  markdown: 'content',
  metric: 'content',
  chart: 'chart',
  table: 'table',
  sql: 'evidence',
  citations: 'citations',
  caveats: 'verification',
};

// More figures than this is a breakdown: the result card's table shows it, not a row of cards.
const MAX_CARDS = 4;

const str = (v: unknown) => (typeof v === 'string' ? v : '');

export function AnswerBlocks({
  message,
  onOpenCitation,
  onExportCsv,
  onSaveToSession,
  onPick,
}: {
  message: AskMessage;
  onOpenCitation?: (citation: Citation) => void;
  onExportCsv?: () => void;
  onSaveToSession?: () => void;
  onPick?: (category: string) => void;
}) {
  const blocks: AnswerBlockView[] = message.blocks ?? [];
  const layout = message.layout ?? 'auto';
  const ordered = [...blocks].sort(
    (a, b) => responseOrder(layout, SECTION[str(a.type)] ?? 'meta') - responseOrder(layout, SECTION[str(b.type)] ?? 'meta'),
  );
  const metrics = blocks.filter((b) => b.type === 'metric') as unknown as MetricBlock[];
  const queryRunId = str(blocks.find((b) => b.type === 'sql')?.query_run_id) || null;
  const run = message.evidence?.runs[0];
  const chart = message.chartSpecs?.[0] ?? null;

  const drawn = new Set<string>();
  const parts: { key: string; type: string; node: ReactNode }[] = [];
  for (const block of ordered) {
    const type = str(block.type);
    if (type === 'metric' || type === 'chart' || type === 'table') {
      // One row of cards for the metrics, one card for chart and table, wherever the first lands.
      const group = type === 'metric' ? 'metric' : 'result';
      if (drawn.has(group)) continue;
      drawn.add(group);
      if (group === 'metric') {
        if (metrics.length === 0 || metrics.length > MAX_CARDS) continue;
        parts.push({ key: 'metrics', type: 'metric', node: <MetricCards metrics={metrics} /> });
      } else {
        const tableBlock = blocks.find((b) => b.type === 'table');
        parts.push({
          key: 'result',
          type: 'result',
          node: (
            <ResultCard
              title={chart?.title || str(tableBlock?.title) || 'Kết quả'}
              chart={chart}
              columns={run?.columns ?? (Array.isArray(tableBlock?.columns) ? (tableBlock.columns as string[]) : [])}
              rows={run?.rows ?? (Array.isArray(tableBlock?.rows) ? (tableBlock.rows as Record<string, unknown>[]) : [])}
              rowCount={run?.row_count ?? (typeof tableBlock?.row_count === 'number' ? tableBlock.row_count : null)}
              reading={message.reading ?? []}
              queryRunId={queryRunId}
              onExportCsv={onExportCsv}
              onSaveToSession={onSaveToSession}
              onPick={onPick}
            />
          ),
        });
      }
      continue;
    }
    if (type === 'markdown') {
      parts.push({
        key: str(block.id) || 'markdown',
        type,
        node: <MarkdownContent text={str(block.content)} citations={message.citations} onOpenCitation={onOpenCitation} />,
      });
    } else if (type === 'sql' && queryRunId) {
      parts.push({ key: 'sql', type, node: <SqlBlock queryRunId={queryRunId} /> });
    } else if (type === 'citations' && message.citations?.length) {
      parts.push({ key: 'citations', type, node: <CitationPanel citations={message.citations} onOpen={onOpenCitation} /> });
    } else if (type === 'caveats' && Array.isArray(block.items) && block.items.length > 0) {
      parts.push({ key: 'caveats', type, node: <CaveatsBlock items={(block.items as unknown[]).map(String)} /> });
    }
  }

  return (
    <div className="flex flex-col gap-da-lg">
      {parts.map((part) => (
        <div key={part.key} data-testid="answer-block" data-type={part.type}>
          {part.node}
        </div>
      ))}
    </div>
  );
}
