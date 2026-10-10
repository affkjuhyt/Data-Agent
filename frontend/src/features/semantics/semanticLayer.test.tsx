/**
 * The semantic layer against the v1 mock: the entity model and saving the agent's relationship
 * suggestions, the glossary with an ambiguous term and an unknown phrase turned into a term, and
 * the context-layer evaluation.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ContextEvalPage from './ContextEvalPage';
import EntityModelPage from './EntityModelPage';
import GlossaryPage from './GlossaryPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('EntityModelPage', () => {
  it('shows the model, switches the entity on the right, and saves suggested relationships', async () => {
    wrap(<EntityModelPage />);
    expect(await screen.findByRole('heading', { level: 2, name: 'Khoản vay' })).toBeInTheDocument();
    expect(screen.getByText('NPL_RATIO v4')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Chi nhánh/ }));
    expect(await screen.findByRole('heading', { level: 2, name: 'Chi nhánh' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Nhờ agent đề xuất quan hệ' }));
    const dialog = await screen.findByRole('dialog', { name: 'Agent đề xuất quan hệ' });
    expect(
      await within(dialog).findByText('Quan hệ thứ hai cần chủ sở hữu CRM xác nhận vì khác mã.'),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: /CRM.cust_no/ }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm quan hệ đã chọn' }));
    expect(await screen.findByText('Đã gửi 1 quan hệ chờ duyệt')).toBeInTheDocument();
  });
});

describe('GlossaryPage', () => {
  it('opens on the ambiguous term with its meanings, and turns an unknown phrase into a term', async () => {
    wrap(<GlossaryPage />);
    const panel = await screen.findByRole('complementary', { name: 'Thuật ngữ Miền Bắc' });
    expect(within(panel).getByText('Mặc định')).toBeInTheDocument();
    expect(within(panel).getByText(/nếu Data Space là Mạng lưới → hỏi người/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thêm “khách VIP”' }));
    expect(await screen.findByText('Đã thêm “Khách VIP” ở trạng thái nháp')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('“khách VIP”')).not.toBeInTheDocument());
    expect(await screen.findByRole('button', { name: 'Khách VIP' })).toBeInTheDocument();
  });

  it('adds a term only with both fields', async () => {
    wrap(<GlossaryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Thêm thuật ngữ' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm thuật ngữ' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm' }));
    expect(await within(dialog).findByText(/Nhập thuật ngữ và nghĩa/)).toBeInTheDocument();
  });
});

describe('ContextEvalPage', () => {
  it('shows the runs and the misses, and leads to the glossary', async () => {
    wrap(<ContextEvalPage />);
    expect(await screen.findByText('108 / 120')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /#56 · 02\/10 .* −2 Bị chặn/ })).toBeInTheDocument();
    expect(screen.getByText('Câu trả lời sai ở lượt #58')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy lại toàn bộ' }));
    expect(await screen.findByText('Đang chạy lại toàn bộ bộ câu hỏi chuẩn')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sửa từ điển nghiệp vụ' }));
    expect(navigate).toHaveBeenCalledWith('data', 'GLOSSARY');
  });
});
