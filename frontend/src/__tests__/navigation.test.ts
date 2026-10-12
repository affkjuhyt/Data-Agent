/**
 * Information architecture guards for the v3.1 navigation (Figma "Điều hướng v3.1").
 */
import { describe, expect, it } from 'vitest';
import { AREA_LANDING, AREA_RAILS, AREAS, firstPageOf, isRailPage } from '../constants';
import { ROUTE_MAP, SINGLE_SCREEN } from '../routes';
import { landingFor, resolveRoute } from '../AppStateContext';

describe('navigation', () => {
  it('shows the six v3.1 areas in order, with Cài đặt only behind the avatar', () => {
    expect(AREAS.filter(a => !a.hidden).map(a => a.label)).toEqual([
      'Trang chủ', 'Cần bạn', 'Agents', 'Phân tích', 'Dữ liệu', 'Quản trị',
    ]);
    expect(AREAS.filter(a => a.hidden).map(a => a.id)).toEqual(['account']);
  });

  it('keeps Trang chủ and Cần bạn in the bar on narrow screens', () => {
    expect(AREAS.filter(a => a.pinnedOnNarrow).map(a => a.id)).toEqual(['home', 'needs-you']);
  });

  it('routes every landing and every rail item to a real screen', () => {
    for (const area of AREAS) {
      if (AREA_LANDING.has(area.id)) expect(ROUTE_MAP[area.id]?.[SINGLE_SCREEN], area.id).toBeDefined();
      for (const item of (AREA_RAILS[area.id] ?? []).flatMap(g => g.items)) {
        expect(ROUTE_MAP[area.id]?.[item.id], `${area.id}/${item.id}`).toBeDefined();
      }
    }
  });

  it('draws the rail on rail pages only', () => {
    expect(isRailPage('analysis', undefined)).toBe(false);
    expect(isRailPage('analysis', 'ASK')).toBe(true);
    // A detail page draws its parent's rail: Tạo agent sits under Đội agent, as in Figma.
    expect(isRailPage('agents', 'BUILDER')).toBe(true);
    expect(isRailPage('analysis', 'REPORT')).toBe(true);
    expect(isRailPage('agents', 'UNKNOWN')).toBe(false);
  });

  it('keeps no rail item of the old Settings screens', () => {
    const adminIds = (AREA_RAILS.admin ?? []).flatMap(g => g.items.map(i => i.id));
    for (const old of ['IAM', 'AUTH', 'SECRETS', 'SECURITY', 'AI_PROVIDERS', 'INFRASTRUCTURE', 'STORAGE', 'BILLING', 'API', 'ADVANCED']) {
      expect(adminIds).not.toContain(old);
    }
  });

  it('lands everyone on Trang chủ', () => {
    expect(landingFor()).toEqual({ module: 'home' });
  });

  it('opens Agents on Đội agent and the other areas on their landing', () => {
    expect(firstPageOf('agents')).toBe('TEAM');
    expect(firstPageOf('analysis')).toBeUndefined();
    expect(firstPageOf('home')).toBeUndefined();
    expect(firstPageOf('account')).toBe('PROFILE');
  });

  it('redirects links of the earlier IAs to the v3.1 areas', () => {
    expect(resolveRoute('ask')).toEqual({ module: 'analysis', sub: 'ASK' });
    expect(resolveRoute('knowledge')).toEqual({ module: 'data', sub: 'SOURCES' });
    expect(resolveRoute('build', 'AGENTS')).toEqual({ module: 'agents', sub: 'TEAM' });
    expect(resolveRoute('build', 'WORKFLOWS')).toEqual({ module: 'agents', sub: 'ORCHESTRATION' });
    expect(resolveRoute('build', 'PLAYGROUND')).toEqual({ module: 'agents', sub: 'EVALUATE' });
    expect(resolveRoute('build', 'KNOWLEDGE')).toEqual({ module: 'data', sub: 'SOURCES' });
    expect(resolveRoute('settings')).toEqual({ module: 'admin', sub: undefined });
    expect(resolveRoute('ops', 'TRACES')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('ops', 'REVIEWS')).toEqual({ module: 'needs-you', sub: undefined });
    expect(resolveRoute('ops', 'ANALYTICS')).toEqual({ module: 'analysis', sub: 'DASHBOARD' });
    expect(resolveRoute('settings', 'TEAM')).toEqual({ module: 'admin', sub: 'USERS' });
    expect(resolveRoute('settings', 'AI_PROVIDERS')).toEqual({ module: 'admin', sub: 'MODELS' });
    expect(resolveRoute('settings', 'BILLING')).toEqual({ module: 'agents', sub: 'OPERATIONS' });
    expect(resolveRoute('settings', 'ADVANCED')).toEqual({ module: 'admin', sub: undefined });
    expect(resolveRoute('monitor', 'TRACES')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('test', 'PLAYGROUND')).toEqual({ module: 'agents', sub: 'EVALUATE' });
  });

  it('leaves v3.1 routes untouched', () => {
    expect(resolveRoute('agents', 'RUNS')).toEqual({ module: 'agents', sub: 'RUNS' });
    expect(resolveRoute('home')).toEqual({ module: 'home', sub: undefined });
  });
});
