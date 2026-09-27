import { useState, useCallback, useRef, useEffect } from 'react';
import type { PromotionEntry } from './PromotionProgressOverlay';
import type { KnowledgeDocument } from '../../../types';
import { apiStream } from '../../../lib/apiClient';

// Persist the queue so a page refresh doesn't drop in-flight promotions. The
// overlay reappears AND `promotingIds` stays populated, so the row stays locked
// against a duplicate promote request. Only in-flight entries are restored —
// completed/failed ones were already applied to the document.
const STORAGE_KEY = 'kb-promotion-queue-v1';

// Browsers allow ~6 concurrent HTTP/1.1 connections per origin and every
// /progress stream holds one open. Restoring six stale entries used to take all
// six, so every other API call queued in the browser, hit apiGet's 5s timeout
// and the Data Layers / Source Registry lists never loaded (Kong saw nothing).
// Keep headroom for ordinary requests.
const MAX_CONCURRENT_STREAMS = 3;

// A stream that ends without a terminal event (kb-backend or Kong restarted,
// network blip) used to leave its entry at the simulated 85% forever, since
// nothing re-attached it. Reconnect a few times, then say so.
const MAX_STREAM_RETRIES = 5;
const STREAM_RETRY_BASE_MS = 3000;

function loadPersisted(): PromotionEntry[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PromotionEntry[];
    return Array.isArray(parsed)
      ? parsed.filter(e => e.status === 'queued' || e.status === 'processing')
      : [];
  } catch {
    return [];
  }
}

// Visual progress animation while we wait for the worker. These milestones ONLY move
// the bar — they never mark the promotion done and are capped below 100, so we never
// show a false "100%". Real completion comes exclusively from the pipeline (SSE) below.
// (Gold can take minutes of per-chunk LLM extraction, so a time-based "done" is wrong.)
const MILESTONES: [number, number][] = [
  [8,   500],
  [22,  2200],
  [40,  5000],
  [58,  9000],
  [72,  15000],
  [85,  26000],
];

