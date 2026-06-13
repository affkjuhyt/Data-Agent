import { useState, useCallback, useRef } from 'react';
import type { PromotionEntry } from './PromotionProgressOverlay';
import type { KnowledgeDocument } from '../../../types';

const BASE = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:8000';

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
  const [queue, setQueue] = useState<PromotionEntry[]>([]);
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>[]>>(new Map());
  const abortRefs = useRef<Map<string, AbortController>>(new Map());

  const _cleanup = (data_id: string) => {
    timersRef.current.get(data_id)?.forEach(clearTimeout);
    timersRef.current.delete(data_id);
    abortRefs.current.get(data_id)?.abort();
    abortRefs.current.delete(data_id);
  };

  const _finalise = useCallback(
    (data_id: string, to_tier: 'silver' | 'gold', failed = false) => {
      _cleanup(data_id);
      setQueue((prev) =>
        prev.map((e) =>
          e.data_id === data_id
            ? { ...e, progress: failed ? e.progress : 100, status: failed ? 'failed' : 'done' }
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

  const startPromotion = useCallback(
    (data_id: string, name: string, from_tier: 'bronze' | 'silver', to_tier: 'silver' | 'gold') => {
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

      // ── Real progress + completion via the pipeline SSE ─────────────────────
      // The worker's terminal log status is EMBEDDING for silver (it then queues gold)
      // and PUBLISHED for gold; the /progress stream only sets `done` on PUBLISHED/FAILED,
      // so silver completion is recognised from the status string. This keeps the entry
      // "in flight" for the entire real run, so the bar never jumps to 100% early.
      const abort = new AbortController();
      abortRefs.current.set(data_id, abort);

      fetch(`${BASE}/api/pipeline/ingestion/documents/${data_id}/progress`, { signal: abort.signal })
        .then(async (res) => {
          if (!res.ok || !res.body) return;
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
                  status?: string; done?: boolean; message?: string;
                  progress_pct?: number; processed_chunks?: number; total_chunks?: number;
                };

                if (data.status === 'FAILED') {
                  _finalise(data_id, to_tier, true);
                  return;
                }
                const reachedTerminal =
                  data.done === true ||
                  (to_tier === 'gold' && data.status === 'PUBLISHED') ||
                  (to_tier === 'silver' && data.status === 'EMBEDDING');
                if (reachedTerminal) {
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
        })
        .catch(() => { /* SSE unavailable — the bar holds at the simulated cap */ });
    },
    [_finalise],
  );

  const dismissEntry = useCallback((data_id: string) => {
    _cleanup(data_id);
    setQueue((prev) => prev.filter((e) => e.data_id !== data_id));
  }, []);

  return { queue, startPromotion, dismissEntry };
}
