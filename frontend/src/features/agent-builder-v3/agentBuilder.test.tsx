/**
 * Agent authoring against the v1 mock: drafting from words, test run and activation,
 * the config tab (versioned save, autonomy rule), the memory tab, and the runs tab.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AgentConfigPage from './AgentConfigPage';
import CreateAgentPage from './CreateAgentPage';
import { lastUsedText } from './MemoryTab';

const navigate = vi.fn();
let routeId = '';
let dataSpace: { id: string; name: string } | null = { id: 'ds-1', name: 'Tín dụng' };
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId, dataSpace }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
// Test-run lookups fall through to the run handlers, so these go first.
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);
const SLOW = { timeout: 3000 };

describe('CreateAgentPage', () => {
  it('drafts from a description, takes a correction, test-runs and activates', async () => {
    wrap(<CreateAgentPage />);
    expect(screen.getByRole('button', { name: 'Tạo agent' })).toHaveAttribute('aria-disabled', 'true');

    fireEvent.change(screen.getByLabelText('Bạn muốn agent làm gì?'), {
      target: {
        value:
          'Mỗi sáng thứ Hai, xem nợ quá hạn 30+ ngày theo chi nhánh và gửi tóm tắt cho tôi qua Teams.',
      },
    });
    expect(await screen.findByText('Agent Theo dõi quá hạn chi nhánh', {}, SLOW)).toBeInTheDocument();
    expect(screen.getByText('cần xem')).toBeInTheDocument();
    expect(screen.getByText(/OVERDUE_30 chưa được chứng nhận/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Sửa Khi nào chạy/ }));
    const input = screen.getByRole('textbox', { name: 'Khi nào chạy' });
    fireEvent.change(input, { target: { value: 'Thứ Hai 08:00 hằng tuần' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(await screen.findByText('Thứ Hai 08:00 hằng tuần')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chạy thử một lần' }));
    const drawer = await screen.findByRole('dialog');
    expect(within(drawer).getByText('Chạy thử · Agent Theo dõi quá hạn chi nhánh')).toBeInTheDocument();
    expect(await within(drawer).findByText('11/12 đạt', {}, SLOW)).toBeInTheDocument();
    expect(within(drawer).getByText('Tin nhắn Teams sẽ gửi')).toBeInTheDocument();
    expect(within(drawer).getByText(/1 tình huống không đạt/)).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('button', { name: 'Kích hoạt' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('agents', 'AGENT', 'agent-new-1'));
  });

  it('takes a description handed over from a session and drops it from the URL', async () => {
    window.history.replaceState(null, '', '/agents/builder?description=Mỗi%20sáng%20xem%20nợ%20quá%20hạn%20theo%20chi%20nhánh&from_session=s-1');
    wrap(<CreateAgentPage />);
    expect(screen.getByLabelText('Bạn muốn agent làm gì?')).toHaveValue('Mỗi sáng xem nợ quá hạn theo chi nhánh');
    expect(window.location.search).toBe('');
    expect(await screen.findByText('Agent Theo dõi quá hạn chi nhánh', {}, SLOW)).toBeInTheDocument();
  });

  it('names the Data Space the agent will belong to, and needs one', async () => {
    const { unmount } = wrap(<CreateAgentPage />);
    expect(screen.getByText(/Agent thuộc Data Space Tín dụng/)).toBeInTheDocument();
    unmount();
    dataSpace = null;
    try {
      wrap(<CreateAgentPage />);
      fireEvent.click(await screen.findByRole('button', { name: /Agent Báo cáo định kỳ/ }));
      expect(await screen.findByText('Phải hỏi bạn khi')).toBeInTheDocument();
      expect(screen.getAllByText(/Chọn một Data Space ở thanh trên/).length).toBeGreaterThan(0);
      expect(screen.getByRole('button', { name: 'Tạo agent' })).toHaveAttribute('aria-disabled', 'true');
    } finally {
      dataSpace = { id: 'ds-1', name: 'Tín dụng' };
    }
  });

  it('starts from a template and cancels back to the team', async () => {
    wrap(<CreateAgentPage />);
    fireEvent.click(await screen.findByRole('button', { name: /Agent Báo cáo định kỳ/ }));
    expect(screen.getByLabelText('Bạn muốn agent làm gì?')).toHaveValue(
      'Soạn bản tin tuần từ chỉ số và biến động, bạn duyệt trước khi gửi',
    );
    expect(await screen.findByText('Phải hỏi bạn khi')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Huỷ' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'TEAM');
  });
});

describe('AgentConfigPage', () => {
  it('shows triggers, autonomy and permissions, and saves a new version', async () => {
    routeId = 'agent-reconcile';
    wrap(<AgentConfigPage />);
    expect(await screen.findByText('Job nạp ld_loans xong và lệch > 0,1% so với hôm qua')).toBeInTheDocument();
    expect(screen.getByText('Đỗ Hải Yến (chủ sở hữu)')).toBeInTheDocument();
    const save = screen.getByRole('button', { name: 'Lưu thành phiên bản v6' });
    expect(save).toHaveAttribute('aria-disabled', 'true');

    fireEvent.click(screen.getByRole('radio', { name: 'Tự làm, báo sau' }));
    fireEvent.click(save);
    expect(await screen.findByText(/cần bộ đánh giá đạt từ 95% và chủ sở hữu Data Space duyệt\.$/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Đề xuất, người duyệt' }));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Câu hỏi có hơn một cách hiểu hợp lệ' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thành phiên bản v6' }));
    expect(await screen.findByRole('button', { name: 'Lưu thành phiên bản v7' })).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Câu hỏi có hơn một cách hiểu hợp lệ' })).not.toBeChecked();
  });

  it('edits a trigger in place and saves it without a new version', async () => {
    routeId = 'agent-reconcile';
    wrap(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sửa trigger Thứ Hai 07:00' }));
    const input = screen.getByRole('textbox', { name: 'Điều kiện Theo lịch' });
    fireEvent.change(input, { target: { value: 'Thứ Hai 06:30' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu trigger' }));
    expect(await screen.findByText('Đã lưu trigger')).toBeInTheDocument();
  });

  it('lists, forgets and adds memories', async () => {
    routeId = 'agent-analysis';
    wrap(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Bộ nhớ' }));
    expect(await screen.findByText('“Miền Bắc” gồm DH Bắc Bộ, ĐB sông Hồng, Trung du')).toBeInTheDocument();
    expect(screen.getByText('Data Space · chờ duyệt')).toBeInTheDocument();
    expect(screen.getByText('hôm qua')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /^Quên: Báo cáo cho Ban điều hành/ }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Quên' }));
    await waitFor(() => expect(screen.queryByText(/Báo cáo cho Ban điều hành/)).toBeNull());

    fireEvent.click(screen.getByRole('button', { name: 'Thêm ghi nhớ' }));
    const add = screen.getByRole('dialog', { name: 'Thêm ghi nhớ' });
    expect(within(add).getByRole('combobox', { name: 'Loại' })).toHaveTextContent('Sở thích người dùng');
    expect(within(add).getByRole('combobox', { name: 'Hạn' })).toHaveTextContent('90 ngày');
    const field = within(add).getByRole('textbox', { name: 'Nội dung' });
    fireEvent.change(field, { target: { value: 'Khách hàng 0123456789 là khách VIP' } });
    fireEvent.click(within(add).getByRole('button', { name: 'Lưu ghi nhớ' }));
    expect(await within(add).findByText(/không được chứa dữ liệu cá nhân/)).toBeInTheDocument();

    fireEvent.change(field, { target: { value: 'Quý tài chính bắt đầu từ tháng 4' } });
    fireEvent.click(within(add).getByRole('button', { name: 'Lưu ghi nhớ' }));
    expect(await screen.findByText('Quý tài chính bắt đầu từ tháng 4')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('edits a memory in a dialog', async () => {
    routeId = 'agent-analysis';
    wrap(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Bộ nhớ' }));
    fireEvent.click(await screen.findByRole('button', { name: /^Sửa: Khi giải thích nợ xấu/ }));
    const dialog = screen.getByRole('dialog', { name: 'Sửa ghi nhớ' });
    expect(within(dialog).getByText('Thay đổi áp dụng cho lượt chạy tiếp theo.')).toBeInTheDocument();
    expect(within(dialog).queryByRole('combobox', { name: 'Loại' })).not.toBeInTheDocument();
    expect(within(dialog).getByRole('combobox', { name: 'Hạn' })).toHaveTextContent('Tới khi sai 2 lần');

    // The api keeps a memory's scope; the dialog shows it without a picker.
    expect(within(dialog).queryByRole('combobox', { name: 'Phạm vi' })).not.toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Nội dung' }), {
      target: { value: 'Khi giải thích nợ xấu, tách riêng khoản tái cơ cấu' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Đã lưu ghi nhớ')).toBeInTheDocument();
    expect(await screen.findByText('Khi giải thích nợ xấu, tách riêng khoản tái cơ cấu')).toBeInTheDocument();
  });

  it('asks before leaving the config tab with unsaved changes', async () => {
    routeId = 'agent-reconcile';
    wrap(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole('radio', { name: 'Chỉ gợi ý' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Bộ nhớ' }));
    let dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Bỏ thay đổi chưa lưu?')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Huỷ' }));
    expect(screen.getByRole('tab', { name: 'Cấu hình' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('radio', { name: 'Chỉ gợi ý' })).toBeChecked();

    fireEvent.click(screen.getByRole('tab', { name: 'Bộ nhớ' }));
    dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ thay đổi' }));
    expect(screen.getByRole('tab', { name: 'Bộ nhớ' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('tab', { name: 'Cấu hình' }));
    expect(screen.getByRole('button', { name: 'Lưu thành phiên bản v6' })).toHaveAttribute('aria-disabled', 'true');
  });

  it('lists the runs of this agent and opens one', async () => {
    routeId = 'agent-reconcile';
    wrap(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole('tab', { name: 'Lượt chạy' }));
    const rows = await screen.findAllByRole('row');
    expect(rows.length).toBeGreaterThan(1);
    fireEvent.click(within(rows[1]).getAllByRole('button')[0]);
    expect(navigate).toHaveBeenCalledWith('agents', 'RUN', expect.any(String));
  });

  it('refuses an id that is not a plain id', async () => {
    routeId = '..%2Fadmin';
    wrap(<AgentConfigPage />);
    expect(await screen.findByText(/Không tải được agent/)).toBeInTheDocument();
  });
});

describe('lastUsedText', () => {
  it('writes the last use the way the table does', () => {
    const now = new Date(2026, 9, 10, 15, 0);
    expect(lastUsedText(new Date(2026, 9, 10, 10, 20).toISOString(), now)).toBe('hôm nay 10:20');
    expect(lastUsedText(new Date(2026, 9, 9, 8, 0).toISOString(), now)).toBe('hôm qua');
    expect(lastUsedText(new Date(2026, 9, 6, 9, 0).toISOString(), now)).toBe('06/10');
    expect(lastUsedText(null, now)).toBe('—');
  });
});
