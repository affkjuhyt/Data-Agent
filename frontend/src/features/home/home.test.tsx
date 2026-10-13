/**
 * Trang chủ against the v1 mock: the guide link, and the mobile read with the newest digest
 * (both layouts are in the DOM; CSS picks one). A2 cannot read back the followed metrics, so
 * "Chỉ số của bạn" is hidden.
 */
import { fireEvent, render, screen } from '@testing-library/react';
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
  it('links the guide, and has no dashboard block (the design has no dashboards)', async () => {
    render(<HomePage />);
    await screen.findByText('Tóm tắt sáng nay');
    expect(screen.queryByRole('region', { name: 'Chỉ số của bạn' })).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Dashboard của bạn' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Mới dùng\?/ }));
    expect(navigate).toHaveBeenCalledWith('home', 'HOW_IT_WORKS');
  });

  it('has the mobile read with the morning summary and the two link cards', async () => {
    render(<HomePage />);
    expect(await screen.findByText('Tóm tắt sáng nay')).toBeInTheDocument();
    expect(screen.getByText(/Kỳ 09\/2026 chưa chốt được/)).toBeInTheDocument();
    expect(screen.getByText('Bản tin lúc 06:00')).toBeInTheDocument();
    expect(screen.getByText('Chào Hải Yến')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Agent đang chạy/ }));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUNS');
  });
});
