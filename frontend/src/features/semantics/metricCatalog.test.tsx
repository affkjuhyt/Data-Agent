/**
 * Dữ liệu & tri thức against the v1 mock: the metric catalog with its filters, and a metric's
 * definition, lineage, versions, usage and quality; editing leads to the definition page.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
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
      await screen.findByRole('heading', { level: 1, name: /Tỷ lệ nợ xấu/ }),
    ).toBeInTheDocument();
    const lineage = await screen.findByRole('list', { name: 'Lineage' });
    expect(within(lineage).getByText('NPL_RATIO v4')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở bản v5 nháp' }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC', 'metric-npl-ratio');
  });

  it('switches tabs', async () => {
    render(<MetricCatalogPage />);
    fireEvent.click(await screen.findByRole('tab', { name: /Phiên bản/ }));
    expect(
      screen.getByRole('row', { name: /v3 Thêm phân khúc 01\/09\/2025 Lưu trữ/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Sử dụng' }));
    expect(screen.getByText('23 artifact · 5 agent · 9 phiên trong 30 ngày')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Chất lượng' }));
    expect(
      screen.getByText('Tây Nguyên thiếu 2 chi nhánh trong T8 (đã ghi chú)'),
    ).toBeInTheDocument();
  });

  it('filters and picks another metric', async () => {
    render(<MetricCatalogPage />);
    await screen.findByRole('heading', { level: 1, name: /Tỷ lệ nợ xấu/ });
    fireEvent.click(screen.getByRole('radio', { name: 'Nháp' }));
    const list = screen.getByRole('list', { name: 'Chỉ số' });
    expect(within(list).getAllByRole('button')).toHaveLength(1);
    fireEvent.click(within(list).getByRole('button', { name: /COST_OF_RISK/ }));
    expect(
      await screen.findByRole('heading', { level: 1, name: /Chi phí rủi ro tín dụng/ }),
    ).toBeInTheDocument();
  });
});
