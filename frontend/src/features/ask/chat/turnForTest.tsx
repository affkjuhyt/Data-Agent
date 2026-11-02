/** Test-only: the old AnswerBlock props mapped onto AssistantTurn, so the scenarios its tests
 *  pinned (trust label, citations, refusal card, actions) keep running against the new turn. */
import { vi } from 'vitest';
import type { AskMessage, Citation } from '../types';
import { AssistantTurn } from './AssistantTurn';

export function TurnForTest({
  msg,
  onOpenCitation,
  onSuggestion,
  onRetry,
  onReport,
  onSaveKnowledge,
  onOpenReceipt,
  reported,
}: {
  msg: AskMessage;
  question?: string;
  onOpenCitation?: (c: Citation) => void;
  onSuggestion?: (q: string) => void;
  onRetry?: () => void;
  onReport?: () => void;
  onSaveKnowledge?: () => void;
  onOpenReceipt?: () => void;
  reported?: boolean;
}) {
  const blocks = msg.blocks ?? [
    ...(msg.content ? [{ type: 'markdown', id: `${msg.id}-m`, content: msg.content }] : []),
    ...(msg.citations?.length ? [{ type: 'citations', id: `${msg.id}-c`, items: msg.citations }] : []),
  ];
  return (
    <AssistantTurn
      message={{ ...msg, blocks, answerId: msg.answerId ?? (onReport || onOpenReceipt || onSaveKnowledge ? 'answer-1' : undefined) }}
      busy={false}
      onRetry={vi.fn()}
      onReport={() => onReport?.()}
      onReceipt={() => onOpenReceipt?.()}
      onSaveKnowledge={onSaveKnowledge ? () => onSaveKnowledge() : undefined}
      onAsk={(q) => onSuggestion?.(q)}
      onAnswerClarification={vi.fn()}
      onRetryRefusal={onRetry}
      onOpenCitation={onOpenCitation}
      onExportCsv={vi.fn()}
      reported={reported}
    />
  );
}
