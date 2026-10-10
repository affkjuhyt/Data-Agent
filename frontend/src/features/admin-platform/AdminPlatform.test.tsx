/**
 * The platform admin and personal settings screens against their v1 mock: what each shows,
 * the actions that change state (rollback, add model, add integration, save notifications)
 * and where each link goes.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import type { ReactElement } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createAdminPlatformHandlers } from '../../mocks/adminPlatform';
import { createHandlers } from '../../mocks/handlers';
import AgentOperationsPage from './AgentOperationsPage';
import ChannelRulesPage from './ChannelRulesPage';
import IntegrationsPage from './IntegrationsPage';
import ModelsPage from './ModelsPage';
import MonitoringPage from './MonitoringPage';
import NotificationsPage from './NotificationsPage';
import ProfilePage from './ProfilePage';
import ShortcutsPage from './ShortcutsPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createAdminPlatformHandlers(), ...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
});
afterAll(() => server.close());

const renderPage = (page: ReactElement) => render(<ToastProvider>{page}</ToastProvider>);

describe('MonitoringPage', () => {
  it('shows KPIs, services, models and open alerts; "Xem nhật ký" opens Kiểm toán', async () => {
    renderPage(<MonitoringPage />);
    expect(await screen.findByText('15 / 16')).toBeInTheDocument();
    expect(screen.getByText('Ingestion khởi động lại 07:42')).toBeInTheDocument();
    expect(screen.getByText('6,8 giây')).toBeInTheDocument();
    expect(screen.getByText('mục tiêu ≤ 8 giây')).toBeInTheDocument();
    expect(screen.getByText('▲ 0,1 điểm')).toBeInTheDocument();
    const query = screen.getByText('Query engine').closest('tr')!;
    expect(within(query).getByText('Chậm')).toBeInTheDocument();
    expect(within(query).getByText('4,9 s')).toBeInTheDocument();
    expect(screen.getByText('18,2 triệu')).toBeInTheDocument();
    expect(screen.getByText('12 trang')).toBeInTheDocument();
    expect(screen.getByText('Dung lượng MinIO')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem nhật ký' }));
    expect(navigate).toHaveBeenCalledWith('admin', 'AUDIT');
  });
});

describe('AgentOperationsPage', () => {
  it('shows the roll-up for 7 days and sends each issue to the place that fixes it', async () => {
    renderPage(<AgentOperationsPage />);
    expect(await screen.findByText('286')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: '7 ngày' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('94,1%')).toBeInTheDocument();
    expect(screen.getByText('trung vị phản hồi 6 phút')).toBeInTheDocument();
    expect(screen.getByText('9 · 3,1%')).toBeInTheDocument();
    expect(screen.getByText('4,2 tr')).toBeInTheDocument();
    expect(screen.getByText('2 ph 10 s')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Xem trace' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-dq-0210');
    fireEvent.click(screen.getByRole('button', { name: 'So sánh 2 cấu hình' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'EVALUATE', 'agent-report');

    fireEvent.click(screen.getByRole('radio', { name: '30 ngày' }));
    await waitFor(() =>
      expect(screen.getByRole('radio', { name: '30 ngày' })).toHaveAttribute(
        'aria-checked',
        'true',
      ),
    );
  });
});

describe('ModelsPage', () => {
  it('lists the model per task and rolls a task back after confirming', async () => {
    renderPage(<ModelsPage />);
    expect(await screen.findByText('Qwen3-72B · GPU cụm A')).toBeInTheDocument();
    expect(screen.getByText('4,1 giây')).toBeInTheDocument();
    expect(screen.getByText('Chặn yêu cầu lấy dữ liệu cá nhân hàng loạt')).toBeInTheDocument();
    expect(await screen.findByText(/Cao điểm 83% lúc 15:00/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Soạn báo cáo' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Quay lại LLM-L 32B/ }));
    const dialog = screen.getByRole('alertdialog', { name: 'Quay lại LLM-L 32B?' });
    expect(
      within(dialog).getByText(
        'Chi phí soạn báo cáo tăng khoảng 3,5 lần. Bộ đánh giá của Agent Báo cáo sẽ chạy lại.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Quay lại' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).toBeNull());
    const row = (await screen.findByText('LLM-L 32B')).closest('tr')!;
    expect(within(row).getByText('Soạn báo cáo')).toBeInTheDocument();
  });

  it('starts the eval run', async () => {
    renderPage(<ModelsPage />);
    await screen.findByText('Sinh SQL');
    fireEvent.click(screen.getByRole('button', { name: 'Chạy bộ đánh giá' }));
    expect(
      await screen.findByText('Đã bắt đầu chạy bộ đánh giá cho mọi tác vụ.'),
    ).toBeInTheDocument();
  });

  it('adds a model, which shows as being evaluated', async () => {
    renderPage(<ModelsPage />);
    await screen.findByText('Sinh SQL');
    fireEvent.click(screen.getByRole('button', { name: 'Thêm mô hình' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm mô hình' });
    const submit = within(dialog).getByRole('button', { name: 'Thêm & đánh giá' });
    expect(submit).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(within(dialog).getByLabelText('Tên'), { target: { value: 'LLM-M 24B' } });
    fireEvent.change(within(dialog).getByLabelText('Địa chỉ'), {
      target: { value: 'http://llm-gw.noibo/m24' },
    });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(await screen.findByText(/LLM-M 24B/)).toBeInTheDocument();
    expect(screen.getByText(/đang đánh giá/)).toBeInTheDocument();
  });
});

describe('IntegrationsPage', () => {
  it('lists clients, shows the usage of the selected one and registers a new one', async () => {
    renderPage(<IntegrationsPage />);
    expect(await screen.findByText('Copilot nội bộ')).toBeInTheDocument();
    expect(screen.getByText('MCP · Ngô Thanh Bình')).toBeInTheDocument();
    expect(screen.getByText('12.480')).toBeInTheDocument();
    expect(screen.getByText('Sắp hết hạn')).toBeInTheDocument();
    expect(await screen.findByText('ask 71% · get_metric 24%')).toBeInTheDocument();
    expect(screen.getByText('5.000 lượt/ngày · đã dùng 41%')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Excel add-in/ }));
    expect(await screen.findByText('Excel add-in · 7 ngày')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký client' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm tích hợp' });
    fireEvent.change(within(dialog).getByLabelText('Địa chỉ'), {
      target: { value: 'https://mcp-ticket.noibo/v1' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Kết nối & kiểm tra' }));
    expect(await screen.findByText('mcp-ticket.noibo · 7 ngày')).toBeInTheDocument();
  });
});

describe('NotificationsPage', () => {
  it('keeps the locked cell on, and saves a changed choice', async () => {
    renderPage(<NotificationsPage />);
    const locked = await screen.findByRole('checkbox', {
      name: 'Agent cần tôi quyết định · Trong ứng dụng (luôn bật)',
    });
    expect(locked).toBeDisabled();
    expect(locked).toBeChecked();
    const save = screen.getByRole('button', { name: 'Lưu' });
    expect(save).toHaveAttribute('aria-disabled', 'true');

    const zalo = screen.getByRole('checkbox', { name: 'Agent cần tôi quyết định · Zalo' });
    expect(zalo).not.toBeChecked();
    fireEvent.click(zalo);
    expect(zalo).toBeChecked();
    expect(save).not.toHaveAttribute('aria-disabled');
    fireEvent.click(save);
    expect(await screen.findByText('Đã lưu cách nhận thông báo.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Lưu' })).toHaveAttribute('aria-disabled', 'true'),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Gửi thử' }));
    expect(
      await screen.findByText('Đã gửi thông báo thử tới Trong ứng dụng, Teams, Email.'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'quy tắc kênh' }));
    expect(navigate).toHaveBeenCalledWith('settings', 'CHANNEL_RULES');
  });
});

describe('settings and channel rules', () => {
  it('shows the profile from the directory', async () => {
    renderPage(<ProfilePage />);
    expect(await screen.findByText('Đỗ Hải Yến')).toBeInTheDocument();
    expect(screen.getByText('Rủi ro tín dụng, Kinh doanh bán lẻ')).toBeInTheDocument();
  });

  it('lists the shortcuts', () => {
    renderPage(<ShortcutsPage />);
    expect(screen.getByText('⌘K')).toBeInTheDocument();
    expect(screen.getByText('Đi tới Trang chủ / Cần bạn / Agents')).toBeInTheDocument();
  });

  it('shows what each channel may do per kind of request', async () => {
    renderPage(<ChannelRulesPage />);
    const row = (await screen.findByText('Duyệt · rủi ro thấp (kỳ chưa nộp)')).closest('tr')!;
    expect(within(row).getByText('Duyệt + xác thực lại (SSO, OTP)')).toBeInTheDocument();
    expect(screen.getByText('Chống làm phiền')).toBeInTheDocument();
  });
});
