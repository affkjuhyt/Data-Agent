import { describe, expect, it, vi } from 'vitest';
import { fakeEventStream } from '../test-utils/fakeEventStream';
import {
  createSseParser,
  openReconnectingStream,
  type SseMessage,
  type StreamStatus,
} from './sseClient';

describe('createSseParser', () => {
  it('reads id / event / multi-line data / retry in any chunking', () => {
    const messages: SseMessage[] = [];
    const retries: number[] = [];
    const feed = createSseParser(
      (m) => messages.push(m),
      (ms) => retries.push(ms),
    );
    for (const chunk of [
      ': ping\n\nretry: 2500\nid: e1\nevent: review.case.created\ndata: {"a":',
      '1}\ndata: {"b":2}\r',
      '\n\r\ndata: plain\n\n',
    ]) {
      feed(chunk);
    }
    expect(retries).toEqual([2500]);
    expect(messages).toEqual([
      { id: 'e1', event: 'review.case.created', data: '{"a":1}\n{"b":2}' },
      // The id carries over until the server sends another; no event field → "message".
      { id: 'e1', event: 'message', data: 'plain' },
    ]);
  });

  it('does not dispatch an event that has not ended yet', () => {
    const messages: SseMessage[] = [];
    const feed = createSseParser((m) => messages.push(m));
    feed('id: e2\ndata: half');
    expect(messages).toEqual([]);
  });
});

const flushUntil = (check: () => void) => vi.waitFor(check, { timeout: 2000, interval: 5 });

describe('openReconnectingStream', () => {
  it('reconnects after a drop, sends Last-Event-ID and reports each state', async () => {
    const server = fakeEventStream();
    const statuses: StreamStatus[] = [];
    const messages: SseMessage[] = [];
    const onReconnected = vi.fn();
    const close = openReconnectingStream({
      url: 'http://inbox.test/stream',
      headers: () => ({ Authorization: 'Bearer t' }),
      onMessage: (m) => messages.push(m),
      onStatus: (s) => statuses.push(s),
      onReconnected,
      fetchImpl: server.fetchImpl,
      sleep: () => Promise.resolve(),
    });

    await flushUntil(() => expect(server.connections).toHaveLength(1));
    expect(server.connections[0].headers).toMatchObject({
      Authorization: 'Bearer t',
      Accept: 'text/event-stream',
    });
    expect(server.connections[0].headers['Last-Event-ID']).toBeUndefined();
    server.connections[0].send('id: e1\nevent: review.case.created\ndata: {}\n\n');
    await flushUntil(() => expect(messages).toHaveLength(1));

    server.connections[0].drop();
    await flushUntil(() => expect(server.connections).toHaveLength(2));
    expect(server.connections[1].headers['Last-Event-ID']).toBe('e1');
    await flushUntil(() => expect(onReconnected).toHaveBeenCalledTimes(1));
    expect(statuses).toEqual(['connecting', 'open', 'reconnecting', 'open']);
    close();
  });

  it('reconnects when the server ends the stream too', async () => {
    const server = fakeEventStream();
    const close = openReconnectingStream({
      url: 'u',
      onMessage: () => {},
      onStatus: () => {},
      sleep: () => Promise.resolve(),
      fetchImpl: server.fetchImpl,
    });
    await flushUntil(() => expect(server.connections).toHaveLength(1));
    server.connections[0].close();
    await flushUntil(() => expect(server.connections).toHaveLength(2));
    close();
  });

  it('backs off exponentially up to a cap, and honours the server retry', async () => {
    const delays: number[] = [];
    let close = () => {};
    const failing = vi.fn().mockRejectedValue(new TypeError('offline'));
    close = openReconnectingStream({
      url: 'u',
      onMessage: () => {},
      onStatus: () => {},
      fetchImpl: failing,
      baseDelayMs: 1000,
      maxDelayMs: 5000,
      sleep: (ms) => {
        delays.push(ms);
        if (delays.length === 5) close();
        return Promise.resolve();
      },
    });
    await flushUntil(() => expect(delays).toHaveLength(5));
    expect(delays).toEqual([1000, 2000, 4000, 5000, 5000]);
  });

  it.each([401, 403, 404])(
    'stops for good on HTTP %i (no stream for this user)',
    async (status) => {
      const server = fakeEventStream([status]);
      const statuses: StreamStatus[] = [];
      openReconnectingStream({
        url: 'u',
        onMessage: () => {},
        onStatus: (s) => statuses.push(s),
        sleep: () => Promise.resolve(),
        fetchImpl: server.fetchImpl,
      });
      await flushUntil(() => expect(statuses).toEqual(['connecting', 'unavailable']));
      expect(server.calls).toHaveLength(1);
    },
  );

  it('retries a 5xx', async () => {
    const server = fakeEventStream([503]);
    const close = openReconnectingStream({
      url: 'u',
      onMessage: () => {},
      onStatus: () => {},
      sleep: () => Promise.resolve(),
      fetchImpl: server.fetchImpl,
    });
    await flushUntil(() => expect(server.connections).toHaveLength(1));
    expect(server.calls).toHaveLength(2);
    close();
  });
});
