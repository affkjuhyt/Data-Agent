/**
 * useV1 over TanStack Query: screens that ask for the same key share one request, the Data Space
 * is part of every key, and a loader of null waits.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setDataSpace } from './client';
import { useV1 } from './useV1';

afterEach(() => setDataSpace(null));

describe('useV1', () => {
  it('asks once for two screens that read the same key', async () => {
    const load = vi.fn(async () => ['a']);
    const first = renderHook(() => useV1(['listThings'], load));
    const second = renderHook(() => useV1(['listThings'], load));
    await waitFor(() => expect(first.result.current.data).toEqual(['a']));
    await waitFor(() => expect(second.result.current.data).toEqual(['a']));
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('asks again in another Data Space', async () => {
    const load = vi.fn(async () => ['a']);
    setDataSpace('ds-1');
    const { result, rerender } = renderHook(() => useV1(['listThings'], load));
    await waitFor(() => expect(result.current.loading).toBe(false));
    setDataSpace('ds-2');
    rerender();
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2));
  });

  it('waits while the loader is null, and reload then does nothing', async () => {
    const { result } = renderHook(() => useV1<string[]>(['listThings'], null));
    expect(result.current).toMatchObject({ data: undefined, loading: false });
    act(() => result.current.reload());
    expect(result.current.loading).toBe(false);
  });

  it('keeps the last answer while reloading and after a failure', async () => {
    let fail = false;
    const load = vi.fn(async () => {
      if (fail) throw new Error('mất kết nối');
      return ['a'];
    });
    const { result } = renderHook(() => useV1(['listThings'], load));
    await waitFor(() => expect(result.current.data).toEqual(['a']));
    fail = true;
    act(() => result.current.reload());
    await waitFor(() => expect(result.current.error?.message).toBe('mất kết nối'));
    expect(result.current.data).toEqual(['a']);
  });
});
