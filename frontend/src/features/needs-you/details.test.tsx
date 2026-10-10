/**
 * The two detail pages of Cần bạn against the v1 mock: deciding a proposal and choosing between
 * two sources, both leaving the request resolved.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { listInbox } from '../../lib/v1/api';
import { createHandlers } from '../../mocks/handlers';
import ConflictPage from './ConflictPage';
import ProposalPage from './ProposalPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('ProposalPage', () => {
  it('shows the change, its effect and the checks, and accepting resolves the request', async () => {
    routeId = 'pr-0318';
    wrap(<ProposalPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /NPL_RATIO v5/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Bản đề xuất v5')).toBeInTheDocument();
    expect(screen.getByText('+0,12 điểm · 214 khoản')).toBeInTheDocument();
    expect(screen.getByText('Bộ câu hỏi chuẩn 96,8% (v4: 96,5%)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    const open = await listInbox({ status: 'open' });
    expect(open.items.map((r) => r.id)).not.toContain('hitl-npl-v5');
  });

  it('asks for a reason before rejecting', async () => {
    routeId = 'pr-0318';
    wrap(<ProposalPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Từ chối' }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(
      await screen.findByText('Ghi lý do từ chối để agent biết cần sửa gì.'),
    ).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole('textbox', { name: 'Lý do từ chối (bắt buộc)' }), {
      target: { value: 'QĐ chưa áp cho kỳ đã nộp' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
  });
});

describe('ConflictPage', () => {
  it('shows both numbers and sends the chosen source with the rule', async () => {
    routeId = 'hitl-core-vs-dwh';
    wrap(<ConflictPage />);
    expect(await screen.findByText('2.296,4 tỷ')).toBeInTheDocument();
    expect(screen.getByText('2.298,2 tỷ')).toBeInTheDocument();
    expect(screen.getByText('Chênh 1,8 tỷ (0,08%)')).toBeInTheDocument();
    expect(screen.getByLabelText(/Dùng Core banking/)).toBeChecked();
    expect(screen.getByLabelText(/Ghi nhớ thành quy tắc/)).toBeChecked();

    fireEvent.click(screen.getByLabelText('Dùng DWH bán lẻ (theo số đã tổng hợp cho báo cáo)'));
    fireEvent.click(screen.getByRole('button', { name: 'Chọn phương án' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    const open = await listInbox({ status: 'open' });
    expect(open.items.map((r) => r.id)).not.toContain('hitl-core-vs-dwh');
  });
});
