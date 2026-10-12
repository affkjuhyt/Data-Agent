import { describe, expect, it } from 'vitest';
import { integratedOperations, isIntegrated } from './integrated';

const operations = integratedOperations();
const at = (path: string) => `http://localhost:8100${path}`;

describe('integrated groups', () => {
  it('lets the requests of an integrated group through, verbs included', () => {
    expect(isIntegrated('GET', at('/v1/inbox?page_size=500'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/hitl-requests/0192-ab:resolve'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/questions'), operations)).toBe(true);
  });

  it('always lets identity through, so X-Data-Space holds a real id', () => {
    expect(isIntegrated('GET', at('/v1/me'), operations)).toBe(true);
    expect(isIntegrated('GET', at('/v1/data-spaces'), operations)).toBe(true);
    expect(isIntegrated('POST', at('/v1/data-spaces'), operations)).toBe(false);
  });

  it('keeps the other groups and the PROPOSAL endpoints on the mock', () => {
    expect(isIntegrated('GET', at('/v1/periods/p-1'), operations)).toBe(false);
    expect(isIntegrated('GET', at('/v1/proposals/p-1/collaboration'), operations)).toBe(false);
    // A verb the api does not serve is not taken for the plain path.
    expect(isIntegrated('POST', at('/v1/hitl-requests/h-1:snooze'), operations)).toBe(false);
    expect(isIntegrated('DELETE', at('/v1/inbox'), operations)).toBe(false);
  });

  it('finds /v1 under a gateway prefix', () => {
    expect(isIntegrated('GET', 'https://gw.example/api/v1/inbox', operations)).toBe(true);
  });
});
