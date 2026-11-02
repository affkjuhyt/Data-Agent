/**
 * Conversation state for the Ask screen over the v1 API (UC-01-01, A1.6): a question is recorded
 * (POST /v1/questions, 202), worker-agent answers it, and the screen follows its stream — each
 * step as an `activity` event of kind "step" (steps.ts), then answer / clarification / error →
 * done. The server sends the state it has recorded and closes, so the hook polls the stream with
 * Last-Event-ID until `done`. A reopened conversation reads each ended turn's steps back
 * (GET /v1/questions/{id}/steps) and follows a turn still running from its first event.
 *
 * Each turn keeps its server ids (question_id, answer_id): Báo sai, Lưu tri thức and Biên nhận
 * act on the real answer. Questions of one screen share a conversation, so a follow-up inherits
 * the reading of the one before (UC-01-04).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { V1Error } from '../../../lib/v1/client';
import {
  askQuestion,
  cancelQuestion,
  getAnswer,
  getConversation,
  getQuestion,
  getQuestionSteps,
  questionEvents,
  type ConversationDetail,
} from '../../../lib/v1/questions';
import { fromAnswer } from '../answerMessage';
import type { AnswerLayout } from '../answerLayout';
import { readPlan } from '../dataAnswer';
import { applyStepEvent, stepsFromView, type TurnStep } from '../steps';
import type { AskMessage, ClarificationReply } from '../types';

const POLL_MS = 400;
// Reading a turn's steps back after `done`: a few short tries while the worker writes them.
const READ_BACK_MS = 300;
const READ_BACK_TRIES = 5;
// A turn the worker has not finished in this long is shown as failed; the question stays
// recorded and can be opened again from the conversation.
const GIVE_UP_MS = 5 * 60_000;

const nowIso = () => new Date().toISOString();
let seq = 0;
const nextId = () => `${Date.now().toString(36)}-${(seq++).toString(36)}`;
type PendingTurn = { text: string; layout: AnswerLayout };
const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

/** The steps of an ended turn; a turn from before steps were kept has none (C5). */
async function readSteps(questionId: string): Promise<{ steps: TurnStep[]; recorded: boolean; durationMs?: number }> {
  try {
    const view = await getQuestionSteps(questionId);
    const steps = stepsFromView(view);
    return { steps, recorded: steps.length > 0, durationMs: view.duration_ms ?? undefined };
  } catch {
    return { steps: [], recorded: false };
  }
}

/** A turn still running when the conversation is opened: followed from its first event. */
const STILL_RUNNING = new Set(['received', 'planning', 'running']);

async function messagesFromConversation(
  detail: ConversationDetail,
): Promise<{ messages: AskMessage[]; running: { assistantId: string; questionId: string }[] }> {
  const out: AskMessage[] = [];
  const running: { assistantId: string; questionId: string }[] = [];
  for (const turn of detail.turns) {
    out.push({
      id: `q-${turn.question_id}`,
      role: 'user',
      content: turn.text,
      timestamp: turn.received_at,
      status: 'done',
      questionId: turn.question_id,
    });
    const assistantId = `a-${turn.question_id}`;
    if (turn.answer_id) {
      try {
        const answer = await getAnswer(turn.answer_id);
        const drawn = fromAnswer(answer);
        let reading: AskMessage['reading'];
        let proposal: AskMessage['proposal'];
        let newQuestion: boolean | undefined;
        try {
          const q = await getQuestion(turn.question_id);
          const plan = readPlan(q.plan);
          reading = plan.reading.length ? plan.reading : undefined;
          proposal = plan.proposal ?? undefined;
          newQuestion = q.seq > 1 && !!reading?.length && !reading.some((c) => c.inherited);
        } catch {
          /* plan optional on reopen */
        }
        const trace = await readSteps(turn.question_id);
        out.push({
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: turn.answered_at ?? turn.received_at,
          status: 'done',
          questionId: turn.question_id,
          answerId: turn.answer_id,
          questionText: turn.text,
          startedAt: turn.received_at,
          steps: trace.steps,
          stepsRecorded: trace.recorded,
          ...(trace.durationMs !== undefined && { durationMs: trace.durationMs }),
          ...drawn,
          ...(reading && { reading }),
          ...(proposal && { proposal }),
          ...(newQuestion !== undefined && { newQuestion }),
        });
      } catch {
        out.push({
          id: assistantId,
          role: 'assistant',
          content: 'Không tải được câu trả lời đã lưu.',
          timestamp: turn.answered_at ?? turn.received_at,
          status: 'error',
          questionId: turn.question_id,
        });
      }
    } else if (STILL_RUNNING.has(turn.status)) {
      // Its steps are not kept until it ends: the stream is replayed from its first event.
      out.push({
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: turn.received_at,
        status: 'streaming',
        questionId: turn.question_id,
        questionText: turn.text,
        startedAt: turn.received_at,
        steps: [],
        stepsRecorded: true,
      });
      running.push({ assistantId, questionId: turn.question_id });
    } else {
      const trace = await readSteps(turn.question_id);
      out.push({
        id: assistantId,
        role: 'assistant',
        content: 'Agent không trả lời được câu hỏi này.',
        timestamp: turn.received_at,
        status: 'error',
        questionId: turn.question_id,
        questionText: turn.text,
        steps: trace.steps,
        stepsRecorded: trace.recorded,
      });
    }
  }
  return { messages: out, running };
}

