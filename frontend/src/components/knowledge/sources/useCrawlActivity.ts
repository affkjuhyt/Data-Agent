import { useCallback, useEffect, useState } from 'react';
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
}

const isActive = (run: CrawlRun) => run.status === 'queued' || run.status === 'running';

// The crawler reports no fractional progress, so the bar tracks the stage
// (waiting / fetching / done) instead of a made-up percentage.
function toEntry(run: CrawlRun): PromotionEntry {
  const base = { data_id: run.run_id, name: run.title, from_tier: 'source', to_tier: 'bronze' } as const;
  switch (run.status) {
    case 'queued':  return { ...base, status: 'queued', progress: 0 };
    case 'running': return { ...base, status: 'processing', progress: 50, detail: 'Fetching source…' };
    case 'success': return {
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
 */
export function useCrawlActivity() {
  const [runs, setRuns] = useState<CrawlRun[]>([]);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [tracked, setTracked] = useState<Set<string>>(new Set());

  const track = useCallback((runIds: string[]) => {
    if (runIds.length === 0) return;
    setTracked(prev => (runIds.every(id => prev.has(id)) ? prev : new Set([...prev, ...runIds])));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const latest = await apiGet<CrawlRun[]>('/api/knowledge/sources/activity');
      setRuns(latest);
      track(latest.filter(isActive).map(run => run.run_id)); // watched in flight: keep it when it finishes
    } catch { /* keep what we have; the next tick retries */ }
  }, [track]);

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
    entries: visibleRuns.map(toEntry),
    runIds: new Set(runs.map(run => run.run_id)),
    activeSourceIds: new Set(runs.filter(isActive).map(run => run.source_id)),
    /** Called by Run Now / Multiple Run with the ids of the runs just queued. */
    onRunsQueued: (runIds: string[]) => { track(runIds); refresh(); },
    dismiss,
  };
}
