/**
 * Restoring stale in-flight promotions after a refresh used to open one /progress
 * stream each. Six of them exhausted the browser's per-origin connection limit,
 * so every other API call timed out before reaching Kong and the Data Layers
 * and Source Registry lists stayed empty.
 */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const apiStream = vi.fn();
vi.mock('../../../lib/apiClient', () => ({ apiStream: (...a: unknown[]) => apiStream(...a) }));

import { usePromotionQueue } from './usePromotionQueue';

const entry = (id: string) => ({
  data_id: id, name: id, from_tier: 'bronze', to_tier: 'gold', progress: 85, status: 'processing',
});

describe('usePromotionQueue stream cap', () => {
  beforeEach(() => {
    apiStream.mockReset();
    apiStream.mockImplementation(() => new Promise(() => { /* stream stays open */ }));
    localStorage.clear();
  });
  afterEach(() => localStorage.clear());

  it('never holds more than 3 progress streams open, however many entries are restored', () => {
    localStorage.setItem(
      'kb-promotion-queue-v1',
      JSON.stringify(['a', 'b', 'c', 'd', 'e', 'f'].map(entry)),
    );
    renderHook(() => usePromotionQueue(() => {}));
    expect(apiStream).toHaveBeenCalledTimes(3);
  });

  it('starts the next waiting stream when an open one ends', async () => {
    localStorage.setItem(
      'kb-promotion-queue-v1',
      JSON.stringify(['a', 'b', 'c', 'd'].map(entry)),
    );
    let closeFirst: (r: unknown) => void = () => {};
    apiStream.mockImplementationOnce(() => new Promise((resolve) => { closeFirst = resolve; }));
    renderHook(() => usePromotionQueue(() => {}));
    expect(apiStream).toHaveBeenCalledTimes(3);

    closeFirst({ ok: false, body: null });
    await vi.waitFor(() => expect(apiStream).toHaveBeenCalledTimes(4));
  });
});


function sseResponse(events: object[]) {
  const chunks = events.map((e) => new TextEncoder().encode(`data: ${JSON.stringify(e)}\n\n`));
  return {
    ok: true,
    body: {
      getReader: () => ({
        read: async () => (chunks.length ? { done: false, value: chunks.shift() } : { done: true, value: undefined }),
      }),
    },
  };
}

describe('usePromotionQueue stream lifecycle', () => {
  beforeEach(() => {
    apiStream.mockReset();
    localStorage.clear();
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
    localStorage.clear();
  });

  const seed = (ids: string[]) =>
    localStorage.setItem('kb-promotion-queue-v1', JSON.stringify(ids.map(entry)));

  it('drops an entry whose document was deleted with no replacement', async () => {
    seed(['a']);
    apiStream.mockResolvedValue(sseResponse([{ status: 'FAILED', done: true, superseded: true, message: 'gone' }]));
    const { result } = renderHook(() => usePromotionQueue(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.queue).toEqual([]);
  });

  it('follows the replacing run under its new id and keeps showing it as running', async () => {
    seed(['a']);
    apiStream
      .mockResolvedValueOnce(sseResponse([{ status: 'FAILED', done: true, superseded: true, replaced_by: 'b' }]))
      .mockImplementation(() => new Promise(() => { /* the new run is still going */ }));
    const { result } = renderHook(() => usePromotionQueue(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(result.current.queue.map((e) => [e.data_id, e.status])).toEqual([['b', 'processing']]);
    expect(String(apiStream.mock.calls[1][0])).toContain('/documents/b/progress');
  });

  it('re-attaches a stream the server closed without a terminal event', async () => {
    seed(['a']);
    apiStream
      .mockResolvedValueOnce(sseResponse([{ status: 'RUNNING', progress_pct: 40 }])) // ends, no terminal
      .mockResolvedValueOnce(sseResponse([{ status: 'PUBLISHED', done: true }]));
    const { result } = renderHook(() => usePromotionQueue(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(10); });
    expect(apiStream).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(3500); });
    expect(apiStream).toHaveBeenCalledTimes(2);
    expect(result.current.queue[0].status).toBe('done');
  });

  it('gives up with a visible failure after repeated dropped connections', async () => {
    seed(['a']);
    apiStream.mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => usePromotionQueue(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(result.current.queue[0].status).toBe('failed');
    expect(result.current.queue[0].detail).toMatch(/Lost connection/);
  });
});
