/**
 * Lượt chạy against the v1 mock: filtering the list, and the story and controls of one run.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import RunDetailPage from './RunDetailPage';
import RunsPage from './RunsPage';
import { runDuration, shortCost } from './runStatus';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('RunsPage', () => {
  it('lists runs, filters by status and search, and opens one', async () => {
    wrap(<RunsPage />);
    expect(await screen.findByText('#427')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(9);

    fireEvent.click(screen.getByRole('radio', { name: 'Lỗi · 1' }));
    expect(screen.getAllByRole('row')).toHaveLength(2);
    expect(screen.getByText('Lỗi kết nối')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Tất cả' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Tìm lượt chạy' }), {
      target: { value: '#414' },
    });
    expect(screen.getAllByRole('row')).toHaveLength(2);

    fireEvent.click(screen.getByText('Soạn báo cáo rủi ro tháng 9'));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-414');
  });
});

describe('RunDetailPage', () => {
  it('tells the run as sentences beside its plan, and switches to the steps', async () => {
    routeId = 'run-427';
    wrap(<RunDetailPage />);
    expect(
      await screen.findByText('Đọc 1,28 triệu khoản vay từ Core banking (dữ liệu 30/09).'),
    ).toBeInTheDocument();
    expect(screen.getByText('bây giờ')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Tiến độ bước hiện tại' })).toHaveAttribute(
      'aria-valuenow',
      '60',
    );
    expect(screen.getByText('Kế hoạch · 5 bước')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Xem kỹ thuật' }));
    expect(await screen.findByText('1. Lập kế hoạch 5 bước')).toBeInTheDocument();
  });

  it('pauses, then cancels after confirmation', async () => {
    routeId = 'run-427';
    wrap(<RunDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạm dừng' }));
    expect(await screen.findByRole('button', { name: 'Chạy tiếp' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Huỷ lượt chạy' }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Huỷ lượt chạy' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Chạy tiếp' })).toBeNull());
    expect(screen.getByText('Đã huỷ')).toBeInTheDocument();
  });
});

describe('run figures', () => {
  it('writes duration and cost the short way of the table', () => {
    const start = new Date(2026, 9, 10, 6, 0, 0).toISOString();
    const end = new Date(2026, 9, 10, 6, 1, 12).toISOString();
    expect(runDuration({ started_at: start, finished_at: end } as never)).toBe('1:12');
    expect(shortCost('8400')).toBe('8k');
    expect(shortCost('900')).toBe('900');
  });
});
