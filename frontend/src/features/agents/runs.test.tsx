/**
 * Lượt chạy against the v1 mock: filtering the list, and the story and controls of one run.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AgentTeamPage from './AgentTeamPage';
import RunDetailPage from './RunDetailPage';
import RunsPage from './RunsPage';
import { runDuration, shortCost } from './runStatus';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, routeId, tenant: { tenantName: 'Rủi ro tín dụng' } }),
}));

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
    // The step under way opens first, with whose permissions it runs and the query it sent.
    const running = await screen.findByRole('button', { name: /tool\.query overdue_check/ });
    expect(running).toHaveAttribute('aria-pressed', 'true');
    expect(within(running).getByText('đang chạy')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'tool.query overdue_check' })).toBeInTheDocument();
    expect(screen.getByText('Đỗ Hải Yến · chính sách POL-002 áp dụng')).toBeInTheDocument();
    expect(screen.getByText(/WHERE l\.overdue_days <> s\.overdue_days/)).toBeInTheDocument();
    expect(screen.queryByText('Kế hoạch · 5 bước')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /llm\.reason/ }));
    expect(screen.getByRole('heading', { name: 'llm.reason' })).toBeInTheDocument();
    expect(screen.getByText(/· chạy 12 giây/)).toBeInTheDocument();
  });

  it('leads a failed run with the error, what stayed safe, and reruns it', async () => {
    let body: unknown;
    // The retry endpoint is a PROPOSAL; answered here until the shared mock registers it.
    server.use(
      http.post('*/v1/runs/:action', async ({ params, request }) => {
        if (params.action !== 'run-409:retry') return undefined;
        body = await request.json();
        return HttpResponse.json({ id: 'run-430' }, { status: 202 });
      }),
    );
    routeId = 'run-409';
    wrap(<RunDetailPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Không kết nối được DWH bán lẻ sau 3 lần thử',
    );
    expect(screen.getByText('Lỗi · đã dừng an toàn')).toBeInTheDocument();
    expect(screen.getByText(/Tạm dừng, sẽ tự thử lại khi nguồn có tín hiệu/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kết nối DWH bán lẻ/ })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Xem kỹ thuật' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Chạy với nguồn dự phòng' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-430'));
    expect(body).toEqual({ use_fallback: true });
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

describe('AgentTeamPage', () => {
  it('opens what a busy agent is doing now, and its configuration', async () => {
    wrap(<AgentTeamPage />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Thao tác với Agent Chất lượng dữ liệu' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Đang làm gì' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-415'));

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Agent Chất lượng dữ liệu' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Cấu hình' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'AGENT', 'agent-dq');
  });
});
