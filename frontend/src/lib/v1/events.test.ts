/** v1Events reads a short Server-Sent Events stream the way the SSE spec says (kernel/sse.py). */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { v1Events } from './client';

let body = '';
let lastEventId: string | null = null;
const server = setupServer(
  http.get('*/v1/questions/q1/stream', ({ request }) => {
    lastEventId = request.headers.get('Last-Event-ID');
    return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream' } });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('v1Events', () => {
  it('reads named events with their ids and joins multi-line data', async () => {
    body =
      'id: 1\nevent: step\ndata: {"a":1}\n\nid: 2\nevent: answer\ndata: line one\ndata: line two\n\n';
    expect(await v1Events('/questions/q1/stream', { lastEventId: '0' })).toEqual([
      { id: '1', event: 'step', data: '{"a":1}' },
      { id: '2', event: 'answer', data: 'line one\nline two' },
    ]);
    expect(lastEventId).toBe('0');
  });

  it('skips the keep-alive comments the server sends between events', async () => {
    // kernel/sse.py HEARTBEAT: a comment, not an event (it used to come back as an empty message).
    body = ': keep-alive\n\nid: 3\nevent: step\ndata: x\n\n: keep-alive\n\n';
    expect(await v1Events('/questions/q1/stream')).toEqual([{ id: '3', event: 'step', data: 'x' }]);
  });

  it('takes one space after the colon, keeps the rest, and reads CR line endings', async () => {
    body = 'event: step\rdata:   indented\r\rdata:no-space\r\n\r\n';
    expect(await v1Events('/questions/q1/stream')).toEqual([
      { id: undefined, event: 'step', data: '  indented' },
      { id: undefined, event: 'message', data: 'no-space' },
    ]);
  });
});
