/**
 * Bản ghi lỗi against the v1 mock: a group held by a structure change cannot reload; another
 * group is dropped with a reason.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ErrorRecordsPage from './ErrorRecordsPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: undefined as string | undefined }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
});
afterAll(() => server.close());

const renderPage = () =>
  render(
    <ToastProvider>
      <ErrorRecordsPage />
    </ToastProvider>,
  );

describe('ErrorRecordsPage', () => {
  it('groups records by cause and links the structure change holding them', async () => {
    renderPage();
    expect(
      await screen.findByText(
        '1.402 bản ghi từ 2 job · giữ 30 ngày · bản ghi đã che dữ liệu cá nhân',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Ngày ngoài khoảng hợp lệ' })).toBeDisabled();
    expect(
      screen.getByText(/1\.399 bản ghi chỉ nạp được sau khi thay đổi cấu trúc được chấp nhận/),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Mẫu bản ghi · channel_code dài hơn 2 ký tự' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở thay đổi cấu trúc' }));
    expect(app.navigate).toHaveBeenCalledWith('data', 'DRIFT', 'drift-card-0810');
  });

  it('refuses to reload a group blocked by a structure change', async () => {
    renderPage();
    const reload = await screen.findByRole('button', { name: 'Nạp lại đã chọn' });
    expect(reload).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('checkbox', { name: 'channel_code dài hơn 2 ký tự' }));
    fireEvent.click(reload);
    expect(
      await screen.findByText(/chỉ nạp lại được sau khi thay đổi cấu trúc được chấp nhận/),
    ).toBeInTheDocument();
  });

  it('drops a group only with a reason', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Thiếu customer_id' }));
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ đã chọn' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Bỏ 3 bản ghi?')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ bản ghi' }));
    expect(
      await within(dialog).findByText('Ghi lý do để lưu vào nhật ký kiểm toán.'),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do'), {
      target: { value: 'Giao dịch thử' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ bản ghi' }));
    expect(await screen.findByText('Đã bỏ 3 bản ghi.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: 'Thiếu customer_id' })).toBeDisabled(),
    );
  });
});
