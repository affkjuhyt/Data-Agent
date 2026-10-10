/**
 * Information architecture guards for the v3.1 navigation (Figma "Điều hướng v3.1").
 */
import { describe, expect, it } from 'vitest';
import { AREA_RAILS, AREAS, firstPageOf } from '../constants';
import { ROUTE_MAP, SINGLE_SCREEN } from '../routes';
import { landingFor, resolveRoute } from '../AppStateContext';

describe('navigation', () => {
  it('shows the six v3.1 areas in order', () => {
    expect(AREAS.map(a => a.label)).toEqual(['Trang chủ', 'Cần bạn', 'Agents', 'Phân tích', 'Dữ liệu', 'Quản trị']);
  });

  it('keeps Trang chủ and Cần bạn in the bar on narrow screens', () => {
    expect(AREAS.filter(a => a.pinnedOnNarrow).map(a => a.id)).toEqual(['home', 'needs-you']);
  });

  it('routes every area and every rail item to a real screen', () => {
    for (const area of AREAS) {
      const rail = AREA_RAILS[area.id];
      if (!rail) {
        expect(ROUTE_MAP[area.id]?.[SINGLE_SCREEN], area.id).toBeDefined();
        continue;
      }
      for (const item of rail.flatMap(g => g.items)) {
        expect(ROUTE_MAP[area.id]?.[item.id], `${area.id}/${item.id}`).toBeDefined();
      }
    }
  });

  it('lands everyone on Trang chủ', () => {
    expect(landingFor('EXECUTIVE')).toEqual({ module: 'home' });
    expect(landingFor('AI_ENGINEER')).toEqual({ module: 'home' });
    expect(landingFor('PLATFORM_ADMIN')).toEqual({ module: 'home' });
  });

  it('opens an area with a rail on its first page', () => {
    expect(firstPageOf('agents')).toBe('TEAM');
    expect(firstPageOf('home')).toBeUndefined();
  });

  it('redirects links of the earlier IAs to the v3.1 areas', () => {
    expect(resolveRoute('ask')).toEqual({ module: 'analysis', sub: 'ASK' });
    expect(resolveRoute('knowledge')).toEqual({ module: 'data', sub: undefined });
    expect(resolveRoute('build', 'AGENTS')).toEqual({ module: 'agents', sub: 'TEAM' });
    expect(resolveRoute('build', 'WORKFLOWS')).toEqual({ module: 'agents', sub: 'ORCHESTRATION' });
    expect(resolveRoute('build', 'PLAYGROUND')).toEqual({ module: 'agents', sub: 'EVALUATE' });
    expect(resolveRoute('build', 'KNOWLEDGE')).toEqual({ module: 'data', sub: undefined });
    expect(resolveRoute('ops', 'TRACES')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('ops', 'REVIEWS')).toEqual({ module: 'needs-you', sub: undefined });
    expect(resolveRoute('ops', 'ANALYTICS')).toEqual({ module: 'analysis', sub: 'DASHBOARD' });
    expect(resolveRoute('settings', 'TEAM')).toEqual({ module: 'admin', sub: 'IAM' });
    expect(resolveRoute('settings', 'BILLING')).toEqual({ module: 'admin', sub: 'BILLING' });
    expect(resolveRoute('monitor', 'TRACES')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('test', 'PLAYGROUND')).toEqual({ module: 'agents', sub: 'EVALUATE' });
  });

  it('leaves v3.1 routes untouched', () => {
    expect(resolveRoute('agents', 'RUNS')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('home')).toEqual({ module: 'home', sub: undefined });
  });
});
