/**
 * A long-lived Server-Sent Events connection that comes back by itself.
 *
 * EventSource cannot send the Authorization header, so this is fetch + a
 * stream reader, with the parts of EventSource the inbox needs: `id:` /
 * `event:` / multi-line `data:` / `retry:` fields, automatic reconnect with
 * capped backoff, and `Last-Event-ID` on reconnect so the server can send what
 * was missed. The Ask stream (features/ask/sse.ts) is a one-shot POST that only
 * reads `data:` lines; it does not use this.
 */

export interface SseMessage {
  id?: string;
  /** `message` when the server sent no `event:` field */
  event: string;
  data: string;
}

export type StreamStatus = 'connecting' | 'open' | 'reconnecting' | 'unavailable';

/**
 * Incremental parser (WHATWG event-stream): feed it decoded text in any
 * chunking; it calls `onMessage` at each blank line that ends an event.
 */
export function createSseParser(
  onMessage: (message: SseMessage) => void,
  onRetry?: (ms: number) => void,
) {
  let buffer = '';
  let id: string | undefined;
  let event = '';
  let data: string[] = [];

  const dispatch = () => {
    if (data.length) onMessage({ id, event: event || 'message', data: data.join('\n') });
    event = '';
    data = [];
  };

  const line = (text: string) => {
    if (text === '') return dispatch();
    if (text.startsWith(':')) return;
    const colon = text.indexOf(':');
    const field = colon === -1 ? text : text.slice(0, colon);
    let value = colon === -1 ? '' : text.slice(colon + 1);
    if (value.startsWith(' ')) value = value.slice(1);
    if (field === 'data') data.push(value);
    else if (field === 'event') event = value;
    // The id stays for the following events until the server sends a new one.
    else if (field === 'id' && !value.includes('\0')) id = value || undefined;
    else if (field === 'retry' && /^\d+$/.test(value)) onRetry?.(Number(value));
  };

  return (chunk: string) => {
    buffer += chunk;
    for (;;) {
      const match = /\r\n|\r|\n/.exec(buffer);
      // A lone trailing \r may be the first half of \r\n: wait for the next chunk.
      if (!match || (match[0] === '\r' && match.index === buffer.length - 1)) return;
      line(buffer.slice(0, match.index));
      buffer = buffer.slice(match.index + match[0].length);
    }
  };
}

// The server answered: the stream is not there or not for this user. Retrying
// cannot change that, and every open tab would keep hammering the endpoint.
const FINAL_STATUSES = new Set([401, 403, 404]);

const defaultSleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      'abort',
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });

export interface ReconnectingStreamOptions {
  url: string;
  /** read at every (re)connect, so a refreshed token is used */
  headers?: () => Record<string, string>;
  onMessage: (message: SseMessage) => void;
  onStatus: (status: StreamStatus) => void;
  /** called when a reconnect (not the first connect) has opened */
  onReconnected?: () => void;
  baseDelayMs?: number;
  maxDelayMs?: number;
  fetchImpl?: typeof fetch;
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

/** Opens the stream and keeps it open; returns the function that closes it for good. */
export function openReconnectingStream({
  url,
  headers,
  onMessage,
  onStatus,
  onReconnected,
  baseDelayMs = 1000,
  maxDelayMs = 30_000,
  fetchImpl = (input, init) => fetch(input, init),
  sleep = defaultSleep,
}: ReconnectingStreamOptions): () => void {
  const controller = new AbortController();
  const { signal } = controller;
  let lastEventId: string | undefined;
  let serverRetryMs: number | undefined;
  let opened = false;
  let failures = 0;

  const run = async () => {
    onStatus('connecting');
    while (!signal.aborted) {
      try {
        const res = await fetchImpl(url, {
          headers: {
            Accept: 'text/event-stream',
            ...headers?.(),
            ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
          },
          cache: 'no-store',
          signal,
        });
        if (FINAL_STATUSES.has(res.status)) {
          onStatus('unavailable');
          return;
        }
        if (!res.ok || !res.body) throw new Error(`notification stream: HTTP ${res.status}`);

        const reconnected = opened;
        opened = true;
        failures = 0;
        onStatus('open');
        if (reconnected) onReconnected?.();

        const parse = createSseParser(
          (message) => {
            if (message.id) lastEventId = message.id;
            onMessage(message);
          },
          (ms) => {
            serverRetryMs = ms;
          },
        );
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parse(decoder.decode(value, { stream: true }));
        }
        // The server closed the stream: reconnect like after a drop.
      } catch {
        if (signal.aborted) return;
      }
      failures += 1;
      // Say so now, not after the wait: the user should know notifications may be late.
      onStatus('reconnecting');
      const delay = Math.min(maxDelayMs, (serverRetryMs ?? baseDelayMs) * 2 ** (failures - 1));
      await sleep(delay, signal);
    }
  };

  void run();
  return () => controller.abort();
}
