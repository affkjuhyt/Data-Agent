/**
 * Thu thập ngoài against the v1 mock: the source list and running one, registering a source
 * (a forbidden source cannot be ticked, saving opens the schema), and approving a schema only
 * after the agent's unsure field is mapped; drift, record review, and consolidation.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import CrawlConsolidationPage from './CrawlConsolidationPage';
import CrawlDriftPage from './CrawlDriftPage';
import CrawlRegisterPage from './CrawlRegisterPage';
import CrawlReviewPage from './CrawlReviewPage';
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

describe('CrawlReviewPage', () => {
  it('confirms the anomaly as the agent suggests, then publishes once nothing blocks', async () => {
    routeId = 'crun-deposit-1010';
    wrap(<CrawlReviewPage />);
    expect(
      await screen.findByText(/412 bản ghi trích được · 6 cần người xem · 4 mục chặn/),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Phát hành lượt' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    const card = await screen.findByRole('region', { name: 'Bản ghi đang xem' });
    expect(within(card).getByText(/lệch 4,1 MAD/)).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Xác nhận theo đề xuất' }));
    expect(await screen.findByText('Đã sửa Ngân hàng K · 12 tháng')).toBeInTheDocument();

    // The other three blocking records share no reason, so decide them one by one.
    for (const title of [
      'Ngân hàng B · 6 tháng',
      'Ngân hàng T · 24 tháng',
      'Ngân hàng Q · 1 tháng',
    ]) {
      fireEvent.click(await screen.findByRole('button', { name: new RegExp(title) }));
      const current = await screen.findByRole('region', { name: 'Bản ghi đang xem' });
      fireEvent.click(within(current).getByRole('button', { name: 'Xác nhận' }));
      expect(await screen.findByText(`Đã xác nhận ${title}`)).toBeInTheDocument();
    }
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Phát hành lượt' })).not.toHaveAttribute(
        'aria-disabled',
      ),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Phát hành lượt' }));
    expect(await screen.findByText('Đã phát hành')).toBeInTheDocument();
  });

  it('rejects a record only with a reason, and bulk-reviews by reason', async () => {
    routeId = 'crun-deposit-1010';
    wrap(<CrawlReviewPage />);
    const card = await screen.findByRole('region', { name: 'Bản ghi đang xem' });
    fireEvent.click(within(card).getByRole('button', { name: 'Bỏ bản ghi' }));
    const dialog = screen.getByRole('alertdialog', { name: /Bỏ bản ghi Ngân hàng K/ });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ bản ghi' }));
    expect(await within(dialog).findByText('Ghi lý do bỏ bản ghi.')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Quay lại' }));

    fireEvent.click(screen.getByRole('button', { name: 'Duyệt hàng loạt theo lý do' }));
    const bulk = screen.getByRole('alertdialog', { name: 'Duyệt hàng loạt theo lý do' });
    fireEvent.change(within(bulk).getByLabelText('Lý do (bắt buộc)'), {
      target: { value: 'Đã đối chiếu biểu PDF gốc' },
    });
    fireEvent.click(within(bulk).getByRole('button', { name: 'Áp dụng' }));
    expect(await screen.findByText('Đã duyệt 1 bản ghi')).toBeInTheDocument();
  });

  it('is opened from the source list by its waiting count', async () => {
    wrap(<CrawlSourcesPage />);
    fireEvent.click(
      await screen.findByRole('button', {
        name: '3 bản ghi chờ duyệt của Lãi suất huy động · 30 ngân hàng',
      }),
    );
    expect(navigate).toHaveBeenCalledWith('data', 'CRAWL_RUN', 'crun-deposit-1010');
  });
});

describe('CrawlConsolidationPage', () => {
  it('decides the entity links, and keeps publishing blocked by the open conflict', async () => {
    routeId = 'cons-deposit-w41';
    wrap(<CrawlConsolidationPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Hợp nhất dữ liệu · tuần 41' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/chờ 2 liên kết thực thể và 1 xung đột trước khi phát hành/),
    ).toBeInTheDocument();
    expect(screen.getByText('Thực thể · 2 chờ')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /^12 tháng/ })).toHaveClass('bg-accent');
    const publish = screen.getByRole('button', { name: 'Phát hành dataset v42' });
    expect(publish).toHaveAttribute('aria-disabled', 'true');

    const vietbank = screen.getByRole('listitem', { name: 'Liên kết “VietBank”' });
    fireEvent.click(within(vietbank).getByRole('button', { name: 'Gộp' }));
    expect(
      await screen.findByText('Đã gộp “VietBank” vào Ngân hàng TMCP Việt Nam Thương Tín'),
    ).toBeInTheDocument();
    const bv = await screen.findByRole('listitem', { name: 'Liên kết “Ngân hàng BV”' });
    fireEvent.click(within(bv).getByRole('button', { name: 'Tách' }));
    expect(await screen.findByText('Đã tách “Ngân hàng BV”')).toBeInTheDocument();
    expect(await screen.findByText('Nhận diện thực thể chờ duyệt · 0')).toBeInTheDocument();
    expect(screen.getByText('Thực thể')).toBeInTheDocument();
    expect(screen.getByText(/chờ 1 xung đột trước khi phát hành/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Phát hành dataset v42' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('is opened from the latest dataset in the source list', async () => {
    wrap(<CrawlSourcesPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Lượt hợp nhất kế tiếp' }));
    expect(navigate).toHaveBeenCalledWith('data', 'CRAWL_MERGE', 'cons-deposit-w41');
  });
});
