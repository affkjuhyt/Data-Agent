import { api, ok } from './client';
import type { components } from './schema';

/*
 * "Báo sai câu trả lời" (UC-01-05, Figma 2239:15384) on POST /v1/answers/{id}/feedback (A2 · Hỏi
 * đáp). The four choices of "Sai ở đâu?" map to the spec's error_kind. Sending it to the data
 * steward is rating `wrong`, which opens review in Cần bạn (and, once confirmed, the eval set);
 * without it the reader only rates the answer down. Reporting never changes a number by itself.
 */

export type AnswerReportKind = 'misread_question' | 'wrong_number' | 'wrong_source' | 'incomplete';
export type Feedback = components['schemas']['FeedbackView'];
type FeedbackCreate = components['schemas']['FeedbackCreate'];

const ERROR_KIND: Record<AnswerReportKind, NonNullable<FeedbackCreate['error_kind']>> = {
  misread_question: 'wrong_scope',
  wrong_number: 'wrong_number',
  wrong_source: 'wrong_document',
  incomplete: 'other',
};

interface AnswerReportInput {
  kind: AnswerReportKind;
  /** "Con số hoặc cách hiểu đúng" */
  correction?: string;
  /** "Gửi người phụ trách dữ liệu xem; nếu đúng, thêm vào bộ câu hỏi chuẩn" */
  sendToSteward: boolean;
}

export const reportAnswer = async (answerId: string, input: AnswerReportInput) =>
  ok<Feedback>(
    api.POST('/v1/answers/{id}/feedback', {
      params: { path: { id: answerId } },
      body: {
        rating: input.sendToSteward ? 'wrong' : 'down',
        error_kind: ERROR_KIND[input.kind],
        expected_value: input.correction ?? null,
      } satisfies FeedbackCreate,
    }),
  );
