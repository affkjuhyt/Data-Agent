/**
 * Person names from ids (GET /v1/principals?id=…), the read every screen uses for an owner, a
 * steward, a certifier or an assignee without the admin-only user list.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { personNames } from './me';

const asked: string[][] = [];
const server = setupServer(
  http.get('*/v1/principals', ({ request }) => {
    const ids = new URL(request.url).searchParams.getAll('id');
    asked.push(ids);
    return HttpResponse.json({
      items: ids
        .filter((id) => id.startsWith('u-'))
        .map((id) => ({ id, kind: 'user', display_name: `Người ${id}` })),
      next_page_token: '',
    });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  asked.length = 0;
});
afterAll(() => server.close());

describe('personNames', () => {
  it('names each id once, in reads of at most 100, and leaves unknown ids unnamed', async () => {
    const ids = Array.from({ length: 150 }, (_, i) => `u-${i}`);
    const names = await personNames([...ids, 'u-0', null, undefined, '', 'svc-1']);
    expect(asked.map((a) => a.length)).toEqual([100, 51]);
    expect(names.get('u-149')).toBe('Người u-149');
    expect(names.has('svc-1')).toBe(false);
    expect(names.size).toBe(150);
  });

  it('asks nothing when no id is given, and survives a refused read', async () => {
    expect((await personNames([null, undefined])).size).toBe(0);
    expect(asked).toEqual([]);
    server.use(http.get('*/v1/principals', () => new HttpResponse(null, { status: 403 })));
    expect((await personNames(['u-1'])).size).toBe(0);
  });
});
