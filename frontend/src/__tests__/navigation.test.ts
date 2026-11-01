/**
 * Information architecture guards for the v3.1 navigation (Figma "Điều hướng v3.1").
 */
import { describe, expect, it } from 'vitest';
import { AREA_LANDING, AREA_RAILS, AREAS, firstPageOf, isRailPage } from '../constants';
import { ROUTE_MAP, SINGLE_SCREEN } from '../routes';
import { landingFor } from '../AppStateContext';

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
});
