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
  seq: number;
}

/** The worker's plan for a question (questions.py _plan_ir, resolver follow_up): a follow-up
 *  inherits the metric and the month; "vì sao" adds the decomposition awaiting a yes. */
function planOf(q: Asked) {
  const from = q.seq > 1 ? 'inherited' : 'default';
  const steps: Record<string, unknown>[] = [
    { id: 's1', kind: 'semantic_query', tool: 'query.semantic', checkpoint: 'none', explain: 'Tính Tỷ lệ nợ xấu' },
  ];
  if (/vì sao|tại sao/i.test(q.text))
    steps.push({
      id: 's2',
      kind: 'skill',
      tool: 'skill.run',
      checkpoint: 'confirm',
      depends_on: ['s1'],
      explain: 'Phân rã biến động Tỷ lệ nợ xấu theo vùng so với tháng trước',
      args: {
        category: 'decomposition',
        params: { metric: 'NPL_RATIO', dimension: 'vung', period: '2026-09', base_period: '2026-08' },
      },
    });
  return {
    version: 1,
    chips: [
      { kind: 'metric', value: 'NPL_RATIO', label: 'Tỷ lệ nợ xấu', source: from, confidence: 1 },
      { kind: 'period', value: '2026-09-01/2026-10-01', label: 'tháng 9/2026', source: from, confidence: 1 },
      { kind: 'dimension', value: 'vung', label: 'vùng', source: 'default', confidence: 1 },
    ],
    steps: { version: 2, steps },
  };
}

const sse = (events: { id: string; event: string; data: unknown }[]) =>
  new HttpResponse(
    events.map((e) => `id: ${e.id}\nevent: ${e.event}\ndata: ${JSON.stringify(e.data)}\n\n`).join(''),
    { headers: { 'Content-Type': 'text/event-stream' } },
  );

const QUERY_RUN_ID = '0192f0c4-7a1e-7c00-9a51-5e1f00000001';
const REGIONS: [string, string][] = [
  ['Đồng bằng sông Hồng', '1.84'],
  ['Duyên hải Bắc Bộ', '2.31'],
  ['Đông Nam Bộ', '1.52'],
];

function dataAnswer(id: string, questionId: string, text: string): Answer {
  return {
    id,
    question_id: questionId,
    plan_id: crypto.randomUUID(),
    kind: 'data',
    text,
    figures: REGIONS.map(([vung, value], i) => ({
      key: `F${i + 1}`,
      label: `Tỷ lệ nợ xấu, Vùng ${vung}, tháng 9/2026`,
      value,
      unit: '%',
      decimals: 2,
      dimensions: { vung },
      display_state: 'shown',
      source: { query_run_id: QUERY_RUN_ID, row: i, col: 'value' },
      query_run_id: QUERY_RUN_ID,
      metric_version_id: 'mv-npl-3',
      trust: 'Theo chỉ số chuẩn',
    })),
    chart_spec: {
      type: 'bar',
      x: 'vung',
      x_label: 'Vùng',
      y: 'value',
      y_label: 'Tỷ lệ nợ xấu',
      unit: '%',
      drill: [{ code: 'chi_nhanh', name: 'chi nhánh' }],
    },
    citations: [],
    filed_vs_current: null,
    reconcile_badge: null,
    agent_name: 'Agent Hỏi đáp',
    latency_ms: 1200,
    lineage_hash: null,
    data_product_id: null,
    product_scope: 'not_applicable',
    forecast_job_id: null,
    // As worker_agent/answer/verification.py writes it with the answer (R0-8).
    number_check: {
      numbers_total: 3,
      traced: 3,
      untraceable_final: 0,
      verification: {
        verdict: 'PASS',
        iteration: 1,
        checks: [
          { name: 'Số trong câu có nguồn', passed: true, detail: '3/3 số trong câu trả lời có nguồn' },
          { name: 'Mỗi số có lần chạy', passed: true, detail: '3 số, mỗi số có lần chạy truy vấn' },
          { name: 'Khớp số đã nộp', passed: true, detail: 'Kỳ này chưa có số đã nộp' },
        ],
      },
    },
    created_at: new Date().toISOString(),
  } as Answer;
}

