/**
 * Conversation state for the Ask screen: sends a question over the
 * orchestrator SSE stream, folds every frame into the assistant message,
 * and knows how to answer a clarification — either by resuming an
 * interrupted orchestration or by sending the choice as the next turn.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { openOrchestrateStream, openResumeStream } from '../api';
import { readSseStream, type SseEvent } from '../sse';
import type { AskMessage, Clarification, ClarificationAnswer, ClarificationReply } from '../types';

interface UseAgentStreamOptions {
  userId: string;
  /** undefined / null → orchestrator picks agents (multi-agent) */
  agentId?: string | null;
  /** how many prior turns to send as context */
  historyTurns?: number;
}

interface SendOptions {
  /** what the backend works on, when it differs from the text shown in the bubble */
  query?: string;
  /** options picked on the previous turn's clarification card */
  answers?: ClarificationAnswer[];
  /** answer an interrupted orchestration (the text is the answer) instead of asking anew */
  resumeSessionId?: string;
}

const nowIso = () => new Date().toISOString();
let seq = 0;
const nextId = () => `${Date.now().toString(36)}-${(seq++).toString(36)}`;

/** The question a clarification answer is sent as: the question that was asked
 *  about, plus the answer. Mirrors backend input_refine.merge_clarification. */
export const withClarificationAnswer = (query: string, answer: string) => `${query}\n\nThông tin bổ sung: ${answer}`;

