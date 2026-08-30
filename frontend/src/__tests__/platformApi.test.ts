/**
 * Pure helpers behind the ops screens and the tenant context.
 */
import { describe, expect, it } from 'vitest';
import { cleanParams, durationMs, pickTenant } from '../lib/platformApi';
import { initialEnvironment } from '../AppStateContext';

describe('platformApi helpers', () => {
  it('drops empty filters from query params', () => {
    expect(cleanParams({ q: '', status: undefined, agent_id: null, limit: 20, offset: 0 })).toEqual({ limit: 20, offset: 0 });
  });

  it('computes conversation duration only when both ends are known', () => {
    expect(durationMs('2026-08-30T10:00:00Z', '2026-08-30T10:03:42Z')).toBe(222_000);
    expect(durationMs('2026-08-30T10:00:00Z', null)).toBeNull();
    expect(durationMs('2026-08-30T10:00:00Z', '2026-08-30T09:00:00Z')).toBeNull();
    expect(durationMs('not a date', '2026-08-30T09:00:00Z')).toBeNull();
  });

  it('activates the persisted tenant when still available, else the first', () => {
    const tenants = [
      { id: 't1', name: 'Alpha', slug: 'alpha' },
      { id: 't2', name: 'Beta', slug: 'beta' },
    ];
    expect(pickTenant(tenants, 't2')?.id).toBe('t2');
    expect(pickTenant(tenants, 'gone')?.id).toBe('t1');
    expect(pickTenant(tenants, null)?.id).toBe('t1');
    expect(pickTenant([], 't1')).toBeNull();
  });
});

describe('environment persistence', () => {
  it('restores a valid stored environment and falls back to PROD', () => {
    expect(initialEnvironment('UAT')).toBe('UAT');
    expect(initialEnvironment('nope')).toBe('PROD');
    expect(initialEnvironment(null)).toBe('PROD');
  });
});