export function useQuestionStream() {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [queued, setQueued] = useState<string[]>([]);
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const runningRef = useRef(false);
  const queueRef = useRef<PendingTurn[]>([]);
  const conversationRef = useRef<string | null>(null);
  const currentQuestionRef = useRef<string | null>(null);
  const mounted = useRef(true);

  useEffect(
    () => () => {
      mounted.current = false;
      abortRef.current?.abort();
    },
    [],
  );

  const messagesRef = useRef<AskMessage[]>([]);
  messagesRef.current = messages;

  const patch = useCallback((id: string, fn: (m: AskMessage) => AskMessage) => {
    if (mounted.current) setMessages((prev) => prev.map((m) => (m.id === id ? fn(m) : m)));
  }, []);

  /** Once a turn ends, its steps as kept: the model calls the stream does not send included. The
   *  worker writes them just after `done`, so the first reads may find no run yet. */
  const readBack = useCallback(
    async (assistantId: string, questionId: string, signal: AbortSignal) => {
      for (let attempt = 0; attempt < READ_BACK_TRIES; attempt++) {
        try {
          await sleep(READ_BACK_MS, signal);
          const view = await getQuestionSteps(questionId);
          if (view.run_id && view.steps?.length) {
            const steps = stepsFromView(view);
            patch(assistantId, (m) => ({ ...m, steps }));
            return;
          }
        } catch {
          return;
        }
      }
    },
    [patch],
  );

  const follow = useCallback(
    async (assistantId: string, questionId: string, signal: AbortSignal) => {
      const started = Date.now();
      let cancelled = false;
      let lastId: string | undefined;
      for (;;) {
        const events = await questionEvents(questionId, lastId, signal);
        for (const e of events) {
          lastId = e.id ?? lastId;
          const data = e.data ? (JSON.parse(e.data) as Record<string, unknown>) : {};
          if (e.event === 'activity') {
            patch(assistantId, (m) => ({ ...m, steps: applyStepEvent(m.steps ?? [], data) }));
          } else if (e.event === 'cancelled') {
            cancelled = true;
            patch(assistantId, (m) => ({
              ...m,
              status: 'error',
              content: 'Đã dừng lượt phân tích theo yêu cầu của bạn.',
            }));
          } else if (e.event === 'answer' && typeof data.answer_id === 'string') {
            if (cancelled) continue;
            const answer = await getAnswer(data.answer_id);
            const drawn = fromAnswer(answer);
            patch(assistantId, (m) => ({ ...m, ...drawn, answerId: answer.id }));
            // The plan: what was understood (inherited chips marked) and a step awaiting a yes.
            getQuestion(questionId)
              .then((q) => {
                const { reading, proposal } = readPlan(q.plan);
                const inherited = reading.some((c) => c.inherited);
                patch(assistantId, (m) => ({
                  ...m,
                  ...(reading.length && { reading }),
                  ...(proposal && { proposal }),
                  newQuestion: q.seq > 1 && reading.length > 0 && !inherited,
                }));
              })
              .catch(() => undefined);
          } else if (e.event === 'clarification' && typeof data.answer_id === 'string') {
            // Without this the turn closed on `done` with nothing in it: the question back is an
            // answer of kind "clarification", sent on its own event rather than `answer`.
            const answer = await getAnswer(data.answer_id);
            patch(assistantId, (m) => ({ ...m, ...fromAnswer(answer), answerId: answer.id }));
          } else if (e.event === 'error') {
            patch(assistantId, (m) => ({
              ...m,
              status: 'error',
              content: m.content || 'Agent không trả lời được câu hỏi này.',
            }));
          } else if (e.event === 'done') {
            void readBack(assistantId, questionId, signal);
            patch(assistantId, (m) => ({
              ...m,
              status: m.status === 'error' ? 'error' : 'done',
              latencyMs: Date.now() - started,
              durationMs: m.startedAt ? Date.now() - Date.parse(m.startedAt) : Date.now() - started,
            }));
            return;
          }
        }
        if (Date.now() - started > GIVE_UP_MS) {
          patch(assistantId, (m) => ({
            ...m,
            status: 'error',
            content: 'Agent chưa trả lời sau 5 phút; câu hỏi vẫn được lưu trong hội thoại.',
          }));
          return;
        }
        await sleep(POLL_MS, signal);
      }
    },
    [patch, readBack],
  );

  const runTurn = useCallback(
    async (text: string, layout: AnswerLayout = 'auto') => {
      const q = text.trim();
      if (!q) return;
      const assistantId = nextId();
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', content: q, timestamp: nowIso(), status: 'done' },
        {
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: nowIso(),
          status: 'streaming',
          layout,
          questionText: q,
          startedAt: nowIso(),
          steps: [],
          stepsRecorded: true,
        },
      ]);
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const accepted = await askQuestion(q, conversationRef.current);
        conversationRef.current = accepted.conversation_id;
        currentQuestionRef.current = accepted.question_id;
        if (mounted.current) setConversationId(accepted.conversation_id);
        patch(assistantId, (m) => ({ ...m, questionId: accepted.question_id }));
        await follow(assistantId, accepted.question_id, controller.signal);
      } catch (err) {
        const aborted = controller.signal.aborted;
        patch(assistantId, (m) => ({
          ...m,
          status: 'error',
          content:
            m.content ||
            (aborted
              ? 'Đã dừng theo dõi; câu hỏi vẫn được lưu trong hội thoại.'
              : err instanceof V1Error && err.code === 'data_space_required'
                ? 'Chọn một Data Space ở thanh trên trước khi hỏi.'
                : err instanceof V1Error
                  ? err.message
                  : 'Không kết nối được máy chủ.'),
        }));
      } finally {
        abortRef.current = null;
        currentQuestionRef.current = null;
      }
    },
    [follow, patch],
  );

  const drain = useCallback(
    async (first: PendingTurn) => {
      runningRef.current = true;
      try {
        await runTurn(first.text, first.layout);
        while (queueRef.current.length > 0 && mounted.current) {
          const next = queueRef.current.shift();
          setQueued(queueRef.current.map((turn) => turn.text));
          if (next) await runTurn(next.text, next.layout);
        }
      } finally {
        runningRef.current = false;
        if (mounted.current) setSending(false);
      }
    },
    [runTurn],
  );

  const send = useCallback(
    async (text: string, layout: AnswerLayout = 'auto') => {
      const q = text.trim();
      if (!q) return;
      if (runningRef.current) {
        queueRef.current.push({ text: q, layout });
        setQueued(queueRef.current.map((turn) => turn.text));
        return;
      }
      await drain({ text: q, layout });
    },
    [drain],
  );

  /** A clarification is answered by asking again in the same conversation (UC-01-04). */
  const answerClarification = useCallback(
    async (id: string, reply: ClarificationReply) => {
      if (!reply.text.trim()) return;
      patch(id, (m) => ({ ...m, clarification: undefined }));
      await send(reply.text);
    },
    [patch, send],
  );

  /** "Hỏi lại": the same question again, as a new turn of the conversation (C6). */
  const retry = useCallback(
    async (id: string) => {
      const text = messagesRef.current.find((m) => m.id === id)?.questionText;
      if (text) await send(text);
    },
    [send],
  );

  const stop = useCallback(() => {
    queueRef.current = [];
    setQueued([]);
    const questionId = currentQuestionRef.current;
    if (questionId) void cancelQuestion(questionId).catch(() => undefined);
    abortRef.current?.abort();
  }, []);

  /** Load a server conversation into the screen (R1-13 URL reopen). */
  const hydrate = useCallback(async (id: string) => {
    if (!id || conversationRef.current === id) return;
    abortRef.current?.abort();
    setHydrating(true);
    try {
      const detail = await getConversation(id);
      const { messages: next, running } = await messagesFromConversation(detail);
      if (!mounted.current) return;
      conversationRef.current = detail.id;
      setConversationId(detail.id);
      setMessages(next);
      for (const turn of running) {
        const controller = new AbortController();
        abortRef.current = controller;
        void follow(turn.assistantId, turn.questionId, controller.signal).catch(() => undefined);
      }
    } catch (err) {
      if (!mounted.current) return;
      conversationRef.current = null;
      setConversationId(null);
      setMessages([
        {
          id: nextId(),
          role: 'assistant',
          content:
            err instanceof V1Error && err.status === 404
              ? 'Không tìm thấy hội thoại này, hoặc bạn không có quyền mở.'
              : err instanceof V1Error
                ? err.message
                : 'Không tải được hội thoại.',
          timestamp: nowIso(),
          status: 'error',
        },
      ]);
    } finally {
      if (mounted.current) setHydrating(false);
    }
  }, [follow]);

  const resetConversation = useCallback(() => {
    abortRef.current?.abort();
    queueRef.current = [];
    runningRef.current = false;
    setQueued([]);
    conversationRef.current = null;
    setConversationId(null);
    setMessages([]);
  }, []);

  const addNotice = useCallback((content: string) => {
    setMessages((prev) => [
      ...prev,
      { id: nextId(), role: 'assistant', content, timestamp: nowIso(), status: 'done' },
    ]);
  }, []);

  return {
    messages,
    sending,
    hydrating,
    queued,
    conversationId,
    send,
    answerClarification,
    retry,
    stop,
    addNotice,
    hydrate,
    resetConversation,
  };
}
