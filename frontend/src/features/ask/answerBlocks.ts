import { responseOrder, type AnswerLayout, type ResponseSection } from './answerLayout';
import type { AskMessage, ChartSpec, Citation, EvidenceRun } from './types';

export type AnswerBlock =
  | { type: 'markdown'; id: string; content: string; citations?: Citation[] }
  | { type: 'chart'; id: string; spec: ChartSpec }
  | { type: 'table'; id: string; run: EvidenceRun; title: string }
  | { type: 'sql'; id: string; sql: string }
  | { type: 'citations'; id: string; citations: Citation[] }
  | { type: 'suggestions'; id: string; items: string[] };

const sectionOf: Record<AnswerBlock['type'], ResponseSection> = {
  markdown: 'content', chart: 'chart', table: 'table', sql: 'evidence', citations: 'citations', suggestions: 'suggestions',
};

export const sectionForBlock = (block: AnswerBlock): ResponseSection => sectionOf[block.type];

export function blocksForMessage(msg: Pick<AskMessage, 'id' | 'content' | 'status' | 'chartSpecs' | 'evidence' | 'citations' | 'suggestions' | 'layout'>): AnswerBlock[] {
  const blocks: AnswerBlock[] = [];
  if (msg.content || msg.status === 'streaming') blocks.push({ type: 'markdown', id: `${msg.id}-markdown`, content: msg.content, citations: msg.citations });
  for (const [index, spec] of (msg.chartSpecs ?? []).entries()) {
    blocks.push({ type: 'chart', id: `${msg.id}-chart-${index}`, spec });
  }
  const runs = msg.evidence?.runs ?? [];
  for (const [index, run] of runs.entries()) {
    blocks.push({ type: 'table', id: `${msg.id}-table-${run.id ?? index}`, run, title: runs.length > 1 ? `Kết quả truy vấn ${index + 1}` : 'Bảng dữ liệu' });
    if (run.sql) blocks.push({ type: 'sql', id: `${msg.id}-sql-${run.id ?? index}`, sql: run.sql });
  }
  if (msg.citations?.length) blocks.push({ type: 'citations', id: `${msg.id}-citations`, citations: msg.citations });
  if (msg.suggestions?.length) blocks.push({ type: 'suggestions', id: `${msg.id}-suggestions`, items: msg.suggestions });
  const layout: AnswerLayout = msg.layout ?? 'auto';
  return [...blocks].sort((left, right) => responseOrder(layout, sectionOf[left.type]) - responseOrder(layout, sectionOf[right.type]));
}
