/**
 * Báo cáo định kỳ and Bản tin against the v1 mock: changing a report's sections and saving them,
 * pausing it and sending a test, and reading the digests sent to me.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import DigestPage from './DigestPage';
import ReportSetupPage from './ReportSetupPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('ReportSetupPage', () => {
  it('saves reordered and ticked sections only once something changed', async () => {
    wrap(<ReportSetupPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /Rủi ro tín dụng · tuần/ }),
    ).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Lưu thay đổi' });
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('row', { name: /22\/09 07:00 Giữ lại/ })).toBeInTheDocument();

    fireEvent.keyDown(screen.getByRole('button', { name: /Đổi thứ tự Nợ xấu theo vùng/ }), {
      key: 'ArrowUp',
    });
    const items = within(screen.getByRole('list', { name: 'Các mục của báo cáo' })).getAllByRole(
      'checkbox',
    );
    expect(items.map((c) => c.closest('label')?.textContent?.trim())).toEqual([
      'Tóm tắt điều đáng chú ý',
      'Nợ xấu theo vùng',
      'Chỉ số chính',
      'Việc đang chờ quyết định',
      'Chi tiết 20 khoản lớn nhất',
    ]);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Việc đang chờ quyết định' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thay đổi' }));
    expect(await screen.findByText('Đã lưu; lần gửi sau dùng thiết lập mới')).toBeInTheDocument();
  });

  it('pauses the report and sends a test to me', async () => {
    wrap(<ReportSetupPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Gửi thử cho tôi' }));
    expect(await screen.findByText('Đang soạn bản thử; gửi riêng cho bạn')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tạm dừng' }));
    expect(await screen.findByRole('button', { name: 'Tiếp tục' })).toBeInTheDocument();
    expect(screen.getAllByText('Tạm dừng').length).toBeGreaterThan(0);
  });
});

describe('DigestPage', () => {
  it('reads the newest digest and switches to another', async () => {
    wrap(<DigestPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Rủi ro tín dụng · tuần 41' }),
    ).toBeInTheDocument();
    expect(screen.getByText('+0,22 điểm')).toHaveClass('text-error');
    expect(screen.getByLabelText('chưa đọc')).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole('button', { name: 'Duyệt điều chỉnh nợ quá hạn 214 khoản vay' }),
    );
    expect(navigate).toHaveBeenCalledWith('needs-you');
    fireEvent.click(screen.getByRole('button', { name: /Danh mục cho vay · tháng 9/ }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'DIGEST', 'rr-board-09');
  });
});
