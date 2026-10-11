import { http, HttpResponse } from 'msw';
import type { Answer } from '../lib/v1/questions';

/*
 * MSW handlers for Hỏi đáp's question flow (POST /v1/questions, its stream, GET /v1/answers/{id}),
 * for tests and the mock dev mode. Like the api, the stream sends the state recorded so far and
 * closes: the first read gets understood + progress, the next one the answer and done, so a
 * client must poll with Last-Event-ID the way it does against worker-agent.
 */
interface Asked {
  conversationId: string;
  text: string;
  reads: number;
  answerId: string;
}

const sse = (events: { id: string; event: string; data: unknown }[]) =>
  new HttpResponse(
    events.map((e) => `id: ${e.id}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join(''),
    { headers: { 'Content-Type': 'text/event-stream' } },
  );

export function createQuestionHandlers() {
  const questions = new Map<string, Asked>();
  const answers = new Map<string, Answer>();

  return [
    http.post('*/v1/questions', async ({ request }) => {
      if (!request.headers.get('X-Data-Space'))
        return HttpResponse.json(
          { type: 'https://docs.dataagent/errors/data_space_required', title: 'Data Space required', status: 400, code: 'data_space_required', detail: 'Chọn Data Space.' },
          { status: 400, headers: { 'Content-Type': 'application/problem+json' } },
        );
      const body = (await request.json()) as { text: string; conversation_id?: string | null };
      const id = crypto.randomUUID();
      const conversationId = body.conversation_id ?? crypto.randomUUID();
      questions.set(id, { conversationId, text: body.text, reads: 0, answerId: crypto.randomUUID() });
      return HttpResponse.json(
        { id, question_id: id, conversation_id: conversationId, status: 'received', poll_after_ms: 250, stream_url: `/v1/questions/${id}/stream` },
        { status: 202, headers: { Location: `/v1/questions/${id}` } },
      );
    }),
    http.get('*/v1/questions/:id/stream', ({ params, request }) => {
      const q = questions.get(String(params.id));
      if (!q) return undefined;
      const after = Number(request.headers.get('Last-Event-ID') ?? -1);
      q.reads += 1;
      const events = [{ id: '11', event: 'understood', data: { plan: { version: 1, chips: [] } } }, { id: '12', event: 'progress', data: { status: 'planning' } }];
      if (q.reads > 1) {
        answers.set(q.answerId, {
          id: q.answerId,
          question_id: String(params.id),
          plan_id: crypto.randomUUID(),
          kind: 'data',
          text: `Trả lời cho “${q.text}”: tỷ lệ nợ xấu bán lẻ tháng 9/2026 là 1,84%.`,
          figures: [],
          chart_spec: null,
          citations: [],
          filed_vs_current: null,
          reconcile_badge: null,
          agent_name: 'Agent Hỏi đáp',
          latency_ms: 1200,
          lineage_hash: null,
          data_product_id: null,
          product_scope: 'not_applicable',
          forecast_job_id: null,
          number_check: {},
          created_at: new Date().toISOString(),
        } as Answer);
        events.push(
          { id: '15', event: 'answer', data: { answer_id: q.answerId, kind: 'data' } } as never,
          { id: '17', event: 'done', data: { status: 'answered', answer_id: q.answerId } } as never,
        );
      }
      return sse(events.filter((e) => Number(e.id) > after));
    }),
    http.get('*/v1/answers/:id', ({ params }) => {
      const answer = answers.get(String(params.id));
      return answer ? HttpResponse.json(answer) : undefined;
    }),
  ];
}
