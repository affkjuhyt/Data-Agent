/**
 * Trang chủ against the v1 mock: the metrics the viewer follows, their dashboards with the
 * agent's line, the guide link, and the mobile read (both layouts are in the DOM; CSS picks one).
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createHandlers } from '../../mocks/handlers';
import HomePage from './HomePage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({
    navigate,
    routeId: '',
    user: { id: 'u-hy', name: 'Đỗ Hải Yến' },
    dataSpace: { id: 'ds-rr', name: 'Rủi ro tín dụng' },
  }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

describe('HomePage', () => {
  it('shows the followed metrics and opens one in the catalog', async () => {
    render(<HomePage />);
    const metrics = await screen.findByRole('region', { name: 'Chỉ số của bạn' });
    expect(within(metrics).getByText('+0,22 điểm so với T8')).toBeInTheDocument();
    expect(within(metrics).getByText('DH Bắc Bộ đóng góp 74%')).toBeInTheDocument();
    fireEvent.click(within(metrics).getByRole('button', { name: /Nợ xấu bán lẻ/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC_CATALOG', 'metric-npl-ratio');
  });

  it('lists the dashboards with the agent line, and links the guide', async () => {
    render(<HomePage />);
    const dashboards = await screen.findByRole('region', { name: 'Dashboard của bạn' });
    const first = await within(dashboards).findByRole('button', { name: 'Rủi ro tín dụng · tuần' });
    fireEvent.click(first);
    expect(navigate).toHaveBeenCalledWith('analysis', 'DASHBOARD', expect.any(String));
    expect(within(dashboards).getAllByText(/^AI: /).length).toBeGreaterThan(0);
    fireEvent.click(screen.getByRole('button', { name: /Mới dùng\?/ }));
    expect(navigate).toHaveBeenCalledWith('home', 'HOW_IT_WORKS');
  });

  it('has the mobile read with the morning summary and the two link cards', async () => {
    render(<HomePage />);
    expect(await screen.findByText('Tóm tắt sáng nay')).toBeInTheDocument();
    expect(screen.getByText('Chào Hải Yến')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Agent đang chạy/ }));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUNS');
  });
});
