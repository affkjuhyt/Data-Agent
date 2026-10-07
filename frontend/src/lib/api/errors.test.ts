import { readdirSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MOCK_FIXTURES_DIR, mockFixture } from '../../test-utils/contractFixtures';
import type { components } from './contracts';
import { ApiError, isVersionConflict } from './errors';

vi.mock('../config', () => ({ INDICATOR_API_URL: 'http://indicator.test' }));

type ErrorBody = components['schemas']['ErrorBody'];

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

afterEach(() => vi.unstubAllGlobals());

describe('ApiError on the contract error bodies', () => {
  const codes = readdirSync(`${MOCK_FIXTURES_DIR}/errors`).map((f) => f.replace(/\.json$/, ''));

  it.each(codes)('%s keeps its code and message', (code) => {
    const body = mockFixture<ErrorBody>(`errors/${code}.json`);
    const error = new ApiError(400, 'Bad Request', body);
    expect(error.code).toBe(code);
    expect(error.message).toBe(body.error.message);
  });

  it('carries current_version on VERSION_CONFLICT', () => {
    const error = new ApiError(409, 'Conflict', mockFixture('version_conflict/response.409.json'));
    expect(isVersionConflict(error)).toBe(true);
    expect(error.currentVersion).toBe(3);
  });

  it('falls back to the HTTP status when the body is not a contract error', () => {
    const error = new ApiError(502, 'Bad Gateway', '<html>');
    expect(error.code).toBeUndefined();
    expect(error.message).toBe('502 Bad Gateway');
    expect(isVersionConflict(error)).toBe(false);
  });
});

describe('indicatorApi', () => {
  it('sends expected_version and turns a 409 into a VERSION_CONFLICT error', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(jsonResponse(409, mockFixture('version_conflict/response.409.json')));
    vi.stubGlobal('fetch', fetchMock);
    const { decideReviewCase } = await import('./indicatorApi');

    const call = decideReviewCase('2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06', 'approve', 2);
    await expect(call).rejects.toMatchObject({
      status: 409,
      code: 'VERSION_CONFLICT',
      currentVersion: 3,
    });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      'http://indicator.test/review/cases/2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06/approve',
    );
    expect(JSON.parse(init.body)).toEqual(mockFixture('version_conflict/request.json'));
  });

  it('reads a series and reports an unknown one as UNRESOLVED_ENTITY', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(jsonResponse(404, mockFixture('errors/UNRESOLVED_ENTITY.json'))),
    );
    const { getSeries } = await import('./indicatorApi');
    await expect(getSeries('NOPE:X')).rejects.toMatchObject({
      status: 404,
      code: 'UNRESOLVED_ENTITY',
    });
  });
});