export function useAgentStream({ userId, agentId, historyTurns = 10 }: UseAgentStreamOptions) {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  // Single-agent mode: the agent's own conversation, continued turn to turn.
  // The previous turn's slots and its "I just asked" marker live there; a new
  // thread per turn made every answer to a clarification look like turn one.
  const agentConvRef = useRef<string | null>(null);

  useEffect(() => () => { mounted.current = false; abortRef.current?.abort(); }, []);
  // Another agent (or auto routing) cannot continue this agent's thread.
  useEffect(() => { agentConvRef.current = null; }, [agentId]);

  const patch = useCallback((id: string, fn: (m: AskMessage) => AskMessage) => {
    setMessages(prev => prev.map(m => (m.id === id ? fn(m) : m)));
  }, []);

  const send = useCallback(async (text: string, opts: SendOptions = {}) => {
    const q = text.trim();
    if (!q || sending) return;
    const query = opts.query?.trim() || q;

    const userMsg: AskMessage = { id: nextId(), role: 'user', content: q, timestamp: nowIso(), status: 'done' };
    const assistantId = nextId();
    const placeholder: AskMessage = { id: assistantId, role: 'assistant', content: '', timestamp: nowIso(), status: 'streaming' };

    // History from state at call time — the closure over `messages` is fine
    // here because send() is not called while sending.
    const history = messages
      .filter(m => m.status !== 'error' && m.content)
      .slice(-historyTurns)
      .map(m => ({ role: m.role, content: m.content }));

    setMessages(prev => [...prev, userMsg, placeholder]);
    setSending(true);
    const started = Date.now();
    const controller = new AbortController();
    abortRef.current = controller;

    let buffer = '';
    let sessionId: string | undefined;
    let options: string[] | null = null;
    let interrupted = false;

    const onEvent = (e: SseEvent) => {
      switch (e.type) {
        case 'token':
          buffer += e.text;
          patch(assistantId, m => ({ ...m, content: buffer }));
          break;
        case 'reset':
          // The streamed answer was rejected; its replacement follows.
          buffer = '';
          patch(assistantId, m => ({ ...m, content: '' }));
          break;
        case 'session':
          sessionId = e.sessionId;
          patch(assistantId, m => ({ ...m, sessionId: e.sessionId }));
          break;
        case 'clarification': {
          // Orchestrator graph interrupted: the answer must go to /resume.
          interrupted = true;
          const clar: Clarification = {
            kind: 'resume',
            sessionId: e.sessionId || sessionId || '',
            questions: e.details,
          };
          patch(assistantId, m => ({ ...m, clarification: clar }));
          break;
        }
        case 'evidence':
          patch(assistantId, m => {
            const next: AskMessage = { ...m, evidence: e.evidence };
            // Deterministic questions from the runtime: render them as a card
            // whose answer is simply the next turn (slots merge server-side).
            if (e.evidence.clarifications.length && !m.clarification) {
              next.clarification = { kind: 'followup', questions: e.evidence.clarifications, query };
            }
            return next;
          });
          break;
        case 'options':
          options = e.options;
          break;
        case 'conv_ids':
          if (agentId && e.convs[0]?.conv_id) agentConvRef.current = e.convs[0].conv_id;
          break;
        case 'chart':
          patch(assistantId, m => ({ ...m, chartSpecs: e.specs }));
          break;
        case 'result_set':
          patch(assistantId, m => ({
            ...m,
            resultSets: [...(m.resultSets ?? []).filter(r => r.result_id !== e.resultSet.result_id), e.resultSet],
          }));
          break;
        case 'citations':
          patch(assistantId, m => ({ ...m, citations: e.citations }));
          break;
        case 'suggestions':
          patch(assistantId, m => ({ ...m, suggestions: e.suggestions }));
          break;
        case 'pending_review':
          patch(assistantId, m => ({ ...m, pendingReview: { convId: e.convId, payload: e.payload as never } }));
          break;
        case 'error':
          patch(assistantId, m => ({ ...m, content: m.content || e.message, status: 'error' }));
          break;
        default:
          break;
      }
    };

    try {
      const res = opts.resumeSessionId
        ? await openResumeStream(opts.resumeSessionId, query, controller.signal)
        : await openOrchestrateStream(
          {
            query,
            agent_id: agentId || undefined,
            user_id: userId,
            messages: history,
            conversation_id: (agentId && agentConvRef.current) || undefined,
            clarification_answers: opts.answers?.length ? opts.answers : undefined,
          },
          controller.signal,
        );
      await readSseStream(res.body!, onEvent);

      if (!mounted.current) return;
      patch(assistantId, m => {
        const next: AskMessage = { ...m, latencyMs: Date.now() - started, status: m.status === 'error' ? 'error' : 'done' };
        // A responder <OPTIONS> block without structured clarifications:
        // still a question the user should be able to answer in one click.
        if (options && !next.clarification) {
          next.clarification = { kind: 'followup', questions: [{ text: '', options }], query };
        }
        if (interrupted && !next.content) next.content = next.clarification?.questions.map(x => x.text).filter(Boolean).join('\n') || '';
        return next;
      });
    } catch (err) {
      if (!mounted.current) return;
      const aborted = controller.signal.aborted;
      patch(assistantId, m => ({
        ...m,
        status: 'error',
        content: m.content || (aborted
          ? 'Stopped.'
          : opts.resumeSessionId ? `Error resuming: ${(err as Error).message}` : 'Error: could not connect to the backend.'),
      }));
      if (!aborted) console.warn('ask | stream failed', err);
    } finally {
      if (mounted.current) setSending(false);
      abortRef.current = null;
    }
  }, [agentId, historyTurns, messages, patch, sending, userId]);

  /** Answer the clarification attached to message `id`. */
  const answerClarification = useCallback(async (id: string, reply: ClarificationReply) => {
    const target = messages.find(m => m.id === id);
    const clar = target?.clarification;
    if (!clar || sending || !reply.text.trim()) return;

    if (clar.kind === 'followup') {
      patch(id, m => ({ ...m, clarification: undefined }));
      // The bubble shows the answer; the backend gets the question it answers
      // plus the answer, so the turn stands on its own even if the thread
      // could not be continued. The picks themselves go as structured answers.
      await send(reply.text, {
        query: clar.query ? withClarificationAnswer(clar.query, reply.text) : undefined,
        answers: reply.answers,
      });
      return;
    }

    // resume: the interrupted graph continues with the user's words, streamed
    // like any turn — so a question it asks next comes back as a card. The
    // card goes; what it asked stays as the message text.
    const asked = clar.questions.map(q => q.text).filter(Boolean).join('\n');
    patch(id, m => ({ ...m, clarification: undefined, content: m.content || asked }));
    await send(reply.text, { resumeSessionId: clar.sessionId });
  }, [messages, patch, send, sending]);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => {
    abortRef.current?.abort();
    agentConvRef.current = null;
    setMessages([]);
    setSending(false);
  }, []);

  return { messages, sending, send, answerClarification, stop, reset };
}
