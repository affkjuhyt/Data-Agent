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
  });

  it('always lets identity through, so X-Data-Space holds a real id', () => {
    expect(isIntegrated('GET', at('/v1/me'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/data-spaces'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/data-spaces'), operations)).toBe(false);
  });

  it('keeps the other groups and the PROPOSAL endpoints on the mock', () => {
    expect(isIntegrated('GET', at('/v1/periods/p-1'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/proposals/p-1/collaboration'), operations)).toBe(false);
    // Phiên is wired, but A2 has no read for a session report: that one stays mocked.
    expect(isIntegrated('GET', at('/v1/session-reports/r-1'), operations)).toBe(false);
    // PROPOSAL verbs next to served ones stay mocked.
    expect(isIntegrated('POST', at('/v1/runs/r-1:retry'), operations)).toBe(false);
    expect(isIntegrated('POST', at('/v1/agents/a-1:lock'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/sources/s-1'), operations)).toBe(false);
    expect(isIntegrated('POST', at('/v1/sources/s-1:activate'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/data-products/dp-1/monitoring'), operations)).toBe(false);
    // A verb the api does not serve is not taken for the plain path.
    expect(isIntegrated('POST', at('/v1/hitl-requests/h-1:snooze'), operations)).toBe(false);
    expect(isIntegrated('DELETE', at('/v1/inbox'), operations)).toBe(false);
  });

  it('finds /v1 under a gateway prefix', () => {
    expect(isIntegrated('GET', 'https://gw.example/api/v1/inbox', operations)).toBe(true);
  });
});
