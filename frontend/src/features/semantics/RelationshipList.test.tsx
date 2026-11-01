/**
 * Gỡ quan hệ: an active join is retired through a proposal with a reason
 * (POST /v1/relationships/{id}:retire); one waiting for review or already gone has no "Gỡ".
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { RelationshipList } from './RelationshipList';

const posted: { id: string; body: unknown }[] = [];
const server = setupServer(
  http.post('*/v1/relationships/:action', async ({ params, request }) => {
    posted.push({ id: String(params.action), body: await request.json() });
    return HttpResponse.json({ id: 'r-ship', status: 'active' }, { status: 202 });
  }),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterAll(() => server.close());

const rows = [
  {
    id: 'r-ship',
    text: 'ban_internet(ship_date_key) → lich(date_key)',
    status: 'Đang dùng',
    active: true,
  },
  {
    id: 'r-due',
    text: 'ban_internet(due_date_key) → lich(date_key)',
    status: 'Chờ duyệt',
    active: false,
  },
];

describe('RelationshipList', () => {
  it('asks for an active join to go, with its reason', async () => {
    render(
      <ToastProvider>
        <RelationshipList rows={rows} />
      </ToastProvider>,
    );
    expect(screen.getAllByRole('button', { name: 'Gỡ' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Gỡ' }));
    const send = screen.getByRole('button', { name: 'Gửi đề xuất gỡ' });
    expect(send).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(screen.getByLabelText(/Lý do gỡ ban_internet\(ship_date_key\)/), {
      target: { value: 'Vai ngày giao hàng' },
    });
    fireEvent.click(send);
    expect(await screen.findByText(/Đã gửi đề xuất gỡ/)).toBeInTheDocument();
    expect(posted).toEqual([{ id: 'r-ship:retire', body: { reason: 'Vai ngày giao hàng' } }]);
  });
});