export function createQuestionHandlers() {
  const questions = new Map<string, Asked>();
  const turns = new Map<string, number>();
  const answers = new Map<string, Answer>([
    [
      'answer-npl-session',
      dataAnswer(
        'answer-npl-session',
        'q-npl-session',
        'Nợ xấu bán lẻ T9 tăng 0,22 điểm; DH Bắc Bộ đóng góp nhiều nhất.',
      ),
    ],
  ]);

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
      const seq = (turns.get(conversationId) ?? 0) + 1;
      turns.set(conversationId, seq);
      questions.set(id, { conversationId, text: body.text, reads: 0, answerId: crypto.randomUUID(), seq });
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
        answers.set(
          q.answerId,
          dataAnswer(
            q.answerId,
            String(params.id),
            `Trả lời cho “${q.text}”: tỷ lệ nợ xấu bán lẻ tháng 9/2026 là 1,84%.`,
          ),
        );
        events.push(
          { id: '15', event: 'answer', data: { answer_id: q.answerId, kind: 'data' } } as never,
          { id: '17', event: 'done', data: { status: 'answered', answer_id: q.answerId } } as never,
        );
      }
      return sse(events.filter((e) => Number(e.id) > after));
    }),
    http.get('*/v1/questions/:id', ({ params }) => {
      const q = questions.get(String(params.id));
      if (!q) return undefined;
      return HttpResponse.json({
        id: String(params.id),
        conversation_id: q.conversationId,
        seq: q.seq,
        text: q.text,
        parent_question_id: null,
        as_of: null,
        knowledge_mode: 'latest',
        knowledge_at: null,
        purpose: null,
        status: q.reads > 1 ? 'answered' : 'planning',
        received_at: new Date().toISOString(),
        answered_at: null,
        plan: { id: `plan-${String(params.id)}`, question_id: String(params.id), confidence: 1, edited_by_user: false, edit_note: null, created_at: new Date().toISOString(), ...planOf(q) },
        answer_id: q.reads > 1 ? q.answerId : null,
      });
    }),
    http.get('*/v1/query-runs/:id/sql', ({ params }) =>
      params.id === QUERY_RUN_ID
        ? HttpResponse.json({
            sql_generated: 'SELECT vung, avg(npl) AS value FROM du_no GROUP BY vung',
            sql_executed: 'SELECT vung, avg(npl) AS value FROM du_no GROUP BY vung',
            policies_applied: [],
          })
        : undefined,
    ),
    http.get('*/v1/answers/:id', ({ params }) => {
      const answer = answers.get(String(params.id));
      return answer ? HttpResponse.json(answer) : undefined;
    }),
    http.get('*/v1/conversations', () => {
      const byConv = new Map<string, { title: string; updated_at: string }>();
      for (const q of questions.values()) {
        const prev = byConv.get(q.conversationId);
        if (!prev) byConv.set(q.conversationId, { title: q.text, updated_at: new Date().toISOString() });
      }
      return HttpResponse.json({
        items: [...byConv.entries()].map(([id, meta]) => ({
          id,
          data_space_id: 'ds',
          user_id: 'u',
          channel: 'web',
          api_client_id: null,
          title: meta.title,
          workspace_id: null,
          last_question_at: meta.updated_at,
          created_at: meta.updated_at,
          updated_at: meta.updated_at,
        })),
        next_page_token: '',
      });
    }),
    http.get('*/v1/conversations/:id', ({ params }) => {
      const id = String(params.id);
      const turns = [...questions.entries()]
        .filter(([, q]) => q.conversationId === id)
        .sort((a, b) => a[1].seq - b[1].seq)
        .map(([qid, q]) => ({
          question_id: qid,
          seq: q.seq,
          text: q.text,
          status: 'answered',
          answer_id: q.answerId,
          received_at: new Date().toISOString(),
          answered_at: new Date().toISOString(),
        }));
      if (turns.length === 0) return new HttpResponse(null, { status: 404 });
      return HttpResponse.json({
        id,
        data_space_id: 'ds',
        user_id: 'u',
        channel: 'web',
        api_client_id: null,
        title: turns[0]?.text ?? null,
        workspace_id: null,
        last_question_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        turns,
      });
    }),
  ];
}
