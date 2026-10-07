import { describe, expect, it } from 'vitest';
import { parseSseData, readSseStream, type SseEvent } from './sse';

describe('parseSseData', () => {
  it('maps every control frame the backend emits', () => {
    expect(parseSseData('"hello"')).toEqual({ type: 'token', text: 'hello' });
    expect(parseSseData('{"__session_id":"s1"}')).toEqual({ type: 'session', sessionId: 's1' });
    expect(parseSseData('{"__reset":true}')).toEqual({ type: 'reset' });
    expect(parseSseData('{"__clarification":{"session_id":"s1","questions":["Which period?"]}}'))
      .toEqual({ type: 'clarification', sessionId: 's1', questions: ['Which period?'], details: [{ text: 'Which period?' }] });
    expect(parseSseData('{"__pending_review":{"conv_id":"c1","payload":{"title":"t"}}}'))
      .toEqual({ type: 'pending_review', convId: 'c1', payload: { title: 't' } });
    expect(parseSseData('{"__pending_review":"c2"}'))
      .toEqual({ type: 'pending_review', convId: 'c2', payload: null });
    expect(parseSseData('{"__chart":[{"chart_type":"bar"}]}').type).toBe('chart');
    expect(parseSseData('{"__result_set":{"result_id":"abc","rows_preview":[]}}')).toEqual({
      type: 'result_set', resultSet: { result_id: 'abc', rows_preview: [] },
    });
    expect(parseSseData('{"__citations":[{"index":1}]}').type).toBe('citations');
    expect(parseSseData('{"__suggestions":["a","b"]}')).toEqual({ type: 'suggestions', suggestions: ['a', 'b'] });
    expect(parseSseData('{"__options":["This month"]}')).toEqual({ type: 'options', options: ['This month'] });
    expect(parseSseData('{"__conv_ids":[{"conv_id":"c"}]}').type).toBe('conv_ids');
    expect(parseSseData('[DONE]')).toEqual({ type: 'done' });
    expect(parseSseData('[ERROR] broken')).toEqual({ type: 'error', message: 'broken' });
    expect(parseSseData('[ERROR]')).toEqual({ type: 'error', message: 'An error occurred, please try again.' });
  });

  it('reads a refusal and keeps [ERROR] for the uncoded failure', () => {
    const ev = parseSseData(JSON.stringify({ __refusal: {
      code: 'AGENT_CONFIG_ERROR',
      message: 'Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.',
      next_action: 'retry',
      detail_id: 'conv-7',
    } }));
    expect(ev).toEqual({
      type: 'refusal',
      refusal: {
        code: 'AGENT_CONFIG_ERROR',
        message: 'Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.',
        next_action: 'retry',
        detail_id: 'conv-7',
      },
    });
    expect(parseSseData('[ERROR] broken').type).toBe('error');
  });

  it('keeps the options of each clarification question', () => {
    const ev = parseSseData(JSON.stringify({ __clarification: {
      session_id: 's1',
      questions: ['Which branch?'],
      details: [{ text: 'Which branch?', options: ['Hanoi', 'HCMC'] }, { options: ['no text'] }],
    } }));
    expect(ev).toEqual({
      type: 'clarification',
      sessionId: 's1',
      questions: ['Which branch?'],
      details: [{ text: 'Which branch?', options: ['Hanoi', 'HCMC'] }],
    });
  });

  it('normalises a partial evidence frame into the full shape', () => {
    const ev = parseSseData('{"__evidence":{"runs":[{"sql":"SELECT 1","columns":[],"rows":[],"generated_by":"llm","kind":"sql"}]}}');
    expect(ev).toEqual({
      type: 'evidence',
      evidence: {
        runs: [{ sql: 'SELECT 1', columns: [], rows: [], generated_by: 'llm', kind: 'sql' }],
        clarifications: [],
        warnings: [],
      },
    });
  });

  it('never throws on garbage and keeps raw text visible', () => {
    expect(parseSseData('not json')).toEqual({ type: 'token', text: 'not json' });
    expect(parseSseData('42')).toEqual({ type: 'unknown', raw: 42 });
    expect(parseSseData('{"__something_new":1}').type).toBe('unknown');
  });
});

function streamOf(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

describe('readSseStream', () => {
  it('reassembles frames split across chunks and stops at [DONE]', async () => {
    const events: SseEvent[] = [];
    await readSseStream(
      streamOf([
        'data: {"__session_id":"s1"}\n\ndata: "Doanh ',
        'thu"\n\ndata: {"__evi',
        'dence":{"runs":[]}}\n\ndata: [DONE]\n\ndata: "never"\n\n',
      ]),
      e => events.push(e),
    );
    expect(events.map(e => e.type)).toEqual(['session', 'token', 'evidence', 'done']);
    expect((events[1] as { text: string }).text).toBe('Doanh thu');
  });

  it('flushes a trailing frame without a newline', async () => {
    const events: SseEvent[] = [];
    await readSseStream(streamOf(['data: "last"']), e => events.push(e));
    expect(events).toEqual([{ type: 'token', text: 'last' }]);
  });

  it('ignores comment / blank lines and CRLF', async () => {
    const events: SseEvent[] = [];
    await readSseStream(streamOf([': ping\r\n\r\ndata: "ok"\r\n\r\n']), e => events.push(e));
    expect(events).toEqual([{ type: 'token', text: 'ok' }]);
  });
});
