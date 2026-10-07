/**
 * Screen state lives in the URL — the shareable source of truth (09 §4.1):
 * which series, which window, viewed as of when and in which mode, and which
 * review case / alert / run is open. Reload, or a link opened on another
 * machine, gives the same screen. Nothing here goes to localStorage.
 *
 *   /module/tab?series=BRENT:SPOT&from=2023-01-01&to=2026-12-31&as_of=2026-09-21&as_of_mode=world
 *
 * Only what the frontend defines is validated here (dates, the mode). Object
 * ids go to the server as they are: an unknown or forbidden id comes back as
 * a contract error, so the id rules are not copied by hand from the contract.
 */
import { useCallback, useMemo, useSyncExternalStore } from 'react';
import { routePath } from '../AppStateContext';
import { formatDate } from './formatVN';

/** world: what was publicly known then (available_at); system: what this system held then (system_time). */
export type AsOfMode = 'world' | 'system';

export interface ScreenParams {
  series?: string;
  from?: string;
  to?: string;
  as_of?: string;
  as_of_mode?: AsOfMode;
  case?: string;
  alert?: string;
  run?: string;
}

type ScreenParamKey = keyof ScreenParams;

// Fixed order: the same state always serialises to the same link.
const KEYS: ScreenParamKey[] = [
  'series',
  'from',
  'to',
  'as_of',
  'as_of_mode',
  'case',
  'alert',
  'run',
];
const DATE_KEYS = new Set<ScreenParamKey>(['from', 'to', 'as_of']);
const MODES: AsOfMode[] = ['world', 'system'];

const isIsoDate = (value: string): boolean => {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!m) return false;
  const d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
};

interface ParsedScreenParams {
  params: ScreenParams;
  /** present in the link but unusable — the screen shows "broken link" */
  invalid: ScreenParamKey[];
}

export function parseScreenParams(search: string): ParsedScreenParams {
  const query = new URLSearchParams(search);
  const params: ScreenParams = {};
  const invalid: ScreenParamKey[] = [];
  for (const key of KEYS) {
    const value = query.get(key)?.trim();
    if (!value) continue;
    if (DATE_KEYS.has(key) && !isIsoDate(value)) invalid.push(key);
    else if (key === 'as_of_mode' && !MODES.includes(value as AsOfMode)) invalid.push(key);
    else (params as Record<string, string>)[key] = value;
  }
  // ISO dates compare as strings.
  if (params.from && params.to && params.from > params.to) {
    delete params.to;
    invalid.push('to');
  }
  return { params, invalid };
}

/** `?query` for `params`; parameters that are not screen state (Keycloak's code/state) are kept. */
export function serializeScreenParams(params: ScreenParams, currentSearch = ''): string {
  const query = new URLSearchParams(currentSearch);
  for (const key of KEYS) query.delete(key);
  for (const key of KEYS) {
    const value = params[key];
    if (value) query.append(key, value);
  }
  const s = query.toString();
  return s ? `?${s}` : '';
}

/** Absolute link to a screen in a given state, for sharing or for a notification. */
export function buildScreenUrl(
  module: string,
  sub: string | undefined,
  params: ScreenParams,
  origin = window.location.origin,
): string {
  return `${origin}${routePath(module, sub)}${serializeScreenParams(params)}`;
}

/** "21/09/2026 · thế giới biết gì" — the as-of point a screen shows (§6). */
export function formatAsOfLabel(params: ScreenParams): string | null {
  if (!params.as_of) return null;
  const [y, m, d] = params.as_of.split('-').map(Number);
  // Built in local time: new Date('2026-09-21') is UTC midnight, the day before west of UTC.
  const date = formatDate(new Date(y, m - 1, d));
  if (!params.as_of_mode) return date;
  return `${date} · ${params.as_of_mode === 'world' ? 'thế giới biết gì' : 'hệ thống biết gì'}`;
}

const CHANGE_EVENT = 'app:screen-params';

const subscribe = (onChange: () => void) => {
  window.addEventListener('popstate', onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
};

const readSearch = () => window.location.search;

/**
 * The screen state in the URL, and a setter that merges a patch into it
 * (`undefined` removes a key). Writes with replaceState, like the module/tab
 * route in AppStateContext — navigation itself is unchanged.
 */
export function useScreenParams(): ParsedScreenParams & {
  setParams: (patch: Partial<ScreenParams>) => void;
} {
  const search = useSyncExternalStore(subscribe, readSearch);
  const parsed = useMemo(() => parseScreenParams(search), [search]);

  const setParams = useCallback((patch: Partial<ScreenParams>) => {
    const next = { ...parseScreenParams(window.location.search).params, ...patch };
    const { pathname, search: current, hash } = window.location;
    window.history.replaceState(
      window.history.state,
      '',
      `${pathname}${serializeScreenParams(next, current)}${hash}`,
    );
    window.dispatchEvent(new Event(CHANGE_EVENT));
  }, []);

  return { ...parsed, setParams };
}
