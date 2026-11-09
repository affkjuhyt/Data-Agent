/**
 * Separation of duties on the rail: an org admin holds `admin` only, so the compliance, audit and
 * write-back screens are not offered, and their counters are not loaded (each would be a 403).
 */
import { renderHook, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { AREA_RAILS } from '../../constants';
import { railFor } from './AppShell';
import { useRailCounts } from './useRailCounts';

const ids = (groups: ReturnType<typeof railFor>) =>
  (groups ?? []).flatMap((g) => g.items.map((i) => i.id));

const called: string[] = [];
const empty = () => HttpResponse.json({ items: [], next_page_token: '' });
const server = setupServer(
  http.get('*/v1/*', ({ request }) => {
    called.push(new URL(request.url).pathname);
    return undefined;
  }),
  http.get('*/v1/dsr', empty),
  http.get('*/v1/admin/users', empty),
  http.get('*/v1/data-spaces', empty),
  http.get('*/v1/policies', () => HttpResponse.json([])),
  http.get('*/v1/access-requests', empty),
  http.get('*/v1/integrations', empty),
  http.get('*/v1/admin/integrations', empty),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => {
  server.resetHandlers();
  called.length = 0;
});
afterAll(() => server.close());

describe('rail capabilities', () => {
  it('hides Kiểm toán and Quyền riêng tư from an admin, and shows each to its own role', () => {
    expect(ids(railFor(AREA_RAILS.admin, ['admin']))).not.toContain('AUDIT');
    expect(ids(railFor(AREA_RAILS.admin, ['admin']))).not.toContain('PRIVACY');
    expect(ids(railFor(AREA_RAILS.admin, ['audit_read']))).toContain('AUDIT');
    expect(ids(railFor(AREA_RAILS.admin, ['compliance']))).toContain('PRIVACY');
  });

  it('hides Ghi ngược without the writeback capability', () => {
    expect(ids(railFor(AREA_RAILS.data, ['admin']))).not.toContain('WRITEBACK');
    expect(ids(railFor(AREA_RAILS.data, ['writeback']))).toContain('WRITEBACK');
  });

  it('loads no counter for an item the viewer does not see', async () => {
    const rail = railFor(AREA_RAILS.admin, ['admin']);
    const { result } = renderHook(() => useRailCounts('admin', rail));
    await waitFor(() => expect(result.current).toHaveProperty('USERS'));
    expect(called).not.toContain('/v1/dsr');
    expect(result.current).not.toHaveProperty('PRIVACY');
  });

  it('counts an item the viewer sees', async () => {
    const rail = railFor(AREA_RAILS.admin, ['admin', 'compliance']);
    const { result } = renderHook(() => useRailCounts('admin', rail));
    await waitFor(() => expect(result.current).toHaveProperty('PRIVACY', 0));
  });
});
