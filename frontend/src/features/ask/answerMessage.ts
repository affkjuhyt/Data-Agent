/**
 * Turn a stored AnswerView into the AskMessage shape the evidence panel and chips draw.
 * Shared by Ask hydrate and Notebook tiles saved from an answer.
 */
import { getAnswer, type Answer } from '../../lib/v1/questions';
import {
  figureChart,
  figureDrill,
  figureRun,
  figureTrust,
  readChartSpec,
  readFigures,
} from './dataAnswer';
import { interpretationChips } from './interpretation';
import type { AskMessage, Citation, Refusal, VerificationFrame } from './types';

/** One entry of qa.answer.citations as worker-agent writes it. */
interface AnswerCitationWire {
  n: number;
  document_id: string;
  document_title: string | null;
  doc_no: string | null;
  section_path: string | null;
  page_from: number;
  superseded_by: string | null;
  quote: string;
  score?: number | null;
}

export function citationsOf(answer: Pick<Answer, 'citations'>): Citation[] {
  const wire = Array.isArray(answer.citations) ? (answer.citations as AnswerCitationWire[]) : [];
  return wire.map((c) => ({
    index: c.n,
    source: [
      c.document_title ?? c.doc_no ?? 'Tài liệu',
      c.section_path,
      `trang ${c.page_from}`,
      c.superseded_by ? `đã được thay thế bởi ${c.superseded_by}` : null,
    ]
      .filter(Boolean)
      .join(' · '),
    type: 'document',
    snippet: c.quote,
    doc_id: c.document_id,
    page: c.page_from,
    score: c.score ?? null,
  }));
}

/** The deterministic checks the worker wrote with the answer (number_check.verification, R0-8).
 *  Anything not of that shape is left out rather than drawn as a verdict. */
export function readVerification(numberCheck: unknown): VerificationFrame | undefined {
  if (!numberCheck || typeof numberCheck !== 'object') return undefined;
  const raw = (numberCheck as { verification?: unknown }).verification;
  if (!raw || typeof raw !== 'object') return undefined;
  const { checks, verdict, iteration, confidence, consensus } = raw as Record<string, unknown>;
  if ((verdict !== 'PASS' && verdict !== 'FAIL') || !Array.isArray(checks)) return undefined;
  const vote = consensus && typeof consensus === 'object' ? (consensus as Record<string, unknown>) : {};
  return {
    verdict,
    iteration: typeof iteration === 'number' ? iteration : 1,
    // Only a query the model wrote has one: how many of its ways of writing agree (R1-14).
    confidence: typeof confidence === 'number' ? confidence : null,
    ...(typeof vote.agreeing === 'number' &&
      typeof vote.written === 'number' && {
        consensus: { agreeing: vote.agreeing, written: vote.written },
      }),
    checks: checks.flatMap((check) => {
      if (!check || typeof check !== 'object') return [];
      const { name, passed, detail } = check as Record<string, unknown>;
      if (typeof name !== 'string' || typeof passed !== 'boolean') return [];
      return [{ name, passed, detail: typeof detail === 'string' ? detail : '' }];
    }),
  };
}

const NEXT_ACTIONS = new Set(['retry', 'rephrase', 'contact_steward']);

/** The code and what the person can do about it, as the worker keeps them with a refusal
 *  (number_check.refusal, R0-7), so the card offers retry or rephrase on reload too. An answer
 *  from before that, or anything not of that shape, is the server's message alone. */
export function readRefusal(answer: Pick<Answer, 'text' | 'number_check'>): Refusal {
  const check = answer.number_check;
  const kept = check && typeof check === 'object' ? (check as { refusal?: unknown }).refusal : undefined;
  const { code, next_action } = (kept && typeof kept === 'object' ? kept : {}) as Record<string, unknown>;
  return {
    code: typeof code === 'string' && code ? code : 'REFUSED',
    message: answer.text,
    next_action: typeof next_action === 'string' && NEXT_ACTIONS.has(next_action) ? next_action : '',
  };
}

/** The answer as the Ask screen draws it. */
export function fromAnswer(answer: Answer): Partial<AskMessage> {
  if (answer.kind === 'refusal') {
    return { content: '', refusal: readRefusal(answer) };
  }
  const citations = citationsOf(answer);
  const verification = readVerification(answer.number_check);
  const drawn: Partial<AskMessage> = {
    content: answer.text,
    productScope: answer.product_scope,
    ...(citations.length > 0 && { citations }),
    ...(verification && { verification }),
  };
  if (answer.kind !== 'data') return drawn;
  const figures = readFigures(answer.figures);
  const spec = readChartSpec(answer.chart_spec);
  const run = figureRun(figures, spec);
  const chart = figureChart(figures, spec);
  const trust = figureTrust(figures);
  const drill = figureDrill(spec);
  return {
    ...drawn,
    ...(drill && { drill }),
    ...(run && { evidence: { runs: [run], clarifications: [], warnings: [] } }),
    ...(chart && { chartSpecs: [chart] }),
    ...(trust && { trust }),
  };
}

/** "Biên nhận" (UC-01-07): an answer with a server id and words, unless it is outside every data
 *  product, which has no commitment behind it and so no receipt (AC-01-07-03). */
export const hasReceipt = (msg: Pick<AskMessage, 'answerId' | 'content' | 'productScope'>) =>
  !!(msg.answerId && msg.content) && msg.productScope !== 'out_of_scope';

/** Load one answer for Notebook "Cách tính" / interpretation chips. */
export async function loadAnswerMessage(answerId: string): Promise<AskMessage> {
  const answer = await getAnswer(answerId);
  const drawn = fromAnswer(answer);
  const run = drawn.evidence?.runs[0];
  const reading = drawn.reading ?? interpretationChips(run);
  return {
    id: `a-${answerId}`,
    role: 'assistant',
    content: drawn.content ?? answer.text ?? '',
    timestamp: answer.created_at,
    status: 'done',
    answerId,
    ...drawn,
    ...(reading.length ? { reading } : {}),
  };
}
