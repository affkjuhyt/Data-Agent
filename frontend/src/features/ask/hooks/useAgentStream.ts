/**
 * Conversation state for the Ask screen: sends a question over the
 * orchestrator SSE stream, folds every frame into the assistant message,
 * and knows how to answer a clarification — either by resuming an
 * interrupted orchestration or by sending the choice as the next turn.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { openOrchestrateStream, resumeClarification } from '../api';
import { readSseStream, type SseEvent } from '../sse';
import type { AskMessage, Clarification } from '../types';

interface UseAgentStreamOptions {
  userId: string;
  /** undefined / null → orchestrator picks agents (multi-agent) */
  agentId?: string | null;
  /** how many prior turns to send as context */
  historyTurns?: number;
}

const nowIso = () => new Date().toISOString();
let seq = 0;
const nextId = () => `${Date.now().toString(36)}-${(seq++).toString(36)}`;

export function useAgentStream({ userId, agentId, historyTurns = 10 }: UseAgentStreamOptions) {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [sending, setSending] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  useEffect(() => () => { mounted.current = false; abortRef.current?.abort(); }, []);

  const patch = useCallback((id: string, fn: (m: AskMessage) => AskMessage) => {
    setMessages(prev => prev.map(m => (m.id === id ? fn(m) : m)));
  }, []);

  const send = useCallback(async (text: string) => {
    const q = text.trim();
    if (!q || sending) return;

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
            questions: e.questions.map(text => ({ text })),
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
              next.clarification = { kind: 'followup', questions: e.evidence.clarifications };
            }
            return next;
          });
          break;
        case 'options':
          options = e.options;
          break;
        case 'chart':
          patch(assistantId, m => ({ ...m, chartSpecs: e.specs }));
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
      const res = await openOrchestrateStream(
        { query: q, agent_id: agentId || undefined, user_id: userId, messages: history },
        controller.signal,
      );
      await readSseStream(res.body!, onEvent);

      if (!mounted.current) return;
      patch(assistantId, m => {
        const next: AskMessage = { ...m, latencyMs: Date.now() - started, status: m.status === 'error' ? 'error' : 'done' };
        // A responder <OPTIONS> block without structured clarifications:
        // still a question the user should be able to answer in one click.
        if (options && !next.clarification) {
          next.clarification = { kind: 'followup', questions: [{ text: '', options }] };
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
        content: m.content || (aborted ? 'Stopped.' : 'Error: could not connect to the backend.'),
      }));
      if (!aborted) console.warn('ask | stream failed', err);
    } finally {
      if (mounted.current) setSending(false);
      abortRef.current = null;
    }
  }, [agentId, historyTurns, messages, patch, sending, userId]);

  /** Answer the clarification attached to message `id`. */
  const answerClarification = useCallback(async (id: string, answer: string) => {
    const target = messages.find(m => m.id === id);
    const clar = target?.clarification;
    if (!clar || sending) return;

    if (clar.kind === 'followup') {
      patch(id, m => ({ ...m, clarification: undefined }));
      await send(answer);
      return;
    }

    // resume: the interrupted graph continues with the user's words and the
    // whole answer comes back in one JSON body.
    setSending(true);
    const started = Date.now();
    const echo: AskMessage = { id: nextId(), role: 'user', content: answer, timestamp: nowIso(), status: 'done' };
    const answerId = nextId();
    setMessages(prev => [
      ...prev.map(m => (m.id === id ? { ...m, clarification: undefined } : m)),
      echo,
      { id: answerId, role: 'assistant', content: '', timestamp: nowIso(), status: 'streaming', sessionId: clar.sessionId },
    ]);
    try {
      const res = await resumeClarification(clar.sessionId, answer);
      if (!mounted.current) return;
      patch(answerId, m => ({
        ...m,
        content: res.response || (res.success ? '' : 'Could not process this request.'),
        suggestions: res.suggestions ?? [],
        status: res.success ? 'done' : 'error',
        latencyMs: Date.now() - started,
        clarification: res.needs_clarification && res.clarification_questions?.length
          ? { kind: 'resume', sessionId: clar.sessionId, questions: res.clarification_questions.map(text => ({ text })) }
          : undefined,
      }));
    } catch (err) {
      if (!mounted.current) return;
      patch(answerId, m => ({ ...m, status: 'error', content: `Error resuming: ${(err as Error).message}` }));
    } finally {
      if (mounted.current) setSending(false);
    }
  }, [messages, patch, send, sending]);

  const stop = useCallback(() => abortRef.current?.abort(), []);
  const reset = useCallback(() => { abortRef.current?.abort(); setMessages([]); setSending(false); }, []);

  return { messages, sending, send, answerClarification, stop, reset };
}