export function usePromotionQueue(
  updateDocument: (id: string, updates: Partial<KnowledgeDocument>) => void,
) {
  const [queue, setQueue] = useState<PromotionEntry[]>(loadPersisted);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>[]>>(new Map());
  const abortRefs = useRef<Map<string, AbortController>>(new Map());
  const activeStreamsRef = useRef<Set<string>>(new Set());
  const waitingStreamsRef = useRef<Array<[string, 'silver' | 'gold']>>([]);
  const attachRef = useRef<(data_id: string, to_tier: 'silver' | 'gold') => void>(() => {});
  const retriesRef = useRef<Map<string, number>>(new Map());
  const namesRef = useRef<Map<string, string>>(new Map());
  // Synchronous lock: prevents a rapid double-click from queuing the same doc
  // twice within one tick (before React re-renders the disabled button).
  const inFlightRef = useRef<Set<string>>(new Set());

  // Persist the queue on every change so it survives a page refresh.
  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
    } catch { /* storage full / unavailable — non-fatal */ }
  }, [queue]);

  const _cleanup = (data_id: string) => {
    timersRef.current.get(data_id)?.forEach(clearTimeout);
    timersRef.current.delete(data_id);
    abortRefs.current.get(data_id)?.abort();
    abortRefs.current.delete(data_id);
    retriesRef.current.delete(data_id);
    activeStreamsRef.current.delete(data_id);
    waitingStreamsRef.current = waitingStreamsRef.current.filter(([id]) => id !== data_id);
    inFlightRef.current.delete(data_id);
  };

  const _finalise = useCallback(
    (data_id: string, to_tier: 'silver' | 'gold', failed = false, detail?: string) => {
      _cleanup(data_id);
      setQueue((prev) =>
        prev.map((e) =>
          e.data_id === data_id
            ? { ...e, progress: failed ? e.progress : 100, status: failed ? 'failed' : 'done', detail: detail ?? e.detail }
            : e,
        ),
      );
      if (!failed) {
        updateDocument(data_id, {
          current_tier: to_tier,
          status: to_tier === 'gold' ? 'PUBLISHED' : 'EMBEDDING',
        });
      }
    },
    [updateDocument],
  );

  // ── Real progress + completion via the pipeline SSE ───────────────────────
  // The worker's terminal log status is EMBEDDING for silver (it then queues gold)
  // and PUBLISHED for gold; the /progress stream only sets `done` on PUBLISHED/FAILED,
  // so silver completion is recognised from the status string. This keeps the entry
  // "in flight" for the entire real run, so the bar never jumps to 100% early. The
  // stream reads server-side state (kb_ingestion_logs), so re-attaching after a page
  // refresh resumes mid-flight and finalises correctly even if the worker is done.
  const _attachStream = useCallback(
    (data_id: string, to_tier: 'silver' | 'gold') => {
      if (abortRefs.current.has(data_id)) return; // already streaming
      if (activeStreamsRef.current.size >= MAX_CONCURRENT_STREAMS) {
        // Newest first: a promotion the user just started beats restored ones.
        if (!waitingStreamsRef.current.some(([id]) => id === data_id)) {
          waitingStreamsRef.current.unshift([data_id, to_tier]);
        }
        return;
      }
      const abort = new AbortController();
      abortRefs.current.set(data_id, abort);
      activeStreamsRef.current.add(data_id);

      // Routed through apiStream (not a bare fetch) so it carries the bearer
      // token and goes through Kong like every other KB request.
      let ended = false; // a terminal event was handled (the entry is finalised or dropped)
      const retryLater = () => {
        if (ended || abort.signal.aborted) return;
        const attempt = retriesRef.current.get(data_id) ?? 0;
        if (attempt >= MAX_STREAM_RETRIES) {
          _finalise(data_id, to_tier, true, 'Lost connection to the progress stream');
          return;
        }
        retriesRef.current.set(data_id, attempt + 1);
        // Free the slot first so attach() does not see itself as "already streaming".
        abortRefs.current.delete(data_id);
        setTimeout(() => attachRef.current(data_id, to_tier), STREAM_RETRY_BASE_MS * (attempt + 1));
      };

      const name = namesRef.current.get(data_id);
      const query = name ? `?name=${encodeURIComponent(name)}` : '';
      apiStream(`/api/pipeline/ingestion/documents/${data_id}/progress${query}`, { signal: abort.signal })
        .then(async (res) => {
          if (!res.ok || !res.body) return retryLater();
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buf = '';

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += decoder.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop() ?? '';

            for (const line of lines) {
              if (!line.startsWith('data: ')) continue;
              try {
                const data = JSON.parse(line.slice(6)) as {
                  status?: string; done?: boolean; message?: string; superseded?: boolean; replaced_by?: string | null;
                  progress_pct?: number; processed_chunks?: number; total_chunks?: number;
                };

                if (data.superseded) {
                  ended = true;
                  const successor = data.replaced_by;
                  _cleanup(data_id);
                  if (successor) {
                    // A re-crawl replaced this document: what runs now is the new
                    // run that replaces the old one, so follow it under its new id.
                    if (name) namesRef.current.set(successor, name);
                    inFlightRef.current.add(successor);
                    setQueue((prev) =>
                      prev.some((e) => e.data_id === successor)
                        ? prev.filter((e) => e.data_id !== data_id)
                        : prev.map((e) =>
                            e.data_id === data_id
                              ? { ...e, data_id: successor, progress: 0, status: 'processing', detail: undefined }
                              : e,
                          ),
                    );
                    attachRef.current(successor, to_tier);
                  } else {
                    // Deleted with no replacement: nothing left to follow.
                    setQueue((prev) => prev.filter((e) => e.data_id !== data_id));
                  }
                  return;
                }
                if (data.status === 'FAILED') {
                  ended = true;
                  // Surfaces e.g. "Silver stage skipped — document was
                  // deleted while processing" instead of a bare "Failed"
                  // that looks like a real pipeline error.
                  _finalise(data_id, to_tier, true, data.message);
                  return;
                }
                const reachedTerminal =
                  data.done === true ||
                  (to_tier === 'gold' && data.status === 'PUBLISHED') ||
                  (to_tier === 'silver' && data.status === 'EMBEDDING');
                if (reachedTerminal) {
                  ended = true;
                  _finalise(data_id, to_tier);
                  return;
                }

                if (data.progress_pct != null) {
                  const pct = Math.min(data.progress_pct, 97);
                  setQueue((prev) =>
                    prev.map((e) =>
                      e.data_id === data_id && e.status !== 'done' && e.status !== 'failed'
                        ? { ...e, progress: Math.max(e.progress, pct), status: 'processing' }
                        : e,
                    ),
                  );
                }
              } catch { /* ignore parse errors */ }
            }
          }
          retryLater(); // the server closed the stream before any terminal event
        })
        .catch(() => retryLater())
        .finally(() => {
          activeStreamsRef.current.delete(data_id);
          const next = waitingStreamsRef.current.shift();
          if (next) attachRef.current(next[0], next[1]);
        });
    },
    [_finalise],
  );
  attachRef.current = _attachStream;

  const startPromotion = useCallback(
    (data_id: string, name: string, from_tier: 'source' | 'bronze' | 'silver', to_tier: 'silver' | 'gold') => {
      // Lock: never queue the same document twice while a promotion is in flight.
      if (inFlightRef.current.has(data_id)) return;
      inFlightRef.current.add(data_id);

      namesRef.current.set(data_id, name);
      const entry: PromotionEntry = { data_id, name, from_tier, to_tier, progress: 0, status: 'queued' };
      setQueue((prev) => [...prev.filter((e) => e.data_id !== data_id), entry]);

      // ── Animation only — capped, never finalises ────────────────────────────
      const timers: ReturnType<typeof setTimeout>[] = [];
      MILESTONES.forEach(([pct, delay]) => {
        const t = setTimeout(() => {
          setQueue((prev) =>
            prev.map((e) => {
              if (e.data_id !== data_id || e.status === 'failed' || e.status === 'done') return e;
              return { ...e, progress: Math.max(e.progress, pct), status: 'processing' };
            }),
          );
        }, delay);
        timers.push(t);
      });
      timersRef.current.set(data_id, timers);

      _attachStream(data_id, to_tier);
    },
    [_attachStream],
  );

  // Rehydrate after a refresh: re-attach the progress stream for every in-flight
  // entry restored from localStorage so the overlay keeps moving and finalises.
  const rehydratedRef = useRef(false);
  useEffect(() => {
    if (rehydratedRef.current) return;
    rehydratedRef.current = true;
    queue.forEach((e) => {
      // `to_tier` is typed 'bronze' | 'silver' | 'gold' on the shared PromotionEntry
      // (useCrawlActivity's Source→Bronze entries use 'bronze'), but this hook's own
      // queue only ever holds the silver/gold promotions it created via
      // startPromotion — narrow explicitly rather than widen _attachStream's type.
      if ((e.status === 'queued' || e.status === 'processing') && (e.to_tier === 'silver' || e.to_tier === 'gold')) {
        inFlightRef.current.add(e.data_id);
        namesRef.current.set(e.data_id, e.name);
        _attachStream(e.data_id, e.to_tier);
      }
    });
  }, [queue, _attachStream]);

  const dismissEntry = useCallback((data_id: string) => {
    _cleanup(data_id);
    setQueue((prev) => prev.filter((e) => e.data_id !== data_id));
  }, []);

  return { queue, startPromotion, dismissEntry };
}
