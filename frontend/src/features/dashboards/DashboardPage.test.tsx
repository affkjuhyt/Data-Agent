/**
 * Dashboard có tóm tắt AI against the v1 mock (/v1/dashboards, PROPOSAL): the summary, the
 * stale-data banner and marked tiles, "Viết lại", and the send-schedule dialog.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createDashboardHandlers } from '../../mocks/dashboards';
import DashboardPage from './DashboardPage';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createDashboardHandlers()));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
  routeId = undefined;
});
afterAll(() => server.close());

const renderPage = () =>
  render(
    <ToastProvider>
      <DashboardPage />
    </ToastProvider>,
  );

describe('DashboardPage', () => {
  it('shows the summary, the KPIs and the charts of the first dashboard', async () => {
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'Rủi ro tín dụng · tuần' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Điều đáng chú ý tuần này' })).toBeInTheDocument();
    expect(screen.getByText(/Dư nợ tăng 1,1%, dẫn đầu bởi vay mua nhà/)).toBeInTheDocument();
    expect(screen.getByText('182.430 tỷ')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: /Nợ xấu 12 tháng: T10 1,48/ })).toBeInTheDocument();
    expect(screen.getByText('DH Bắc Bộ')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Lịch gửi: thứ Hai 07:00/ })).toBeInTheDocument();
  });

  it('says which source is late, marks its tile, and opens the source', async () => {
    renderPage();
    const banner = await screen.findByText(/dữ liệu của 8 giờ trước vì DWH bán lẻ ngừng cập nhật/);
    expect(screen.getAllByRole('img', { name: 'Dữ liệu cũ' })).toHaveLength(1);
    fireEvent.click(
      within(banner.closest('[role="status"]') as HTMLElement).getByRole('button', {
        name: 'Xem nguồn',
      }),
    );
    expect(navigate).toHaveBeenCalledWith('data', 'SOURCE', 'src-dwh-retail');
  });

  it('rewrites the summary', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'Viết lại' }));
    expect(await screen.findByText('Agent đã viết lại phần tóm tắt')).toBeInTheDocument();
  });

  it('changes the send schedule with a preview of the next runs', async () => {
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /Lịch gửi: thứ Hai 07:00/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Lịch gửi · Rủi ro tín dụng · tuần' });
    expect(await within(dialog).findByText(/Gửi thứ Hai hằng tuần lúc 07:00/)).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('radio', { name: 'Hằng ngày lúc 07:00' }));
    expect(await within(dialog).findByText(/Gửi mỗi ngày lúc 07:00/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lưu lịch' }));

    expect(await screen.findByText('Đã lưu lịch gửi: hằng ngày 07:00')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /Lịch gửi: hằng ngày 07:00/ })).toBeInTheDocument(),
    );
  });

  it('says there is no dashboard yet when the list is empty', async () => {
    server.use(
      http.get('*/v1/dashboards', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'Chưa có dashboard nào' }),
    ).toBeInTheDocument();
  });

  it('keeps the page usable when the dashboard fails to load', async () => {
    routeId = 'dash-missing';
    renderPage();
    expect(await screen.findByText(/Không tải được dashboard/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});
