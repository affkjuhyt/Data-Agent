/**
 * Gói ngành as the api sends it: IndustryPackView names the pack and its publisher; what is
 * installed and the latest version are PROPOSAL, so without them the pack is listed, not opened.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { listPacks } from './packs';

const server = setupServer(
  http.get('*/v1/packs', () =>
    HttpResponse.json({
      items: [
        {
          id: 'p-1',
          code: 'banking',
          name: 'Ngân hàng Việt Nam',
          publisher: 'Data Agent',
          industry_code: 'banking',
        },
      ],
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('packs from the api', () => {
  it('lists a pack with no version to open', async () => {
    expect((await listPacks()).items).toEqual([
      {
        id: 'p-1',
        name: 'Ngân hàng Việt Nam',
        publisher: 'Data Agent',
        installed: null,
        latest: null,
      },
    ]);
  });
});
