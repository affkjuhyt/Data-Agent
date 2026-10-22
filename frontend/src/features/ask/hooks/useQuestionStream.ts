/**
 * Conversation state for the Ask screen over the v1 API (UC-01-01, A1.6): a question is recorded
 * (POST /v1/questions, 202), worker-agent answers it, and the screen follows its stream —
 * understood → progress → answer / clarification / error → done. The server sends the state it
 * has recorded and closes, so the hook polls the stream with Last-Event-ID until `done`.
 *
 * Each turn keeps its server ids (question_id, answer_id): Báo sai, Lưu tri thức and Biên nhận
 * act on the real answer. Questions of one screen share a conversation, so a follow-up inherits
 * the reading of the one before (UC-01-04).
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { V1Error } from '../../../lib/v1/client';
import { getQuerySql } from '../../../lib/v1/explore';
import {
  askQuestion,
  getAnswer,
  getConversation,
  getQuestion,
  questionEvents,
  type ConversationDetail,
} from '../../../lib/v1/questions';
import { fromAnswer } from '../answerMessage';
import { readPlan } from '../dataAnswer';
import type { AskMessage, ClarificationReply } from '../types';

const POLL_MS = 400;
// A turn the worker has not finished in this long is shown as failed; the question stays
// recorded and can be opened again from the conversation.
const GIVE_UP_MS = 5 * 60_000;

const nowIso = () => new Date().toISOString();
let seq = 0;
const nextId = () => `${Date.now().toString(36)}-${(seq++).toString(36)}`;
const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(new DOMException('aborted', 'AbortError'));
    });
  });

const STATUS_STEP: Record<string, string> = {
  received: 'Đã nhận câu hỏi',
  planning: 'Đang hiểu câu hỏi và lập kế hoạch',
  running: 'Đang chạy kế hoạch',
};

async function messagesFromConversation(detail: ConversationDetail): Promise<AskMessage[]> {
  const out: AskMessage[] = [];
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
          proposal = plan.proposal;
          newQuestion = q.seq > 1 && !!reading?.length && !reading.some((c) => c.inherited);
        } catch {
          /* plan optional on reopen */
        }
        out.push({
          id: assistantId,
          role: 'assistant',
          content: '',
          timestamp: turn.answered_at ?? turn.received_at,
          status: 'done',
          questionId: turn.question_id,
          answerId: turn.answer_id,
          ...drawn,
          ...(reading && { reading }),
          ...(proposal && { proposal }),
          ...(newQuestion !== undefined && { newQuestion }),
        });
        const run = drawn.evidence?.runs[0];
        if (run?.id) {
          try {
            const sql = await getQuerySql(run.id);
            const last = out[out.length - 1];
            if (last.evidence) {
              last.evidence = {
                ...last.evidence,
                runs: last.evidence.runs.map((r) =>
                  r.id === run.id ? { ...r, sql: sql.sql_executed } : r,
                ),
              };
            }
          } catch {
            /* SQL is enrichment for Cách tính */
          }
        }
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
    } else {
      out.push({
        id: assistantId,
        role: 'assistant',
        content:
          turn.status === 'failed'
            ? 'Agent không trả lời được câu hỏi này.'
            : 'Câu hỏi chưa có câu trả lời (đang chạy hoặc chưa xong).',
        timestamp: turn.received_at,
        status: turn.status === 'failed' ? 'error' : 'done',
        questionId: turn.question_id,
      });
    }
  }
  return out;
}

