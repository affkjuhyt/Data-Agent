/**
 * Parser for the orchestrator's SSE stream.
 *
 * The stream is a sequence of `data: <json>` lines. A JSON string is an answer
 * token; a JSON object with a single `__name` key is a control frame. Both the
 * Playground and the Ask screen read the same stream, so the frame → event
 * mapping lives here once. `readSseStream` also buffers partial lines across
 * network chunks — a frame split across two chunks used to be parsed as two
 * broken pieces of text.
 */
import type { ChartSpec, Citation, Evidence } from './types';

export type SseEvent =
  | { type: 'token'; text: string }
  // The answer streamed so far was withdrawn (rejected by the judge or the
  // output guardrail); the text that replaces it follows as tokens.
  | { type: 'reset' }
  | { type: 'session'; sessionId: string }
  | { type: 'clarification'; sessionId: string; questions: string[] }
  | { type: 'pending_review'; convId: string; payload: unknown }
  | { type: 'chart'; specs: ChartSpec[] }
  | { type: 'citations'; citations: Citation[] }
  | { type: 'suggestions'; suggestions: string[] }
  | { type: 'options'; options: string[] }
  | { type: 'evidence'; evidence: Evidence }
  | { type: 'conv_ids'; convs: Array<{ conv_id: string; title?: string; agent_name?: string }> }
  | { type: 'error'; message: string }
  | { type: 'done' }
  | { type: 'unknown'; raw: unknown };

const DEFAULT_ERROR = 'An error occurred, please try again.';

/** One `data:` payload → a typed event. Never throws. */
export function parseSseData(raw: string): SseEvent {
  const data = raw.trim();
  if (data === '[DONE]') return { type: 'done' };
  if (data.startsWith('[ERROR]')) {
    return { type: 'error', message: data.slice(7).trim() || DEFAULT_ERROR };
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    // Not JSON — the backend only ever sends JSON, but a raw chunk is still
    // text the user should see rather than a silent drop.
    return { type: 'token', text: raw };
  }

  if (typeof parsed === 'string') return { type: 'token', text: parsed };
  if (!parsed || typeof parsed !== 'object') return { type: 'unknown', raw: parsed };

  const obj = parsed as Record<string, unknown>;

  if (typeof obj.__session_id === 'string') return { type: 'session', sessionId: obj.__session_id };
  if (obj.__reset === true) return { type: 'reset' };

  if (obj.__clarification && typeof obj.__clarification === 'object') {
    const c = obj.__clarification as { session_id?: string; questions?: unknown };
    return {
      type: 'clarification',
      sessionId: c.session_id ?? '',
      questions: Array.isArray(c.questions) ? c.questions.map(String) : [],
    };
  }

  if (obj.__pending_review !== undefined) {
    const pr = obj.__pending_review as { conv_id?: string; payload?: unknown } | string;
    return typeof pr === 'string'
      ? { type: 'pending_review', convId: pr, payload: null }
      : { type: 'pending_review', convId: pr?.conv_id ?? '', payload: pr?.payload ?? null };
  }

  if (Array.isArray(obj.__chart)) return { type: 'chart', specs: obj.__chart as ChartSpec[] };
  if (Array.isArray(obj.__citations)) return { type: 'citations', citations: obj.__citations as Citation[] };
  if (Array.isArray(obj.__suggestions)) return { type: 'suggestions', suggestions: obj.__suggestions.map(String) };
  if (Array.isArray(obj.__options)) return { type: 'options', options: obj.__options.map(String) };
  if (Array.isArray(obj.__conv_ids)) return { type: 'conv_ids', convs: obj.__conv_ids as SseEvent extends { convs: infer C } ? C : never };

  if (obj.__evidence && typeof obj.__evidence === 'object') {
    const ev = obj.__evidence as Partial<Evidence>;
    return {
      type: 'evidence',
      evidence: {
        runs: Array.isArray(ev.runs) ? ev.runs : [],
        clarifications: Array.isArray(ev.clarifications) ? ev.clarifications : [],
        warnings: Array.isArray(ev.warnings) ? ev.warnings : [],
      },
    };
  }

  return { type: 'unknown', raw: parsed };
}

/**
 * Read a `text/event-stream` body to the end, calling `onEvent` per frame.
 * Resolves after `[DONE]` or when the body closes. Lines are buffered so a
 * frame split across chunks is reassembled before parsing.
 */
export async function readSseStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  const handleLine = (line: string): boolean => {
    if (!line.startsWith('data:')) return false;
    const payload = line.startsWith('data: ') ? line.slice(6) : line.slice(5);
    const event = parseSseData(payload);
    onEvent(event);
    return event.type === 'done';
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl = buffer.indexOf('\n');
      while (nl !== -1) {
        const line = buffer.slice(0, nl).replace(/\r$/, '');
        buffer = buffer.slice(nl + 1);
        if (handleLine(line)) return;
        nl = buffer.indexOf('\n');
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) handleLine(buffer.replace(/\r$/, ''));
  } finally {
    try { reader.releaseLock(); } catch { /* already released */ }
  }
}
