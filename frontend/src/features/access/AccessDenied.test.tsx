/**
 * Thiếu quyền against the v1 mock: a metric over a Data Space the user is not in shows what was
 * refused instead of the data, and the access request goes out from the page.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import MetricPage from '../semantics/MetricPage';

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate: vi.fn(), routeId: 'metric-cost-of-risk' }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterAll(() => server.close());

describe('AccessDenied', () => {
  it('names what was refused, never the data, and sends the access request', async () => {
    render(
      <ToastProvider>
        <MetricPage />
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', {
        name: 'Bạn chưa có quyền với dữ liệu trích lập dự phòng',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Chủ sở hữu: Trần Quốc An/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Mức quyền' })).toHaveTextContent(
      'Xem tổng hợp theo vùng (không chi tiết khoản vay)',
    );
    expect(screen.getByRole('button', { name: 'Thời hạn' })).toHaveTextContent('30 ngày');
    const reason = screen.getByRole('textbox', { name: 'Lý do' });
    expect(reason).toHaveValue('Điều tra nợ xấu bán lẻ T9 · phiên của Đỗ Hải Yến');

    fireEvent.change(reason, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }));
    expect(await screen.findByText('Không gửi được: Ghi lý do xin quyền.')).toBeInTheDocument();

    fireEvent.change(reason, { target: { value: 'Báo cáo chi phí rủi ro quý 3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }));
    await waitFor(() =>
      expect(
        screen.getByText(/Đã gửi yêu cầu AR-\d+\. Trần Quốc An nhận trong Cần bạn/),
      ).toBeInTheDocument(),
    );
  });
});
