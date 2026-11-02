import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { setDataSpace } from '../../../lib/v1/client';
import { createQuestionHandlers } from '../../../mocks/questions';
import { readVerification } from '../answerMessage';
import { useQuestionStream } from './useQuestionStream';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createQuestionHandlers()));
afterEach(() => {
  server.resetHandlers();
  setDataSpace(null);
});
afterAll(() => server.close());

describe('useQuestionStream', () => {
  it('AC-01-01-01 records the question, follows its stream and keeps the server answer id', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].status).toBe('done'));
    const answer = result.current.messages[1];
    expect(answer.questionId).toBeTruthy();
    expect(answer.answerId).toBeTruthy();
    expect(answer.content).toContain('1,84%');
    expect(answer.progress).toBeUndefined();
    // the figures are drawn: a bar per region, the table, the label, and the SQL behind them
    expect(answer.chartSpecs?.[0]).toMatchObject({ chart_type: 'bar', x_key: 'Vùng', y_keys: ['Tỷ lệ nợ xấu'] });
    expect(answer.chartSpecs?.[0].data).toHaveLength(3);
    expect(answer.evidence?.runs[0].columns).toEqual(['Vùng', 'Tỷ lệ nợ xấu']);
    expect(answer.trust?.label).toBe('Theo chỉ số chuẩn');
    // The steps it went through, as the stream sent them, and the answer's blocks.
    expect(answer.steps?.map((step) => [step.action, step.status])).toEqual([
      ['understand', 'done'],
      ['query', 'done'],
    ]);
    expect(answer.steps?.[1].detail).toMatchObject({ row_count: 3 });
    expect(answer.durationMs).toBeGreaterThanOrEqual(0);
    expect(answer.questionText).toBe('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
  });

  it('AC-01-04-01 a follow-up shows the chips it inherited, and a why-question its proposal', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].reading).toBeDefined());
    const first = result.current.messages[1];
    expect(first.reading?.some((c) => c.inherited)).toBe(false);
    expect(first.newQuestion).toBe(false);
    expect(first.drill).toEqual({ axis: 'Vùng', cuts: [{ code: 'chi_nhanh', name: 'chi nhánh' }] });
    expect(first.proposal).toBeUndefined();

    await act(async () => {
      await result.current.send('Vì sao còn theo vùng thì tăng?');
    });
    await waitFor(() => expect(result.current.messages[3].reading).toBeDefined());
    const next = result.current.messages[3];
    expect(next.reading?.filter((c) => c.inherited).map((c) => c.label)).toEqual(['Chỉ số', 'Kỳ']);
    expect(next.newQuestion).toBe(false);
    expect(next.proposal?.params).toEqual({
      metric: 'NPL_RATIO',
      dimension: 'vung',
      period: '2026-09',
      base_period: '2026-08',
    });
  });

  it('queues a follow-up while the current answer is streaming', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());

    let first: Promise<void> | undefined;
    await act(async () => {
      first = result.current.send('Doanh thu tháng này');
      await result.current.send('Doanh thu tháng trước');
    });

    expect(result.current.queued).toEqual(['Doanh thu tháng trước']);
    await act(async () => {
      await first;
    });
    await waitFor(() => expect(result.current.queued).toEqual([]));
    await waitFor(() => expect(result.current.messages).toHaveLength(4));
    expect(result.current.messages[2].content).toContain('Doanh thu tháng trước');
  });

  it('asks for a Data Space when none is chosen (A1.2 data_space_required)', async () => {
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Doanh thu tháng này');
    });
    expect(result.current.messages[1].status).toBe('error');
    expect(result.current.messages[1].content).toBe('Chọn một Data Space ở thanh trên trước khi hỏi.');
  });

  it('R1-13 hydrates a conversation URL into messages with answer ids', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.conversationId).toBeTruthy());
    const conversationId = result.current.conversationId!;
    const answerId = result.current.messages[1].answerId;
    expect(answerId).toBeTruthy();

    const reopened = renderHook(() => useQuestionStream());
    await act(async () => {
      await reopened.result.current.hydrate(conversationId);
    });
    await waitFor(() => expect(reopened.result.current.messages).toHaveLength(2));
    expect(reopened.result.current.conversationId).toBe(conversationId);
    expect(reopened.result.current.messages[0].content).toContain('nợ xấu');
    expect(reopened.result.current.messages[1].answerId).toBe(answerId);
  });

  it('once a turn ends, its steps are read back, with the model calls the stream does not send', async () => {
    let reads = 0;
    server.use(
      http.get('*/v1/questions/:id/steps', ({ params }) => {
        reads += 1;
        // The worker writes the trace just after `done`: the first read finds no run yet.
        if (reads === 1) return HttpResponse.json({ question_id: String(params.id), run_id: null, status: null, duration_ms: null, tokens: 0, steps: [] });
        const at = '2026-11-02T10:00:00Z';
        return HttpResponse.json({
          question_id: String(params.id), run_id: 'run-1', status: 'succeeded', duration_ms: 1800, tokens: 296,
          steps: [
            { step_id: 's1', seq: 1, action: 'write_answer', title: 'Viết câu trả lời', target: '', chips: [], status: 'done', started_at: at, duration_ms: 900, detail: null, summary: null, parent: null },
            { step_id: 's2', seq: 2, action: 'model_call', title: 'Gọi mô hình responder', target: '', chips: [], status: 'done', started_at: at, duration_ms: 760, detail: { model: 'responder' }, summary: null, parent: 's1' },
          ],
        });
      }),
    );
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].steps?.[0]?.children).toHaveLength(1), { timeout: 4000 });
    expect(result.current.messages[1].steps?.map((step) => step.action)).toEqual(['write_answer']);
  });

  it('reads the steps of an ended turn back when the conversation is reopened', async () => {
    setDataSpace('ds-rr');
    const first = renderHook(() => useQuestionStream());
    await act(async () => {
      await first.result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(first.result.current.conversationId).toBeTruthy());
    const reopened = renderHook(() => useQuestionStream());
    await act(async () => {
      await reopened.result.current.hydrate(first.result.current.conversationId!);
    });
    await waitFor(() => expect(reopened.result.current.messages).toHaveLength(2));
    const turn = reopened.result.current.messages[1];
    expect(turn.stepsRecorded).toBe(true);
    expect(turn.steps?.map((step) => step.action)).toEqual(['understand', 'query']);
    expect(turn.durationMs).toBe(2400);
  });

  it('a turn recorded before steps were kept says so', async () => {
    server.use(
      http.get('*/v1/questions/:id/steps', ({ params }) =>
        HttpResponse.json({ question_id: String(params.id), run_id: null, status: null, duration_ms: null, tokens: 0, steps: [] }),
      ),
    );
    setDataSpace('ds-rr');
    const first = renderHook(() => useQuestionStream());
    await act(async () => {
      await first.result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(first.result.current.conversationId).toBeTruthy());
    const reopened = renderHook(() => useQuestionStream());
    await act(async () => {
      await reopened.result.current.hydrate(first.result.current.conversationId!);
    });
    await waitFor(() => expect(reopened.result.current.messages).toHaveLength(2));
    expect(reopened.result.current.messages[1].stepsRecorded).toBe(false);
  });

  it('follows a turn still running when the conversation is reopened, from its first event', async () => {
    const events = [
      'id: 1-0\nevent: activity\ndata: {"kind":"step","step_id":"s1","seq":1,"action":"understand","title":"Hiểu câu hỏi","target":"","chips":[],"status":"done","started_at":"2026-11-02T10:00:00Z","duration_ms":80,"detail":{"reading":[]},"parent":null}\n\n',
      'id: 2-0\nevent: done\ndata: {"status":"refused"}\n\n',
    ];
    let lastIds: (string | null)[] = [];
    server.use(
      http.get('*/v1/conversations/conv-run', () =>
        HttpResponse.json({
          id: 'conv-run', data_space_id: 'ds', user_id: 'u', channel: 'web', api_client_id: null, title: 'Q',
          workspace_id: null, last_question_at: null, created_at: '2026-11-02T10:00:00Z', updated_at: '2026-11-02T10:00:00Z',
          turns: [{ question_id: 'q-run', seq: 1, text: 'Doanh thu?', status: 'running', answer_id: null, received_at: '2026-11-02T10:00:00Z', answered_at: null }],
        }),
      ),
      http.get('*/v1/questions/q-run/stream', ({ request }) => {
        lastIds = [...lastIds, request.headers.get('Last-Event-ID')];
        return new HttpResponse(events.join(''), { headers: { 'Content-Type': 'text/event-stream' } });
      }),
    );
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.hydrate('conv-run');
    });
    await waitFor(() => expect(result.current.messages[1].status).toBe('done'));
    expect(lastIds[0]).toBeNull();
    expect(result.current.messages[1].steps?.map((step) => step.action)).toEqual(['understand']);
  });

  it('asks the same question again as a new turn', async () => {
    setDataSpace('ds-rr');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
    });
    await waitFor(() => expect(result.current.messages[1].status).toBe('done'));
    await act(async () => {
      await result.current.retry(result.current.messages[1].id);
    });
    await waitFor(() => expect(result.current.messages).toHaveLength(4));
    expect(result.current.messages[2].content).toBe('Tỷ lệ nợ xấu tháng 9/2026 theo vùng');
  });

  it('shows the question back of a clarifying turn, with a box to answer it', async () => {
    const asked = 'Data Space không có chỉ số nào khớp đúng câu hỏi. Bạn muốn hỏi: 1. Doanh thu Internet?';
    server.use(
      http.get('*/v1/questions/:id/stream', () =>
        new HttpResponse(
          [
            'id: 4\nevent: clarification\ndata: {"answer_id": "answer-ask-back"}\n\n',
            'id: 7\nevent: done\ndata: {"status": "clarifying", "answer_id": "answer-ask-back"}\n\n',
          ].join(''),
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      ),
      http.get('*/v1/answers/answer-ask-back', () =>
        HttpResponse.json({ id: 'answer-ask-back', kind: 'clarification', text: asked }),
      ),
    );
    setDataSpace('ds-aw');
    const { result } = renderHook(() => useQuestionStream());
    await act(async () => {
      await result.current.send('Doanh thu Internet năm 2013 là bao nhiêu?');
    });
    await waitFor(() => expect(result.current.messages[1].status).toBe('done'));
    const back = result.current.messages[1];
    expect(back.content).toBe(asked);
    expect(back.answerId).toBe('answer-ask-back');
    expect(back.clarification).toEqual({ kind: 'followup', questions: [{ text: '' }] });
  });
});

describe('readVerification', () => {
  it('R0-8 reads the checks the worker wrote with the answer', () => {
    const verification = readVerification({
      numbers_total: 2,
      verification: {
        verdict: 'FAIL',
        iteration: 1,
        checks: [
          { name: 'Số trong câu có nguồn', passed: true, detail: '2/2 số' },
          { name: 'Khớp số đã nộp', passed: false, detail: 'Số hiện tại khác số đã nộp của kỳ' },
        ],
      },
    });
    expect(verification?.verdict).toBe('FAIL');
    expect(verification?.checks.map(c => c.passed)).toEqual([true, false]);
  });

  it('draws no verdict from anything else', () => {
    expect(readVerification({ numbers_total: 2 })).toBeUndefined();
    expect(readVerification({ verification: { verdict: 'MAYBE', checks: [] } })).toBeUndefined();
    expect(readVerification(null)).toBeUndefined();
  });
});
