/**
 * Duyệt nhiều người against the v1 mock: the chain of a proposal several people review, read
 * from the proposal and the caller's review request, asking for changes only with a note, and
 * approving.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ApprovalChainPage from './ApprovalChainPage';
import ProposalPage from './ProposalPage';

const navigate = vi.fn();
let routeId = 'proposal-214';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = 'proposal-214';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('ApprovalChainPage', () => {
  it('reads the chain from the proposal and my review request, me on the open step', async () => {
    wrap(<ApprovalChainPage />);
    const chain = await screen.findByRole('list', { name: 'Người duyệt' });
    const steps = within(chain).getAllByRole('listitem');
    expect(steps.map((s) => s.textContent)).toEqual([
      expect.stringContaining('Agent Đối soát'),
      expect.stringContaining('Lê Minh Châu'),
      expect.stringContaining('Chủ sở hữu dữ liệu'),
      expect.stringContaining('Phạm Đức Long'),
    ]);
    expect(within(steps[1]).getByText('Đồng ý')).toBeInTheDocument();
    expect(within(chain).getByText('Đang xem').closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    );
    expect(within(steps[3]).getByText('Chờ')).toBeInTheDocument();
    expect(screen.getByText(/^Hạn duyệt /)).toBeInTheDocument();
    expect(screen.getByText(/Agent Đối soát tạo đề xuất/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chuyển người duyệt' })).toBeInTheDocument();
    expect(screen.queryByText(/Thảo luận/)).not.toBeInTheDocument();
  });

  it('asks for changes only with a note, then approves', async () => {
    wrap(<ApprovalChainPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Yêu cầu sửa' }));
    const dialog = screen.getByRole('dialog', { name: 'Yêu cầu sửa' });
    expect(within(dialog).getByRole('button', { name: 'Gửi yêu cầu' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Huỷ' }));
    fireEvent.click(screen.getByRole('button', { name: 'Duyệt' }));
    expect(await screen.findByText('Đã duyệt; chuyển bước tiếp theo')).toBeInTheDocument();
    expect(await screen.findByText('Đề xuất này đã được xử lý.')).toBeInTheDocument();
  });

  it('is opened from a proposal with more than one reviewer', async () => {
    wrap(<ProposalPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Người duyệt & thảo luận' }));
    expect(navigate).toHaveBeenCalledWith('needs-you', 'APPROVAL', 'proposal-214');
  });
});
