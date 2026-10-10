/**
 * Thu thập ngoài against the v1 mock: the source list and running one, registering a source
 * (a forbidden source cannot be ticked, saving opens the schema), and approving a schema only
 * after the agent's unsure field is mapped.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import CrawlDriftPage from './CrawlDriftPage';
import CrawlRegisterPage from './CrawlRegisterPage';
import CrawlSchemaPage from './CrawlSchemaPage';
import CrawlSourcesPage from './CrawlSourcesPage';

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

describe('CrawlSourcesPage', () => {
  it('lists the sources with their health, and runs the selected one', async () => {
    wrap(<CrawlSourcesPage />);
    expect(
      await screen.findByText(
        '9 nguồn · 6 đúng lịch · 1 đổi cấu trúc · 1 bị chặn · 23 bản ghi chờ duyệt',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('98,6%')).toBeInTheDocument();
    expect(screen.getByText('Bị chặn 403 · 3 lượt')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chạy ngay' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    fireEvent.click(screen.getByRole('row', { name: /Giá vàng SJC/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Chạy ngay' }));
    expect(await screen.findByText('Đang thu Giá vàng SJC')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Lãi suất tiết kiệm online' }));
    expect(navigate).toHaveBeenCalledWith('data', 'CRAWL_SOURCE', 'cs-online');
  });

  it('refuses to run a blocked source', async () => {
    wrap(<CrawlSourcesPage />);
    fireEvent.click(await screen.findByRole('row', { name: /Lãi suất cho vay mua nhà/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Chạy ngay' }));
    expect(
      await screen.findByText(/Không chạy được: Nguồn chưa chạy được: bị chặn 403/),
    ).toBeInTheDocument();
  });
});

describe('CrawlRegisterPage', () => {
  it('suggests sources from the goal, keeps a forbidden one out, and saves into the schema', async () => {
    wrap(<CrawlRegisterPage />);
    fireEvent.change(screen.getByLabelText('Mục tiêu thu thập'), {
      target: { value: 'Thu lãi suất huy động các kỳ hạn của ngân hàng thương mại, hằng ngày' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Tìm nguồn' }));
    expect(await screen.findByText('2 · Nguồn agent đề xuất · 30 ngân hàng')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Ngân hàng D' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Ngân hàng D' })).not.toBeChecked();
    expect(screen.getByText(/Ngân hàng D cấm thu thập tự động/)).toBeInTheDocument();
    expect(screen.getByText('29 miền của ngân hàng đã chọn')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Tự động/ })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu và suy ngược schema' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith(
        'data',
        'CRAWL_SOURCE',
        expect.stringMatching(/^cs-new-/),
      ),
    );
  });
});

describe('CrawlSchemaPage', () => {
  it('approves the schema only after the unsure field is mapped', async () => {
    routeId = 'cs-online';
    wrap(<CrawlSchemaPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Suy ngược schema · bản nháp v1' }),
    ).toBeInTheDocument();
    expect(screen.getByText('đạt ngưỡng 95% (BR-151)')).toBeInTheDocument();
    expect(screen.getByText('channel chưa ánh xạ chắc')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Duyệt schema' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    fireEvent.click(screen.getByRole('combobox', { name: 'Thuộc tính chuẩn của channel' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Kênh giao dịch' }));
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Duyệt schema' })).not.toHaveAttribute(
        'aria-disabled',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt schema' }));
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Schema · v1 đang dùng' }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('row', { name: /^channel/ })).getByText('Kênh giao dịch'),
    ).toBeInTheDocument();
  });
});

describe('CrawlDriftPage', () => {
  it('shows the drift and approves the proposed version', async () => {
    routeId = 'drift-fees-0710';
    wrap(<CrawlDriftPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Thay đổi cấu trúc nặng · lượt 07/10' }),
    ).toBeInTheDocument();
    expect(screen.getByText('0,62')).toHaveClass('text-error');
    expect(screen.getByRole('row', { name: /Bỏ note/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt phiên bản v4' }));
    expect(await screen.findByText('Đã duyệt v4; nguồn chạy tiếp')).toBeInTheDocument();
    expect(await screen.findByText(/Đã xử lý: duyệt v4/)).toBeInTheDocument();
  });

  it('reverts only with a reason', async () => {
    routeId = 'drift-fees-0710';
    wrap(<CrawlDriftPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Hoàn tác về v3' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Hoàn tác về v3?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Hoàn tác' }));
    expect(await within(dialog).findByText('Ghi lý do hoàn tác.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'v4 trích sai tệp scan' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Hoàn tác' }));
    expect(await screen.findByText(/Đã xử lý: hoàn tác về v3/)).toBeInTheDocument();
  });

  it('opens the drift from the quarantined source in the list', async () => {
    wrap(<CrawlSourcesPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Đổi cấu trúc · cách ly' }));
    expect(navigate).toHaveBeenCalledWith('data', 'CRAWL_DRIFT', 'drift-fees-0710');
  });
});
