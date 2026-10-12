/**
 * Lệch cấu trúc · Duyệt thay đổi against the v1 mock: from the rail it opens the open drift of a
 * connected source (P14's crawled drift is not mixed in); accepting records one of two approvals;
 * rejecting goes through a confirm and closes it.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import SourceDriftPage from './SourceDriftPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: undefined as string | undefined }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
  app.routeId = undefined;
});
afterAll(() => server.close());

const renderPage = () =>
  render(
    <ToastProvider>
      <SourceDriftPage />
    </ToastProvider>,
  );

describe('SourceDriftPage', () => {
  it('shows the changes, their impact and who approves', async () => {
    renderPage();
    expect(
      await screen.findByRole('heading', { name: /Thẻ tín dụng đổi cấu trúc/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        'Phát hiện 08:00 hôm nay khi quét · job card_transactions đang tạm dừng · 1.402 bản ghi giữ lại',
      ),
    ).toBeInTheDocument();
    const row = screen.getByText('channel_code').closest('tr')!;
    expect(within(row).getByText('CHAR(2)')).toBeInTheDocument();
    expect(within(row).getByText('Đổi kiểu · 2 chỉ số dùng cột này')).toBeInTheDocument();
    expect(screen.getByText('CARD_CHANNEL_MIX')).toBeInTheDocument();
    expect(screen.getByText('Agent Chất lượng dữ liệu đánh giá lúc 08:02')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Người duyệt' })).toBeInTheDocument();
  });

  it('records the first of two approvals, then refuses the same person twice', async () => {
    app.routeId = 'drift-card-0810';
    renderPage();
    const accept = await screen.findByRole('button', { name: 'Chấp nhận và nạp tiếp' });
    fireEvent.click(accept);
    expect(
      await screen.findByText('Đã ghi duyệt của bạn. Cần thêm 1 người duyệt.'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Chờ duyệt · 1/2 người')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận và nạp tiếp' }));
    expect(await screen.findByText(/cần thêm người phụ trách chỉ số duyệt/)).toBeInTheDocument();
  });

  it('rejects after a confirm and keeps the old structure', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Từ chối, giữ cấu trúc cũ' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối, giữ cấu trúc cũ' }));
    expect(
      await screen.findByText('Đã từ chối. Job card_transactions chạy tiếp với cấu trúc cũ.'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Không có thay đổi cấu trúc nào chờ duyệt')).toBeInTheDocument();
  });
});
