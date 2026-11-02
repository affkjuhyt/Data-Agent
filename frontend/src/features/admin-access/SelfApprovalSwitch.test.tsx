/**
 * "Cho phép tự duyệt" on Người dùng và vai trò: the organisation's switch as GET
 * /v1/admin/organization says it is, turned by PATCH, and only for someone holding `admin`.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { SelfApprovalSwitch } from './SelfApprovalSwitch';

let capabilities: string[] = [];
vi.mock('../../lib/AuthProvider', () => ({
  useAuth: () => ({ me: { capabilities } }),
}));

const organization = (allow: boolean) => ({
  id: 'org-1',
  code: 'ORG',
  name: 'Tổ chức thử',
  allow_self_approval: allow,
});

const patched: unknown[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  patched.length = 0;
});
afterAll(() => server.close());

function serve(allow: boolean) {
  let current = allow;
  server.use(
    http.get('*/v1/admin/organization', () => HttpResponse.json(organization(current))),
    http.patch('*/v1/admin/organization', async ({ request }) => {
      const body = (await request.json()) as { allow_self_approval: boolean };
      patched.push(body);
      current = body.allow_self_approval;
      return HttpResponse.json(organization(current));
    }),
  );
}

const draw = () =>
  render(
    <ToastProvider>
      <SelfApprovalSwitch />
    </ToastProvider>,
  );

describe('SelfApprovalSwitch', () => {
  it('shows the switch as the organisation has it, with what turning it on means', async () => {
    capabilities = ['admin'];
    serve(true);
    draw();
    const toggle = await screen.findByRole('switch', { name: 'Cho phép tự duyệt' });
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
    expect(screen.getByText('Cho phép tự duyệt')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Khi bật, người tạo được tự duyệt việc của mình. Mỗi lần tự duyệt được ghi vào nhật ký kiểm toán.',
      ),
    ).toBeInTheDocument();
  });

  it('an admin turns it on', async () => {
    capabilities = ['admin'];
    serve(false);
    draw();
    const toggle = await screen.findByRole('switch', { name: 'Cho phép tự duyệt' });
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'false'));
    expect(toggle).toBeEnabled();
    fireEvent.click(toggle);
    await waitFor(() => expect(patched).toEqual([{ allow_self_approval: true }]));
    await waitFor(() => expect(toggle).toHaveAttribute('aria-checked', 'true'));
    expect(await screen.findByText('Đã bật tự duyệt cho tổ chức')).toBeInTheDocument();
  });

  it('is disabled for someone without admin', async () => {
    capabilities = ['ask', 'steward'];
    serve(false);
    draw();
    const toggle = await screen.findByRole('switch', { name: 'Cho phép tự duyệt' });
    expect(toggle).toBeDisabled();
    fireEvent.click(toggle);
    expect(patched).toEqual([]);
  });
});
