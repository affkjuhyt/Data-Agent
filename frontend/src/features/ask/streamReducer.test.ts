import { describe, expect, it } from 'vitest';
import { parseSseData, readSseStream, type SseEvent } from './sse';
import { finishStream, startStream, streamReducer } from './streamReducer';
import type { AskMessage } from './types';

const placeholder: AskMessage = {
  id: 'a1',
  role: 'assistant',
  content: '',
  timestamp: 't',
  status: 'streaming',
};

/** Feed raw `data:` payloads through the parser and the reducer, as the hook does. */
const run = (frames: unknown[], query = 'Doanh thu tháng 9?') => {
  const state = frames
    .map((f) =>
      parseSseData(typeof f === 'string' && /^\[(DONE|ERROR)/.test(f) ? f : JSON.stringify(f)),
    )
    .reduce(streamReducer, startStream(placeholder, query));
  return { state, done: finishStream(state, 42) };
};

describe('streamReducer: frames the Ask screen already shows', () => {
  it('builds the answer from tokens; a reset drops what was streamed', () => {
    const { done } = run([{ __session_id: 's1' }, 'Doanh ', 'thu', { __reset: true }, 'Mới']);
    expect(done).toMatchObject({ content: 'Mới', sessionId: 's1', status: 'done', latencyMs: 42 });
  });

  it('keeps chart, citations, suggestions, result sets (replaced by id) and pending review', () => {
    const { done } = run([
      { __chart: [{ chart_type: 'bar' }] },
      { __citations: [{ index: 1 }] },
      { __suggestions: ['a'] },
      { __result_set: { result_id: 'r1', row_count: 1 } },
      { __result_set: { result_id: 'r1', row_count: 2 } },
      { __pending_review: { conv_id: 'c1', payload: { title: 't' } } },
    ]);
    expect(done.chartSpecs).toEqual([{ chart_type: 'bar' }]);
    expect(done.citations).toEqual([{ index: 1 }]);
    expect(done.suggestions).toEqual(['a']);
    expect(done.resultSets).toEqual([{ result_id: 'r1', row_count: 2 }]);
    expect(done.pendingReview).toEqual({ convId: 'c1', payload: { title: 't' } });
  });

  it('an interrupted orchestration resumes on the streamed session and shows its question', () => {
    const { done } = run([
      { __session_id: 's1' },
      { __clarification: { questions: ['Kỳ nào?'] } },
      '[DONE]',
    ]);
    expect(done.clarification).toEqual({
      kind: 'resume',
      sessionId: 's1',
      questions: [{ text: 'Kỳ nào?' }],
    });
    expect(done.content).toBe('Kỳ nào?');
  });

  it('evidence questions become a follow-up card, unless a clarification is already there', () => {
    const q = [{ text: 'Chi nhánh nào?', options: ['HN'] }];
    expect(run([{ __evidence: { clarifications: q } }]).done.clarification).toEqual({
      kind: 'followup',
      questions: q,
      query: 'Doanh thu tháng 9?',
    });
    expect(
      run([
        { __clarification: { session_id: 's', questions: ['A?'] } },
        { __evidence: { clarifications: q } },
      ]).done.clarification,
    ).toMatchObject({ kind: 'resume' });
  });

  it('responder options become a follow-up card only when the stream ends', () => {
    const { state, done } = run([{ __options: ['Tháng này'] }]);
    expect(state.message.clarification).toBeUndefined();
    expect(done.clarification).toEqual({
      kind: 'followup',
      questions: [{ text: '', options: ['Tháng này'] }],
      query: 'Doanh thu tháng 9?',
    });
  });

  it('a refusal replaces the answer; an [ERROR] keeps what was streamed', () => {
    const refused = run([
      'Một phần',
      {
        __refusal: {
          code: 'PLANNER_ERROR',
          message: 'Không lập được kế hoạch',
          next_action: 'rephrase',
        },
      },
    ]).done;
    expect(refused.content).toBe('');
    expect(refused.refusal).toMatchObject({ code: 'PLANNER_ERROR' });
    const failed = run(['Một phần', '[ERROR] hỏng']).done;
    expect(failed).toMatchObject({ content: 'Một phần', status: 'error' });
    expect(run(['[ERROR] hỏng']).done.content).toBe('hỏng');
  });
});

describe('streamReducer: agent-run frames (AC-5)', () => {
  it('keeps run, plan, step, block, verification and lineage beside the old frames', () => {
    const { done } = run([
      { __run: { run_id: 'r1', status: 'started', agent: 'sales' } },
      { __plan: { nodes: [{ id: 'n1' }, { id: 'n2' }], edges: [['n1', 'n2']] } },
      { __step: { node_id: 'n1', status: 'running', started_at: '09:00' } },
      'Doanh thu ',
      { __step: { node_id: 'n1', status: 'done', ended_at: '09:01' } },
      { __result_set: { result_id: 'rs', row_count: 3 } },
      { __block: { block: 'ValueCard', props: { value: '1.2' } } },
      {
        __verification: { checks: [{ name: 'number_in_evidence', passed: true }], verdict: 'pass' },
      },
      { __lineage: { nodes: ['src', 'answer'], edges: [] } },
      'tăng',
      { __run: { run_id: 'r1', status: 'finished' } },
      '[DONE]',
    ]);
    expect(done.content).toBe('Doanh thu tăng');
    expect(done.status).toBe('done');
    expect(done.run).toEqual({ run_id: 'r1', status: 'finished', agent: 'sales' });
    expect(done.plan?.nodes).toHaveLength(2);
    expect(done.steps).toEqual({
      n1: { node_id: 'n1', status: 'done', started_at: '09:00', ended_at: '09:01' },
    });
    expect(done.blocks).toEqual([{ block: 'ValueCard', props: { value: '1.2' } }]);
    expect(done.verification?.checks[0]).toEqual({ name: 'number_in_evidence', passed: true });
    expect(done.lineage?.nodes).toEqual(['src', 'answer']);
    expect(done.resultSets).toHaveLength(1);
  });

  it('a run that ends in error marks the message as failed', () => {
    expect(run([{ __run: { run_id: 'r1', status: 'error' } }]).done.status).toBe('error');
  });

  it('ignores unknown frames, blocks outside the catalog and malformed new frames', () => {
    const { done } = run([
      'Trả lời',
      { __interrupt_v9: { kind: 'budget' } },
      { __block: { block: '<script>alert(1)</script>' } },
      { __block: { block: 'IframeEmbed', props: {} } },
      { __step: { status: 'done' } },
      { __plan: { nodes: 'not a list' } },
      { __run: { status: 'started' } },
      42,
    ]);
    expect(done).toEqual({ ...placeholder, content: 'Trả lời', status: 'done', latencyMs: 42 });
  });

  it('a stream cut in the middle of a frame shows what arrived and nothing broken', async () => {
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('data: "Giá Brent "\n\ndata: {"__block":{"block":"ValueCard","pro'));
        c.close();
      },
    });
    const events: SseEvent[] = [];
    await readSseStream(body, (e) => events.push(e));
    const state = events.reduce(streamReducer, startStream(placeholder, 'q'));
    expect(events.map((e) => e.type)).toEqual(['token', 'unknown']);
    expect(finishStream(state, 1)).toMatchObject({ content: 'Giá Brent ', status: 'done' });
  });
});
