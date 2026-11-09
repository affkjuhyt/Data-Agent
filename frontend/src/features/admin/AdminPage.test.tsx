/**
 * "Quản trị" landing: the compliance entries follow the viewer's capabilities, as the rail does,
 * so an org admin (`admin` only) is not sent to screens that can only answer 403.
 */
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import AdminPage from './AdminPage';

vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate: vi.fn() }) }));
let capabilities: string[] = [];
vi.mock('../../lib/AuthProvider', () => ({ useAuth: () => ({ me: { capabilities } }) }));

const empty = { items: [], next_page_token: '' };
const server = setupServer(
  http.get('*/v1/admin/users', () => HttpResponse.json(empty)),
  http.get('*/v1/admin/models', () => HttpResponse.json(empty)),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('AdminPage', () => {
  it('leaves out Kiểm toán and Quyền riêng tư for an admin without audit_read or compliance', () => {
    capabilities = ['admin'];
    render(<AdminPage />);
    expect(screen.getByText('Người & quyền')).toBeInTheDocument();
    expect(screen.queryByText('Kiểm toán')).toBeNull();
    expect(screen.queryByText('Quyền riêng tư')).toBeNull();
  });

  it('offers them to whoever holds the capability', () => {
    capabilities = ['admin', 'audit_read', 'compliance'];
    render(<AdminPage />);
    expect(screen.getByText('Kiểm toán')).toBeInTheDocument();
    expect(screen.getByText('Quyền riêng tư')).toBeInTheDocument();
  });
});
