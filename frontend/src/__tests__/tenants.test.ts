/**
 * Pure helpers behind the tenant context.
 */
import { describe, expect, it } from 'vitest';
import { pickTenant } from '../lib/tenants';
import { initialEnvironment } from '../AppStateContext';

describe('pickTenant', () => {
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
