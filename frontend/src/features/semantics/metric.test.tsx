/**
 * Chỉ số · Định nghĩa against the v1 mock: the version in use, who owns it, opening the draft
 * through "Sửa" and backtesting it on six periods.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createMetricHandlers } from '../../mocks/metrics';
import MetricPage from './MetricPage';

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate: vi.fn(), routeId: 'metric-npl-ratio' }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createMetricHandlers()));
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <MetricPage />
    </ToastProvider>,
  );

describe('MetricPage', () => {
  it('shows the certified definition, its use and its stewards', async () => {
    page();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Nợ xấu \(%\) · NPL_RATIO/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/v4 đang dùng · chứng nhận bởi Phạm Đức Long 12\/06/),
    ).toBeInTheDocument();
    expect(screen.getByText(/dùng ở 23 artifact, 5 agent, 2 báo cáo định kỳ/)).toBeInTheDocument();
    expect(screen.getByText('Thấp hơn là tốt')).toBeInTheDocument();
    expect(screen.getByText('Số cuối kỳ, theo ngày nghiệp vụ')).toBeInTheDocument();
    expect(screen.getByText(/is_internal_loan = false/)).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Chủ sở hữu Phạm Đức Long/ })).toBeInTheDocument();
  });

  it('opens the draft from "Sửa" and backtests it on six periods', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Sửa → bản nháp v5' }));
    expect(screen.getByRole('heading', { name: 'Định nghĩa · v5' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy thử trên 6 kỳ' }));
    expect(await screen.findByText('Đang chạy thử v5 trên 6 kỳ')).toBeInTheDocument();
    const v5 = await screen.findByRole('button', { name: /v5 Khoản cơ cấu lần 2/ });
    expect(await within(v5).findByText('Đang chạy thử')).toBeInTheDocument();
  });
});
