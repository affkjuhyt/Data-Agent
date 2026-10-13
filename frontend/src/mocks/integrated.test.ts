import { describe, expect, it } from 'vitest';
import { integratedOperations, isIntegrated } from './integrated';

const operations = integratedOperations();
const at = (path: string) => `http://localhost:8100${path}`;

describe('integrated groups', () => {
  it('lets the requests of an integrated group through, verbs included', () => {
    expect(isIntegrated('GET', at('/v1/inbox?page_size=500'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/hitl-requests/0192-ab:resolve'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/questions'), operations)).toBe(true);
    expect(isIntegrated('PATCH', at('/v1/tiles/t-1'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/runs/r-1:pause'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/sources/s-1:test'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/unmapped-phrases'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/me/notifications'), operations)).toBe(true);
  });

  it('always lets identity through, so X-Data-Space holds a real id', () => {
    expect(isIntegrated('GET', at('/v1/me'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/data-spaces'), operations)).toBe(true);
    // With Quản trị wired the rest of the group follows identity to the api.
    expect(isIntegrated('POST', at('/v1/data-spaces'), operations)).toBe(true);
  });

  it('sends the vault to the api now that it takes the device ids', () => {
    expect(isIntegrated('GET', at('/v1/exec/theses'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/exec/memory-items'), operations)).toBe(true);
  });

  it('keeps the other groups and the PROPOSAL endpoints on the mock', () => {
    expect(isIntegrated('GET', at('/v1/proposals/p-1/collaboration'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/session-reports/r-1'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/runs/r-1:retry'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/agents/a-1:lock'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/sources/s-1:activate'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/data-products/dp-1/monitoring'), operations)).toBe(false);
    // A verb the api does not serve is not taken for the plain path.
    expect(isIntegrated('POST', at('/v1/hitl-requests/h-1:snooze'), operations)).toBe(false);
    expect(isIntegrated('DELETE', at('/v1/inbox'), operations)).toBe(false);
  });

  it('finds /v1 under a gateway prefix', () => {
    expect(isIntegrated('GET', 'https://gw.example/api/v1/inbox', operations)).toBe(true);
  });

  it('passes every A2 operation of an integrated group to the api', () => {
    expect(isIntegrated('GET', at('/v1/alerts'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/reports/r-1:pause'), operations)).toBe(true);
    expect(isIntegrated('PATCH', at('/v1/monitors/m-1'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/report-runs/rr-1'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/admin/model-providers'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/admin/clients'), operations)).toBe(true);
    expect(isIntegrated('PUT', at('/v1/data-spaces/ds-1/members'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/dsr'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/packs:import'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/documents'), operations)).toBe(true);
  });
});
