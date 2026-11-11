/**
 * useV1 over TanStack Query: screens that ask for the same key share one request, the Data Space
 * is part of every key, and a loader of null waits. useV1Mutation: pending and error follow the
 * write, and a success refetches the queries under the prefixes it invalidates — in this Data
 * Space only.
 */
import { renderHook, waitFor } from '@testing-library/react';
import { act } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setDataSpace, V1Error } from './client';
import { useV1, useV1Mutation } from './useV1';

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

describe('useV1Mutation', () => {
  it('is pending while the write runs and refetches the invalidated prefixes after it', async () => {
    let things = ['a'];
    const listThings = vi.fn(async () => things);
    const listOther = vi.fn(async () => ['x']);
    const reader = renderHook(() => ({
      things: useV1(['listThings', 'page-1'], listThings),
      other: useV1(['listOther'], listOther),
    }));
    await waitFor(() => expect(reader.result.current.things.data).toEqual(['a']));
    await waitFor(() => expect(reader.result.current.other.data).toEqual(['x']));

    let finish: () => void = () => undefined;
    const add = vi.fn(
      (name: string) =>
        new Promise<string>((resolve) => {
          finish = () => {
            things = [...things, name];
            resolve(name);
          };
        }),
    );
    const onSuccess = vi.fn();
    const { result } = renderHook(() =>
      useV1Mutation(add, { invalidates: [['listThings']], onSuccess }),
    );
    act(() => result.current.run('b'));
    await waitFor(() => expect(result.current.pending).toBe(true));
    expect(result.current.variables).toBe('b');
    await act(async () => finish());
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(onSuccess).toHaveBeenCalledWith('b', 'b');
    expect(result.current.data).toBe('b');
    await waitFor(() => expect(reader.result.current.things.data).toEqual(['a', 'b']));
    expect(listThings).toHaveBeenCalledTimes(2);
    expect(listOther).toHaveBeenCalledTimes(1);
  });

  it('keeps a failure in error, clears it on the next run, and does not invalidate', async () => {
    const listThings = vi.fn(async () => ['a']);
    const reader = renderHook(() => useV1(['listThings'], listThings));
    await waitFor(() => expect(reader.result.current.data).toEqual(['a']));
    let fail = true;
    const save = vi.fn(async () => {
      if (fail) throw new V1Error(409, 'conflict', 'Đã có người sửa trước.');
      return 'ok';
    });
    const { result } = renderHook(() => useV1Mutation(save, { invalidates: [['listThings']] }));
    act(() => result.current.run());
    await waitFor(() => expect(result.current.error?.message).toBe('Đã có người sửa trước.'));
    expect(result.current.error).toBeInstanceOf(V1Error);
    expect(listThings).toHaveBeenCalledTimes(1);
    fail = false;
    act(() => result.current.run());
    await waitFor(() => expect(result.current.data).toBe('ok'));
    expect(result.current.error).toBeUndefined();
    await waitFor(() => expect(listThings).toHaveBeenCalledTimes(2));
    act(() => result.current.reset());
    await waitFor(() => expect(result.current.data).toBeUndefined());
  });

  it('rejects from runAsync', async () => {
    const { result } = renderHook(() =>
      useV1Mutation(async () => {
        throw new Error('hỏng');
      }),
    );
    await act(async () => {
      await expect(result.current.runAsync()).rejects.toThrow('hỏng');
    });
    await waitFor(() => expect(result.current.error?.message).toBe('hỏng'));
  });

  it('invalidates only in the current Data Space', async () => {
    const listThings = vi.fn(async () => ['a']);
    setDataSpace('ds-1');
    const reader = renderHook(() => useV1(['listThings'], listThings));
    await waitFor(() => expect(reader.result.current.data).toEqual(['a']));
    setDataSpace('ds-2');
    const { result } = renderHook(() =>
      useV1Mutation(async () => 'ok', { invalidates: [['listThings']] }),
    );
    act(() => result.current.run());
    await waitFor(() => expect(result.current.data).toBe('ok'));
    expect(listThings).toHaveBeenCalledTimes(1);
  });
});
