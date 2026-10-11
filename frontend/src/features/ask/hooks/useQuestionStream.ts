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
import { askQuestion, getAnswer, questionEvents, type Answer } from '../../../lib/v1/questions';
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

/** The answer as the Ask screen draws it. A refusal has no code on the wire (qa.answer keeps
 *  kind and text only), so it is drawn as a refusal with the server's message. */
function fromAnswer(answer: Answer): Partial<AskMessage> {
  if (answer.kind === 'refusal') {
    return {
      content: '',
      refusal: { code: 'REFUSED', message: answer.text, next_action: '' },
    };
  }
  return { content: answer.text };
}

export function useQuestionStream() {
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [sending, setSending] = useState(false);
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
            patch(assistantId, (m) => ({ ...m, ...fromAnswer(answer), answerId: answer.id }));
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

  return { messages, sending, send, answerClarification, confirmUnderstanding, stop };
}
