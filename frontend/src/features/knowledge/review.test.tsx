/**
 * Rà soát tri thức against the v1 mock: the run's figures, the open findings by kind, the
 * selected finding's evidence, and its fixed actions (dismiss needs a reason).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import KnowledgeReviewPage from './KnowledgeReviewPage';

vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <KnowledgeReviewPage />
    </ToastProvider>,
  );

describe('KnowledgeReviewPage', () => {
  it('shows the run, the findings by kind, and opens the contradiction with both sides', async () => {
    page();
    expect(
      await screen.findByText(/412 trang · 6 phát hiện mở · 61% nguồn bỏ qua/),
    ).toBeInTheDocument();
    expect(screen.getByText('2 mức cao')).toHaveClass('text-error');
    expect(screen.getByRole('radio', { name: 'Mâu thuẫn 1' })).toBeInTheDocument();
    const card = screen.getByRole('region', { name: 'Phát hiện đang xem' });
    expect(within(card).getByText('5,10%/năm · website · 08:04 10/10')).toBeInTheDocument();
    expect(within(card).getByText('4,90%/năm · API · 08:31 10/10')).toBeInTheDocument();
  });

  it('marks a page stale, which closes the finding', async () => {
    page();
    const card = await screen.findByRole('region', { name: 'Phát hiện đang xem' });
    fireEvent.click(within(card).getByRole('button', { name: 'Đánh dấu cũ' }));
    expect(await screen.findByText('Đã đánh dấu trang cũ')).toBeInTheDocument();
    expect(await screen.findByText(/5 phát hiện mở/)).toBeInTheDocument();
  });

  it('dismisses only with a reason', async () => {
    page();
    const card = await screen.findByRole('region', { name: 'Phát hiện đang xem' });
    fireEvent.click(within(card).getByRole('button', { name: 'Bỏ qua' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Bỏ qua phát hiện?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ qua' }));
    expect(await within(dialog).findByText('Ghi lý do bỏ qua.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Website chưa cập nhật, API là nguồn chính' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ qua' }));
    expect(await screen.findByText('Đã bỏ qua; ẩn 90 ngày')).toBeInTheDocument();
  });
});
