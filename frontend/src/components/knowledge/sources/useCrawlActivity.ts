import { useCallback, useEffect, useRef, useState } from 'react';
import type { PromotionEntry } from '../inventory/PromotionProgressOverlay';
import { apiGet } from '../../../lib/apiClient';

interface CrawlRun {
  run_id: string;
  source_id: string;
  title: string;
  status: 'queued' | 'running' | 'success' | 'failed';
  error: string | null;
  unchanged: boolean;
  existing_tier: string | null;
  promotion_mode: 'manual' | 'auto';
  purpose?: string;
  data_ids: string[];
}

// Training/Evaluation data ends at Silver by design (it never enters the
// knowledge base), so an auto run for it is tracked to Silver, not Gold —
// otherwise its bar would wait for a Gold stage that never happens.
const autoTargetTier = (run: CrawlRun): 'silver' | 'gold' =>
  run.purpose && run.purpose !== 'production' ? 'silver' : 'gold';

const isActive = (run: CrawlRun) => run.status === 'queued' || run.status === 'running';

// The crawler reports no fractional progress, so the bar tracks the stage
// (waiting / fetching / done) instead of a made-up percentage.
//
// An auto-promote run that actually produced a new document is NOT given a
// "done" entry here at all (returns null, filtered out below) — showing
// 100%/done the instant Bronze lands would be exactly the false completion
// this panel must not claim (Gold can take minutes of OCR/LLM work).
// useCrawlActivity instead hands it off, once, to the real Bronze->Gold
// promotion queue (usePromotionQueue's SSE-backed tracker, the same one
// Data Layers' manual "Process to Gold" uses) via onAutoPromoted — that
// queue's own entry becomes the single row the panel shows from then on,
// and only reaches 100% when the document's tier is genuinely Gold.
function toEntry(run: CrawlRun): PromotionEntry | null {
  const toGold = run.promotion_mode === 'auto';
  const base = { data_id: run.run_id, name: run.title, from_tier: 'source', to_tier: toGold ? autoTargetTier(run) : 'bronze' } as const;
  switch (run.status) {
    case 'queued':  return { ...base, status: 'queued', progress: 0 };
    case 'running': return { ...base, status: 'processing', progress: 50, detail: 'Fetching source…' };
    case 'success':
      if (toGold && !run.unchanged) return null; // handed off — see onAutoPromoted
      return {
        ...base, status: 'done', progress: 100,
        detail: run.unchanged ? `No change — kept existing ${(run.existing_tier ?? '').toUpperCase() || 'document'}` : undefined,
      };
    default:        return { ...base, status: 'failed', progress: 50, detail: run.error ? `Failed: ${run.error.slice(0, 48)}` : 'Failed' };
  }
}

/**
 * Crawl runs for the shared Pipeline Activity panel. The database is the source
 * of truth (queued / running), so the panel and the row locks stay right across
 * tab switches and reloads. A finished run is shown only if this page started
 * or watched it, like the promotion queue (which restores in-flight entries
 * only): otherwise a reload would bring back entries the user already closed.
 *
 * `onAutoPromoted(dataId, title)` is called exactly once per run, the first
 * time it's observed as a successful, changed, auto-promote crawl — the
 * caller wires this to usePromotionQueue's startPromotion(dataId, title,
 * 'source', toTier) so the same real, SSE-tracked progress bar Data Layers
 * uses takes over from here.
 */
export function useCrawlActivity(onAutoPromoted?: (dataId: string, title: string, toTier: 'silver' | 'gold') => void) {
  const [runs, setRuns] = useState<CrawlRun[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [tracked, setTracked] = useState<Set<string>>(new Set());
  const handedOffRef = useRef<Set<string>>(new Set());
  const firstLoadRef = useRef(true);

  const track = useCallback((runIds: string[]) => {
    if (runIds.length === 0) return;
    setTracked(prev => (runIds.every(id => prev.has(id)) ? prev : new Set([...prev, ...runIds])));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const latest = await apiGet<CrawlRun[]>('/api/knowledge/sources/activity');
      setRuns(latest);
      track(latest.filter(isActive).map(run => run.run_id)); // watched in flight: keep it when it finishes
      if (firstLoadRef.current) {
        // Runs already finished when the page opens were handed off (or not)
        // before this page existed. handedOffRef lives in memory, so without
        // this every reload replayed the recent successful auto runs and
        // brought back "Source > Gold" entries the user had already closed.
        firstLoadRef.current = false;
        for (const run of latest) if (!isActive(run)) handedOffRef.current.add(run.run_id);
      }
      if (onAutoPromoted) {
        for (const run of latest) {
          if (
            run.status === 'success' && run.promotion_mode === 'auto' && !run.unchanged
            && !handedOffRef.current.has(run.run_id)
          ) {
            handedOffRef.current.add(run.run_id);
            for (const dataId of run.data_ids) onAutoPromoted(dataId, run.title, autoTargetTier(run));
          }
        }
      }
    } catch { /* keep what we have; the next tick retries */ }
  }, [track, onAutoPromoted]);

  const anyActive = runs.some(isActive);
  useEffect(() => {
    refresh();
    // Fast while something is in flight, slow otherwise (one cheap query).
    const timer = setInterval(refresh, anyActive ? 3000 : 20000);
    return () => clearInterval(timer);
  }, [anyActive, refresh]);

  const dismiss = useCallback((runId: string) => {
    setDismissed(prev => new Set(prev).add(runId));
  }, []);

  const visibleRuns = runs.filter(run => !dismissed.has(run.run_id) && (isActive(run) || tracked.has(run.run_id)));
  return {
    entries: visibleRuns.map(toEntry).filter((e): e is PromotionEntry => e !== null),
    runIds: new Set(runs.map(run => run.run_id)),
    activeSourceIds: new Set(runs.filter(isActive).map(run => run.source_id)),
    /** Called by Run Now / Multiple Run with the ids of the runs just queued. */
    onRunsQueued: (runIds: string[]) => { track(runIds); refresh(); },
    dismiss,
  };
}
