/**
 * Information architecture guards — the things a demo notices first.
 */
import { describe, expect, it } from 'vitest';
import { MODULE_SUB_ITEMS, NAV_ITEMS } from '../constants';
import { ROUTE_MAP, SINGLE_SCREEN } from '../routes';
import { landingFor, resolveRoute } from '../AppStateContext';

describe('navigation', () => {
  it('opens with "Hỏi dữ liệu" and uses Vietnamese labels for end-user modules', () => {
    expect(NAV_ITEMS.map(n => n.id)).toEqual(['ask', 'knowledge', 'build', 'ops', 'settings']);
    expect(NAV_ITEMS[0].label).toBe('Hỏi dữ liệu');
  });

  it('has no "Coming Soon" entries anywhere in the nav or routes', () => {
    for (const items of Object.values(MODULE_SUB_ITEMS)) {
      for (const item of items) expect(item.comingSoon).toBeFalsy();
    }
    for (const routes of Object.values(ROUTE_MAP)) {
      for (const entry of Object.values(routes)) expect('comingSoon' in entry).toBe(false);
    }
  });

  it('routes every sub-nav item to a real screen', () => {
    for (const [module, items] of Object.entries(MODULE_SUB_ITEMS)) {
      for (const item of items) {
        expect(ROUTE_MAP[module]?.[item.id], `${module}/${item.id}`).toBeDefined();
      }
    }
  });

  it('single-screen modules have no secondary nav and a default route', () => {
    for (const module of ['ask', 'knowledge']) {
      expect(MODULE_SUB_ITEMS[module]).toBeUndefined();
      expect(ROUTE_MAP[module][SINGLE_SCREEN]).toBeDefined();
    }
  });

  it('exposes the Review Center under Vận hành › Chờ duyệt', () => {
    expect(MODULE_SUB_ITEMS.ops.find(i => i.id === 'REVIEWS')?.label).toBe('Chờ duyệt');
    expect(ROUTE_MAP.ops.REVIEWS).toBeDefined();
  });

  it('keeps old bookmarks working', () => {
    expect(resolveRoute('test', 'PLAYGROUND')).toEqual({ module: 'build', sub: 'PLAYGROUND' });
    expect(resolveRoute('release')).toEqual({ module: 'build', sub: 'AGENTS' });
    expect(resolveRoute('build', 'VERSIONS')).toEqual({ module: 'build', sub: 'AGENTS' });
    expect(resolveRoute('monitor', 'TRACES')).toEqual({ module: 'ops', sub: 'TRACES' });
    expect(resolveRoute('monitor')).toEqual({ module: 'ops', sub: 'CONVERSATIONS' });
    expect(resolveRoute('build', 'KNOWLEDGE')).toEqual({ module: 'knowledge' });
    expect(resolveRoute('knowledge-ops')).toEqual({ module: 'knowledge', sub: undefined });
    expect(resolveRoute('ops', 'REVIEWS')).toEqual({ module: 'ops', sub: 'REVIEWS' });
  });

  it('lands business users on Ask and engineers on Build', () => {
    expect(landingFor('EXECUTIVE')).toEqual({ module: 'ask' });
    expect(landingFor('AI_ENGINEER')).toEqual({ module: 'build', sub: 'AGENTS' });
    expect(landingFor('PLATFORM_ADMIN')).toEqual({ module: 'build', sub: 'AGENTS' });
  });
});
