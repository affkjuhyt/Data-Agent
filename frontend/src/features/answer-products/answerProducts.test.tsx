/**
 * Data product against the v1 mock: the list with the active product opened, its scope and
 * members, freshness of the last periods, and sending for review held back by BR-175.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AnswerProductsPage from './AnswerProductsPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = () =>
  render(
    <ToastProvider>
      <AnswerProductsPage />
    </ToastProvider>,
  );

describe('AnswerProductsPage', () => {
  it('opens the active product with its scope, sample questions and open Error Book entries', async () => {
    wrap();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Giá xăng dầu trong nước/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Thuế BVMT với xăng .* Trượt/ })).toBeInTheDocument();
    expect(screen.getByText('Ngoài phạm vi')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Error Book mở/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'ERROR_BOOK', 'eb-0043');
  });

  it('keeps the primary member of the active version and shows the last 8 periods', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('tab', { name: /Thành phần/ }));
    const primary = screen.getByRole('row', { name: /gia_xang_dau_ky_dieu_hanh/ });
    expect(within(primary).getByRole('button', { name: 'Gỡ' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(screen.getByRole('tab', { name: 'Độ tươi' }));
    const periods = await screen.findByRole('list', { name: '8 kỳ gần nhất' });
    expect(within(periods).getByText('Bị che')).toBeInTheDocument();
  });

  it('refuses to send a draft short of 10 sample questions', async () => {
    wrap();
    fireEvent.click(await screen.findByRole('button', { name: /Lãi suất huy động niêm yết/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Gửi duyệt' }));
    const dialog = screen.getByRole('dialog', { name: /Gửi duyệt Lãi suất/ });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gửi duyệt' }));
    expect(await within(dialog).findByText(/Mới có 7\/10 câu hỏi mẫu/)).toBeInTheDocument();
  });
});
