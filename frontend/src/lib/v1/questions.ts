import { pathId, v1Events, type Page, api, ok, okAs } from './client';
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

/** Wire shapes for GET /v1/conversations/{id} (R1-13). Kept local until `make api-types`. */
export interface ConversationTurn {
  question_id: string;
  seq: number;
  text: string;
  status: string;
  answer_id: string | null;
  received_at: string;
  answered_at: string | null;
}

export interface ConversationDetail {
  id: string;
  data_space_id: string;
  user_id: string;
  channel: string;
  api_client_id: string | null;
  title: string | null;
  workspace_id: string | null;
  last_question_at: string | null;
  created_at: string;
  updated_at: string;
  turns: ConversationTurn[];
}

export interface ConversationListItem {
  id: string;
  title: string | null;
  last_question_at: string | null;
  updated_at: string;
}

export const askQuestion = (text: string, conversationId: string | null) =>
  ok<QuestionAccepted>(
    api.POST('/v1/questions', {
      body: {
        text,
        conversation_id: conversationId,
        knowledge_mode: 'latest',
        stream: true,
      } satisfies QuestionCreate,
    }),
  );

export const getAnswer = async (id: string) =>
  ok<Answer>(api.GET('/v1/answers/{id}', { params: { path: { id } } }));

export type QuestionWire = components['schemas']['QuestionView'];

/** The question with its latest plan (chips and steps), for "Hệ thống hiểu là" and the steps
 *  waiting for the person (UC-01-04, UC-03-01). */
export const getQuestion = async (id: string) =>
  ok<QuestionWire>(api.GET('/v1/questions/{id}', { params: { path: { id } } }));

/** Reopen an Ask thread: ordered turns with answer_id when answered (R1-13). */
export const getConversation = async (id: string) =>
  okAs<ConversationDetail>(api.GET('/v1/conversations/{id}', { params: { path: { id } } }));

/** Recent conversations for this viewer in the current Data Space. */
export const listConversations = async (pageSize = 8) =>
  ok<Page<ConversationListItem>>(
    api.GET('/v1/conversations', { params: { query: { page_size: pageSize } } }),
  );

/** The question's events after `lastEventId`; the server closes the stream after what it has. */
export const questionEvents = async (id: string, lastEventId?: string, signal?: AbortSignal) =>
  v1Events(`/questions/${pathId(id)}/stream`, { lastEventId, signal });

/** "Thêm vào phiên": the answer becomes a question tile (POST /v1/workspaces/{id}/tiles). */
export const saveAnswerToSession = (
  workspaceId: string,
  answer: { answerId: string; title: string },
) =>
  ok<components['schemas']['TileView']>(
    api.POST('/v1/workspaces/{id}/tiles', {
      params: { path: { id: workspaceId } },
      body: {
        kind: 'question',
        title: answer.title,
        spec: { question: answer.title, answer_id: answer.answerId },
      },
    }),
  );
