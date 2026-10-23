/**
 * Dữ liệu & tri thức against the v1 mock: the metric catalog with its filters, and a metric's
 * definition, lineage, versions, usage and quality read from GET /v1/metrics/{id} and GET
 * /v1/lineage; editing leads to the definition page.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import MetricCatalogPage from './MetricCatalogPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

describe('MetricCatalogPage', () => {
  it('opens the first metric with its lineage and the drafted change', async () => {
    render(<MetricCatalogPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /Nợ xấu \(%\)/ }),
    ).toBeInTheDocument();
    const lineage = await screen.findByRole('list', { name: 'Lineage' });
    // Upstream first, the metric, then its uses summed up.
    expect(
      within(lineage)
        .getAllByRole('listitem')
        .map((li) => li.textContent?.trim()),
    ).toEqual([
      'NguồnCore banking',
      'Datasetfact_loan_balance_daily',
      'Chỉ sốNPL_RATIO v4',
      'Sử dụng4 nơi dùng',
    ]);
    expect(screen.getByText('Agent Giám sát rủi ro')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở bản v5 nháp' }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC', 'metric-npl-ratio');
  });

  it('switches tabs', async () => {
    render(<MetricCatalogPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /Phiên bản/ }));
    expect(
      screen.getByRole('row', { name: /v5 Khoản cơ cấu lần 2 vào nhóm 3 — Nháp/ }),
    ).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /v3 Thêm phân khúc — Lưu trữ/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Sử dụng' }));
    expect(
      screen.getByRole('row', { name: /Báo cáo rủi ro tháng 9 Báo cáo v4/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Dùng ở 0 data product, 0 trang tri thức.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Chất lượng' }));
    expect(screen.getByText('Chưa có kiểm tra chất lượng.')).toBeInTheDocument();
  });

  it('filters and picks another metric', async () => {
    render(<MetricCatalogPage />);
    await screen.findByRole('heading', { level: 1, name: /Nợ xấu \(%\)/ });
    fireEvent.click(screen.getByRole('radio', { name: 'Nháp' }));
    const list = screen.getByRole('list', { name: 'Chỉ số' });
    expect(within(list).getAllByRole('button')).toHaveLength(1);
    fireEvent.click(screen.getByRole('radio', { name: 'Domain: Bán lẻ' }));
    fireEvent.click(within(list).getByRole('button', { name: /GROSS_MARGIN/ }));
    expect(
      await screen.findByRole('heading', { level: 1, name: /Biên lợi nhuận gộp/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('GROSS_MARGIN = …')).toBeInTheDocument();
  });

  it('R1-07 creates a metric as a draft and opens its page', async () => {
    render(<MetricCatalogPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo chỉ số' }));
    const dialog = await screen.findByRole('dialog', { name: 'Tạo chỉ số' });
    fireEvent.change(within(dialog).getByPlaceholderText('NET_REVENUE'), { target: { value: 'loi_gop' } });
    fireEvent.change(within(dialog).getByPlaceholderText('Doanh thu thuần'), { target: { value: 'Lãi gộp' } });
    fireEvent.change(within(dialog).getByPlaceholderText('doanh_thu - chiet_khau'), {
      target: { value: 'doanh_thu - gia_von' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo bản nháp' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'METRIC', 'metric-loi_gop'));
  });

  it('says what a code must look like before sending it', async () => {
    render(<MetricCatalogPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo chỉ số' }));
    const dialog = await screen.findByRole('dialog', { name: 'Tạo chỉ số' });
    fireEvent.change(within(dialog).getByPlaceholderText('NET_REVENUE'), { target: { value: '1 sai' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo bản nháp' }));
    expect(await within(dialog).findByText(/Mã chỉ số bắt đầu bằng chữ/)).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });
});
