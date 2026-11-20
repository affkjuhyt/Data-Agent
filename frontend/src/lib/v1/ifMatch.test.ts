/**
 * DF-18 (AUD-09): a PATCH of a context asset carries If-Match, the ETag of the last read of that
 * resource; a PATCH of one never read here reads it first; a write forgets the tag it used.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { v1Get, v1Send } from './client';

const sent: { method: string; path: string; ifMatch: string | null }[] = [];
let version = 1;
const server = setupServer(
  http.get('*/v1/metrics/:id', ({ params, request }) => {
    sent.push({
      method: 'GET',
      path: `/metrics/${params.id}`,
      ifMatch: request.headers.get('If-Match'),
    });
    return HttpResponse.json({ id: params.id, version }, { headers: { ETag: `W/"m-${version}"` } });
  }),
  http.patch('*/v1/metrics/:id', ({ params, request }) => {
    sent.push({
      method: 'PATCH',
      path: `/metrics/${params.id}`,
      ifMatch: request.headers.get('If-Match'),
    });
    version += 1;
    return HttpResponse.json({ id: params.id, version });
  }),
  http.patch('*/v1/feedback/:id', ({ request }) => {
    sent.push({ method: 'PATCH', path: '/feedback', ifMatch: request.headers.get('If-Match') });
    return HttpResponse.json({});
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  sent.length = 0;
  version = 1;
});
afterAll(() => server.close());

describe('If-Match on a context asset', () => {
  it('sends the ETag of the last read, then reads again after writing', async () => {
    await v1Get('/metrics/m-1');
    await v1Send('PATCH', '/metrics/m-1', { name: 'Doanh thu' });
    expect(sent.at(-1)).toEqual({ method: 'PATCH', path: '/metrics/m-1', ifMatch: 'W/"m-1"' });
    // The write forgot that tag: the next edit reads the version it now writes over.
    await v1Send('PATCH', '/metrics/m-1', { name: 'Doanh thu thuần' });
    expect(sent.slice(-2)).toEqual([
      { method: 'GET', path: '/metrics/m-1', ifMatch: null },
      { method: 'PATCH', path: '/metrics/m-1', ifMatch: 'W/"m-2"' },
    ]);
  });

  it('reads an asset never read here before patching it; other writes are left alone', async () => {
    await v1Send('PATCH', '/metrics/m-2', { name: 'Lãi gộp' });
    expect(sent).toEqual([
      { method: 'GET', path: '/metrics/m-2', ifMatch: null },
      { method: 'PATCH', path: '/metrics/m-2', ifMatch: 'W/"m-1"' },
    ]);
    await v1Send('PATCH', '/feedback/f-1', { note: 'ok' });
    expect(sent.at(-1)).toEqual({ method: 'PATCH', path: '/feedback', ifMatch: null });
  });
});
