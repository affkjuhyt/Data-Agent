/**
 * Thẩm quyền nguồn against the v1 mock: the ranking per kind of data, reordering and saving a
 * draft, and BR-121 refusing a manual source above a level-1 one.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createAuthorityHandlers } from '../../mocks/authorities';
import SourceAuthorityPage from './SourceAuthorityPage';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createAuthorityHandlers()));
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <SourceAuthorityPage />
    </ToastProvider>,
  );

describe('SourceAuthorityPage', () => {
  it('shows each ranking, threshold and decider, with the latest example', async () => {
    page();
    expect(
      await screen.findByText('1 Core banking → 2 Kho dữ liệu doanh nghiệp → 3 Tải tay'),
    ).toBeInTheDocument();
    expect(screen.getByText('0,1% hoặc 1 tỷ')).toBeInTheDocument();
    expect(screen.getByText(/lệch 1,8 tỷ dư nợ chi nhánh Hà Nội/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lưu' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('reorders sources and saves the row as a draft waiting for approval', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: /Doanh thu thẻ/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Đưa Sổ cái GL lên' }));
    expect(screen.getByText('1 Sổ cái GL → 2 Hệ thống thẻ')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText(/Đã lưu 1 thay đổi/)).toBeInTheDocument();
    const row = screen.getByRole('row', { name: /Doanh thu thẻ/ });
    expect(await within(row).findByText('Bản nháp · chờ duyệt')).toBeInTheDocument();
  });

  it('refuses a manual upload above a level-1 source (BR-121)', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: /Dư nợ, nhóm nợ/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Đưa Tải tay lên' }));
    fireEvent.click(screen.getByRole('button', { name: 'Đưa Tải tay lên' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText(/không được đứng trên nguồn cấp 1/)).toBeInTheDocument();
  });
});
