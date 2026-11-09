import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { useShellCounts } from './useShellCounts';

let inbox = 3;
const page = (n: number) => ({ items: Array.from({ length: n }, (_, i) => ({ id: `i${i}` })), next_page_token: '' });
const server = setupServer(
  http.get('*/v1/inbox', () => HttpResponse.json(page(inbox))),
  http.get('*/v1/runs', () => HttpResponse.json(page(0))),
  http.get('*/v1/agents', () => HttpResponse.json(page(0))),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

describe('useShellCounts', () => {
  it('reads the counts again when the Data Space changes, not at the next poll', async () => {
    const { result, rerender } = renderHook(({ scope }) => useShellCounts(true, scope), {
      initialProps: { scope: 'ds-a' },
    });
    await waitFor(() => expect(result.current.needsYou).toBe(3));
    inbox = 1;
    rerender({ scope: 'ds-b' });
    await waitFor(() => expect(result.current.needsYou).toBe(1));
  });
});
