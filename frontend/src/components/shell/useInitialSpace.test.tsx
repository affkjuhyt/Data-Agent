/**
 * The shell opens on one Data Space and then leaves the choice to the viewer: the list of spaces
 * reloads on every switch (its query key carries the space), and that reload must not take the
 * viewer back to their default.
 */
import { renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useInitialSpace } from './useInitialSpace';

const AW = { id: 'aw', name: 'AdventureWorks' };
const TLC = { id: 'tlc', name: 'NYC Taxi' };

describe('useInitialSpace', () => {
  it('opens on the candidate once the list has it', () => {
    const apply = vi.fn();
    const { rerender } = renderHook(
      ({ items }) => useInitialSpace('aw', items, apply),
      { initialProps: { items: undefined as (typeof AW)[] | undefined } },
    );
    expect(apply).not.toHaveBeenCalled();
    rerender({ items: [AW, TLC] });
    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith(AW);
  });

  it('does not take a switch back when the list reloads', () => {
    const apply = vi.fn();
    const { result, rerender } = renderHook(
      ({ items }) => useInitialSpace('aw', items, apply),
      { initialProps: { items: [AW, TLC] } },
    );
    expect(apply).toHaveBeenCalledTimes(1);
    // The viewer picks NYC Taxi; the list comes back as a new array for the new space.
    result.current.markChosen();
    rerender({ items: [{ ...AW }, { ...TLC }] });
    expect(apply).toHaveBeenCalledTimes(1);
  });

  it('leaves a pick made before the list arrived alone', () => {
    const apply = vi.fn();
    const { result, rerender } = renderHook(
      ({ items }) => useInitialSpace('aw', items, apply),
      { initialProps: { items: undefined as (typeof AW)[] | undefined } },
    );
    result.current.markChosen();
    rerender({ items: [AW, TLC] });
    expect(apply).not.toHaveBeenCalled();
  });
});
