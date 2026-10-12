import { http, HttpResponse } from 'msw';
import type { Feedback } from '../lib/v1/answerReports';

/*
 * MSW handler for POST /v1/answers/{id}/feedback (A2 · Hỏi đáp, UC-01-05), for tests and the
 * mock dev mode. Same rule as the api: rating `wrong` needs an error_kind (422).
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { type: `https://docs.dataagent/errors/${code}`, title: code, status, code, detail, trace_id: 'mock' },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createFeedbackHandlers() {
  return [
    http.post('*/v1/answers/:id/feedback', async ({ params, request }) => {
      const body = (await request.json()) as Partial<Feedback>;
      if (body.rating === 'wrong' && !body.error_kind)
        return problem(422, 'validation_failed', 'Chọn câu trả lời sai ở đâu.');
      const feedback: Feedback = {
        id: crypto.randomUUID(),
        answer_id: String(params.id),
        user_id: 'u-mock',
        rating: body.rating ?? 'down',
        error_kind: body.error_kind ?? null,
        expected_value: body.expected_value ?? null,
        comment: body.comment ?? null,
        status: 'open',
        reviewer_id: null,
        decided_at: null,
        reason: null,
        proposal_id: null,
        eval_item_id: null,
        created_at: new Date().toISOString(),
      } as Feedback;
      return HttpResponse.json(feedback, { status: 201 });
    }),
  ];
}
