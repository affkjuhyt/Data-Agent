import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  buildScreenUrl,
  formatAsOfLabel,
  parseScreenParams,
  serializeScreenParams,
  useScreenParams,
  type ScreenParams,
} from './urlState';

// Brent spot 2023–2026, viewed as of 21/09/2026, "what the world knew".
const BRENT: ScreenParams = {
  series: 'EIA:RBRTE:D',
  from: '2023-01-01',
  to: '2026-12-31',
  as_of: '2026-09-21',
  as_of_mode: 'world',
};

beforeEach(() => window.history.replaceState(null, '', '/ask'));

describe('link ↔ screen state', () => {
  it('a shared link opens the same series, window and as-of point (AC-1)', () => {
    const link = buildScreenUrl('ask', undefined, BRENT, 'https://app.example');
    expect(link).toBe(
      'https://app.example/ask?series=EIA%3ARBRTE%3AD&from=2023-01-01&to=2026-12-31&as_of=2026-09-21&as_of_mode=world',
    );
    expect(parseScreenParams(new URL(link).search)).toEqual({ params: BRENT, invalid: [] });
  });

  it('every key round-trips, in a fixed order', () => {
    const all: ScreenParams = {
      ...BRENT,
      as_of_mode: 'system',
      case: 'c-1',
      alert: 'a-1',
      run: 'r-1',
    };
    expect(parseScreenParams(serializeScreenParams(all)).params).toEqual(all);
    expect(serializeScreenParams({ run: 'r', series: 'S:X' })).toBe('?series=S%3AX&run=r');
  });

  it('keeps parameters that are not screen state (Keycloak code/state)', () => {
    expect(serializeScreenParams({ case: 'c-1' }, '?code=abc&state=xyz&case=old')).toBe(
      '?code=abc&state=xyz&case=c-1',
    );
  });

  it('reports unusable parameters instead of guessing', () => {
    const { params, invalid } = parseScreenParams(
      '?series=EIA:RBRTE:D&as_of=21/09/2026&from=2026-02-30&as_of_mode=both',
    );
    expect(params).toEqual({ series: 'EIA:RBRTE:D' });
    expect(invalid).toEqual(['from', 'as_of', 'as_of_mode']);
    expect(parseScreenParams('?from=2026-01-01&to=2025-01-01').invalid).toEqual(['to']);
  });

  it('labels the as-of point in Vietnamese format', () => {
    expect(formatAsOfLabel(BRENT)).toBe('21/09/2026 · thế giới biết gì');
    expect(formatAsOfLabel({ as_of: '2026-09-21', as_of_mode: 'system' })).toBe(
      '21/09/2026 · hệ thống biết gì',
    );
    expect(formatAsOfLabel({ as_of: '2026-09-21' })).toBe('21/09/2026');
    expect(formatAsOfLabel({})).toBeNull();
  });
});

describe('useScreenParams', () => {
  afterEach(() => vi.restoreAllMocks());

  it('writes the state into the URL, so a reload gives the same screen (AC-2), and nothing to storage (BR-1)', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem');
    const { result, unmount } = renderHook(() => useScreenParams());

    act(() => result.current.setParams(BRENT));
    expect(result.current.params).toEqual(BRENT);
    expect(window.location.pathname).toBe('/ask');

    // "Reload": a fresh mount reads only the URL.
    unmount();
    const reloaded = renderHook(() => useScreenParams());
    expect(reloaded.result.current.params).toEqual(BRENT);
    expect(setItem).not.toHaveBeenCalled();
  });

  it('merges a patch and removes keys set to undefined', () => {
    window.history.replaceState(null, '', '/ask?code=k&series=A:B');
    const { result } = renderHook(() => useScreenParams());
    act(() => result.current.setParams({ as_of: '2026-09-21', series: undefined }));
    expect(window.location.search).toBe('?code=k&as_of=2026-09-21');
  });

  it('follows back/forward navigation', () => {
    const { result } = renderHook(() => useScreenParams());
    act(() => {
      window.history.pushState(null, '', '/ask?case=c-9');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    expect(result.current.params).toEqual({ case: 'c-9' });
  });
});