export function useQuestionStream() {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [hydrating, setHydrating] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const conversationRef = useRef<string | null>(null);
  const mounted = useRef(true);

  useEffect(
    () => () => {
      mounted.current = false;
      abortRef.current?.abort();
    },
    [],
  );

  const patch = useCallback((id: string, fn: (m: AskMessage) => AskMessage) => {
    if (mounted.current) setMessages((prev) => prev.map((m) => (m.id === id ? fn(m) : m)));
  }, []);

  const follow = useCallback(
    async (assistantId: string, questionId: string, signal: AbortSignal) => {
      const started = Date.now();
      let lastId: string | undefined;
      for (;;) {
        const events = await questionEvents(questionId, lastId, signal);
        for (const e of events) {
          lastId = e.id ?? lastId;
          const data = e.data ? (JSON.parse(e.data) as Record<string, unknown>) : {};
          if (e.event === 'progress' || e.event === 'understood') {
            const status = String(data.status ?? (e.event === 'understood' ? 'planning' : ''));
            patch(assistantId, (m) => ({
              ...m,
              progress: {
                agent: 'Agent Hỏi đáp',
                steps: [{ title: STATUS_STEP[status] ?? 'Đang xử lý', state: 'running' }],
              },
            }));
          } else if (e.event === 'answer' && typeof data.answer_id === 'string') {
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
            const run = drawn.evidence?.runs[0];
            // "Cách tính" shows the SQL that ran; the answer does not carry it, the query run does.
            if (run?.id)
              getQuerySql(run.id)
                .then((q) =>
                  patch(assistantId, (m) => ({
                    ...m,
                    evidence: m.evidence && {
                      ...m.evidence,
                      runs: m.evidence.runs.map((r) => (r.id === run.id ? { ...r, sql: q.sql_executed } : r)),
                    },
                  })),
                )
                .catch(() => undefined);
          } else if (e.event === 'error') {
            patch(assistantId, (m) => ({
              ...m,
              status: 'error',
              content: m.content || 'Agent không trả lời được câu hỏi này.',
            }));
          } else if (e.event === 'done') {
            patch(assistantId, (m) => ({
              ...m,
              progress: undefined,
              status: m.status === 'error' ? 'error' : 'done',
              latencyMs: Date.now() - started,
            }));
            return;
          }
        }
        if (Date.now() - started > GIVE_UP_MS) {
          patch(assistantId, (m) => ({
            ...m,
            status: 'error',
            progress: undefined,
            content: 'Agent chưa trả lời sau 5 phút; câu hỏi vẫn được lưu trong hội thoại.',
          }));
          return;
        }
        await sleep(POLL_MS, signal);
      }
    },
    [patch],
  );

  const send = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || sending) return;
      const assistantId = nextId();
      setMessages((prev) => [
        ...prev,
        { id: nextId(), role: 'user', content: q, timestamp: nowIso(), status: 'done' },
        { id: assistantId, role: 'assistant', content: '', timestamp: nowIso(), status: 'streaming' },
      ]);
      setSending(true);
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const accepted = await askQuestion(q, conversationRef.current);
        conversationRef.current = accepted.conversation_id;
        if (mounted.current) setConversationId(accepted.conversation_id);
        patch(assistantId, (m) => ({ ...m, questionId: accepted.question_id }));
        await follow(assistantId, accepted.question_id, controller.signal);
      } catch (err) {
        const aborted = controller.signal.aborted;
        patch(assistantId, (m) => ({
          ...m,
          status: 'error',
          progress: undefined,
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
        if (mounted.current) setSending(false);
        abortRef.current = null;
      }
    },
    [follow, patch, sending],
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

  /** The v1 stream does not pause for a reading check yet (PATCH /v1/questions/{id}/plan edits
   *  chips after the fact); confirming only hides the check. */
  const confirmUnderstanding = useCallback(
    // Same signature as the screen calls it with; the choice has nowhere to go on v1 yet.
    async (id: string, _choice?: { meaning?: string; remember: boolean }) => {
      patch(id, (m) => (m.understanding ? { ...m, understanding: { ...m.understanding, check_required: false } } : m));
    },
    [patch],
  );

  const stop = useCallback(() => abortRef.current?.abort(), []);

  /** Load a server conversation into the screen (R1-13 URL reopen). */
  const hydrate = useCallback(async (id: string) => {
    if (!id || conversationRef.current === id) return;
    abortRef.current?.abort();
    setHydrating(true);
    try {
      const detail = await getConversation(id);
      const next = await messagesFromConversation(detail);
      if (!mounted.current) return;
      conversationRef.current = detail.id;
      setConversationId(detail.id);
      setMessages(next);
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
  }, []);

  const resetConversation = useCallback(() => {
    abortRef.current?.abort();
    conversationRef.current = null;
    setConversationId(null);
    setMessages([]);
  }, []);

  return {
    messages,
    sending,
    hydrating,
    conversationId,
    send,
    answerClarification,
    confirmUnderstanding,
    stop,
    hydrate,
    resetConversation,
  };
}
