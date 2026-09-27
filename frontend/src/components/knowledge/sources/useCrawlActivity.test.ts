/**
 * Reloading the page must not bring back Pipeline Activity entries the user
 * already closed: the hand-off of a successful auto crawl to the promotion
 * queue happens once per run, not once per page load.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();
vi.mock('../../../lib/apiClient', () => ({ apiGet: (...a: unknown[]) => apiGet(...a) }));

import { useCrawlActivity } from './useCrawlActivity';

const run = (id: string, status: string) => ({
  run_id: id, source_id: `s-${id}`, title: `Source ${id}`, status, error: null, unchanged: false,
  existing_tier: null, promotion_mode: 'auto', data_ids: [`d-${id}`],
});

describe('useCrawlActivity hand-off', () => {
  beforeEach(() => { apiGet.mockReset(); vi.useFakeTimers(); });
  afterEach(() => vi.useRealTimers());

  it('does not replay runs that were already finished when the page opened', async () => {
    apiGet.mockResolvedValue([run('1', 'success'), run('2', 'success')]);
    const onAutoPromoted = vi.fn();
    renderHook(() => useCrawlActivity(onAutoPromoted));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(onAutoPromoted).not.toHaveBeenCalled();
  });

  it('hands off a run that finishes while the page is open, exactly once', async () => {
    apiGet.mockResolvedValue([run('1', 'success'), run('2', 'running')]);
    const onAutoPromoted = vi.fn();
    renderHook(() => useCrawlActivity(onAutoPromoted));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(onAutoPromoted).not.toHaveBeenCalled();

    apiGet.mockResolvedValue([run('1', 'success'), run('2', 'success')]);
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(onAutoPromoted).toHaveBeenCalledTimes(1);
    expect(onAutoPromoted).toHaveBeenCalledWith('d-2', 'Source 2', 'gold');
  });

  it('tracks a Training source to Silver, since it never reaches Gold', async () => {
    apiGet.mockResolvedValue([{ ...run('1', 'running'), purpose: 'training' }]);
    const onAutoPromoted = vi.fn();
    const { result } = renderHook(() => useCrawlActivity(onAutoPromoted));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.entries[0].to_tier).toBe('silver');

    apiGet.mockResolvedValue([{ ...run('1', 'success'), purpose: 'training' }]);
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(onAutoPromoted).toHaveBeenCalledWith('d-1', 'Source 1', 'silver');
  });
});
