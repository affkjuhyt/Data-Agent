import { pathId, v1Events, v1Get, v1Send } from './client';
import type { components } from './schema';

/*
 * Hỏi đáp (A2, UC-01-01): a question is recorded with 202 and answered by worker-agent; the
 * stream (A1.6) sends understood → progress → answer/clarification/error → done. Types are
 * generated from the api (make api-types). Every call here belongs to a Data Space, so the
 * X-Data-Space header the top bar sets must be present (400 data_space_required otherwise).
 */

type QuestionCreate = components['schemas']['QuestionCreate'];
type QuestionAccepted = components['schemas']['QuestionAccepted'];
export type Answer = components['schemas']['AnswerView'];

export const askQuestion = (text: string, conversationId: string | null) =>
  v1Send<QuestionAccepted>('POST', '/questions', {
    text,
    conversation_id: conversationId,
    knowledge_mode: 'latest',
    stream: true,
  } satisfies QuestionCreate);

export const getAnswer = async (id: string) => v1Get<Answer>(`/answers/${pathId(id)}`);

export type QuestionWire = components['schemas']['QuestionView'];

/** The question with its latest plan (chips and steps), for "Hệ thống hiểu là" and the steps
 *  waiting for the person (UC-01-04, UC-03-01). */
export const getQuestion = async (id: string) => v1Get<QuestionWire>(`/questions/${pathId(id)}`);

/** The question's events after `lastEventId`; the server closes the stream after what it has. */
export const questionEvents = async (id: string, lastEventId?: string, signal?: AbortSignal) =>
  v1Events(`/questions/${pathId(id)}/stream`, { lastEventId, signal });
