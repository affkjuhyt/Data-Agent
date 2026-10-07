/**
 * A fake `text/event-stream` server for tests: each connection is a
 * ReadableStream the test writes to, closes, or breaks (a dropped network).
 */
import { vi } from 'vitest';

interface FakeConnection {
  headers: Record<string, string>;
  send: (text: string) => void;
  close: () => void;
  drop: () => void;
}

export function fakeEventStream(statuses: number[] = []) {
  const connections: FakeConnection[] = [];
  const encoder = new TextEncoder();

  const fetchImpl = vi.fn(async (_url: RequestInfo | URL, init?: RequestInit) => {
    const status = statuses.shift() ?? 200;
    if (status !== 200) return new Response(null, { status });
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start: (c) => {
        controller = c;
      },
    });
    init?.signal?.addEventListener('abort', () => {
      try {
        controller.error(new Error('aborted'));
      } catch {
        /* closed */
      }
    });
    connections.push({
      headers: { ...(init?.headers as Record<string, string>) },
      send: (text) => controller.enqueue(encoder.encode(text)),
      close: () => controller.close(),
      drop: () => controller.error(new TypeError('network error')),
    });
    return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } });
  });

  return {
    fetchImpl: fetchImpl as unknown as typeof fetch,
    connections,
    calls: fetchImpl.mock.calls,
  };
}
