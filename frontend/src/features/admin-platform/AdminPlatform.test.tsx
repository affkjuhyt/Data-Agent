/**
 * The platform admin and personal settings screens against their v1 mock: what each shows,
 * the actions that change state (rollback, add model, add integration, save notifications)
 * and where each link goes.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type { ReactElement } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
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
beforeEach(() => server.use(...createHandlers()));
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
    fireEvent.click(screen.getByRole('link', { name: 'Xem nhật ký' }));
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
    // The routing table's row; a Select's hidden native <select> holds the model's name too.
    expect(await screen.findByRole('row', { name: /LLM-M 24B.*đang đánh giá/ })).toBeInTheDocument();
  });
});

describe('ModelsPage tabs', () => {
  it('shows the AI policy on its own tab', async () => {
    renderPage(<ModelsPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'Chính sách AI' }));
    expect(
      await screen.findByText('Chặn yêu cầu lấy dữ liệu cá nhân hàng loạt'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Tải GPU 24 giờ')).toBeNull();
  });

  it('lists providers with AC06 status and cost, and calls one to test it', async () => {
    renderPage(<ModelsPage />);
    expect(
      await screen.findByRole('tab', { name: 'Nhật ký định tuyến · 412' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Provider' }));
    expect(await screen.findByText('Provider · 3')).toBeInTheDocument();
    const a = screen.getByText('Nhà cung cấp A (API)').closest('tr')!;
    expect(within(a).getByText('Có cam kết')).toBeInTheDocument();
    expect(within(a).getByText('18,4 / 30 triệu đ')).toBeInTheDocument();
    expect(within(a).getByText('61% ngân sách tháng 10')).toBeInTheDocument();
    const b = screen.getByText('Nhà cung cấp B (API)').closest('tr')!;
    expect(within(b).getByText('Chờ duyệt AC06')).toBeInTheDocument();
    expect(within(b).getByText('chưa phát sinh')).toBeInTheDocument();
    expect(await screen.findByText('Nhật ký định tuyến · tuần 41')).toBeInTheDocument();
    expect(screen.getByText('9.860')).toBeInTheDocument();
    expect(screen.getByText('So sánh dư nợ CN Hà Nội với kế hoạch')).toBeInTheDocument();

    fireEvent.click(within(a).getByRole('button', { name: 'Gọi thử Nhà cung cấp A (API)' }));
    expect(await screen.findByText('Nhà cung cấp A (API) trả lời sau 1,3 s.')).toBeInTheDocument();
    fireEvent.click(within(b).getByRole('button', { name: 'Gọi thử Nhà cung cấp B (API)' }));
    expect(await screen.findByText(/Nhà cung cấp B \(API\) không trả lời/)).toBeInTheDocument();
  });

  it('turns frontier off and back on, each after a confirm', async () => {
    renderPage(<ModelsPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'Provider' }));
    const toggle = await screen.findByRole('switch', { name: 'Cho phép frontier trong tổ chức' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Đang bật')).toBeInTheDocument();

    fireEvent.click(toggle);
    let dialog = screen.getByRole('alertdialog', { name: 'Tắt frontier trong tổ chức?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tắt frontier' }));
    expect(await screen.findByText('Đang tắt')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Cho phép frontier trong tổ chức' })).toHaveAttribute(
      'aria-checked',
      'false',
    );

    fireEvent.click(screen.getByRole('switch', { name: 'Cho phép frontier trong tổ chức' }));
    dialog = screen.getByRole('alertdialog', { name: 'Cho phép frontier trong tổ chức?' });
    expect(within(dialog).getByText(/Nhà cung cấp A \(API\)/)).toBeInTheDocument();
    expect(within(dialog).queryByText(/Nhà cung cấp B/)).toBeNull();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bật frontier' }));
    expect(await screen.findByText('Đang bật')).toBeInTheDocument();
  });

  it('adds a frontier provider, which waits for AC06', async () => {
    renderPage(<ModelsPage />);
    fireEvent.click(screen.getByRole('tab', { name: 'Provider' }));
    await screen.findByText('Provider · 3');
    fireEvent.click(screen.getByRole('button', { name: 'Thêm provider' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm provider' });
    const submit = within(dialog).getByRole('button', { name: 'Thêm provider' });
    expect(submit).toHaveAttribute('aria-disabled', 'true');
    fireEvent.change(within(dialog).getByLabelText('Tên'), { target: { value: 'Nhà cung cấp C' } });
    fireEvent.change(within(dialog).getByLabelText('Mô hình'), { target: { value: 'Mô hình C' } });
    fireEvent.change(within(dialog).getByLabelText('Địa chỉ'), {
      target: { value: 'https://api.c.example/v1' },
    });
    fireEvent.change(within(dialog).getByLabelText('Ngân sách tháng (triệu đ)'), {
      target: { value: '5' },
    });
    fireEvent.click(submit);
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(
      await screen.findByText('Đã thêm Nhà cung cấp C; chỉ được định tuyến sau khi AC06 duyệt.'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Provider · 4')).toBeInTheDocument();
    const row = screen.getByText('Nhà cung cấp C').closest('tr')!;
    expect(within(row).getByText('0 / 5 triệu đ')).toBeInTheDocument();
  });

  it('shows the whole week on the routing log tab', async () => {
    renderPage(<ModelsPage />);
    fireEvent.click(screen.getByRole('tab', { name: /Nhật ký định tuyến/ }));
    expect(await screen.findByText('Top 10 khách hàng theo dư nợ')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: 'Người hỏi' })).toBeInTheDocument();
    expect(screen.getByText('09/10 09:42')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(9);
  });
});

describe('IntegrationsPage', () => {
  it('lists clients, shows the usage of the selected one and registers a new one', async () => {
    renderPage(<IntegrationsPage />);
    expect(await screen.findByText('Copilot nội bộ')).toBeInTheDocument();
    expect(screen.getByText('MCP · Ngô Thanh Bình')).toBeInTheDocument();
    expect(screen.getByText('12.480')).toBeInTheDocument();
    expect(screen.getByText('Sắp hết hạn')).toBeInTheDocument();
    expect(
      await screen.findByText('ask_question 71% · get_metric_definition 24%'),
    ).toBeInTheDocument();
    expect(screen.getByText('5.000 lượt/ngày · hôm nay đã dùng 41%')).toBeInTheDocument();
    expect(screen.getByText('52 lượt · người dùng không có quyền dữ liệu')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Excel add-in/ }));
    expect(await screen.findByText('Excel add-in · 7 ngày')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đăng ký client' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm tích hợp' });
    fireEvent.change(within(dialog).getByLabelText('Tên'), { target: { value: 'Ticket MCP' } });
    for (const field of ['Người phụ trách', 'Data Space']) {
      fireEvent.click(await within(dialog).findByRole('button', { name: field }));
      fireEvent.click((await screen.findAllByRole('option'))[0]);
    }
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'ask_question' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cấp client' }));
    const secret = await screen.findByRole('dialog', { name: 'Đã cấp Ticket MCP' });
    expect(within(secret).getByDisplayValue('da_sk_live_9f3c1b7e2a')).toBeInTheDocument();
    fireEvent.click(within(secret).getByRole('button', { name: 'Đã chép, đóng' }));
    expect(await screen.findByText('Ticket MCP · 7 ngày')).toBeInTheDocument();
    expect(screen.getByText('Chờ người phụ trách')).toBeInTheDocument();
  });

  it('shows a rotated secret once and revokes with a reason', async () => {
    renderPage(<IntegrationsPage />);
    expect(await screen.findByText('Copilot nội bộ · 7 ngày')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xoay bí mật' }));
    const secret = await screen.findByRole('dialog', { name: 'Bí mật mới của Copilot nội bộ' });
    expect(within(secret).getByDisplayValue('da_sk_live_rotated_51d0')).toBeInTheDocument();
    fireEvent.click(within(secret).getByRole('button', { name: 'Đã chép, đóng' }));

    fireEvent.click(screen.getByRole('button', { name: 'Thu hồi' }));
    const revoke = screen.getByRole('dialog', { name: 'Thu hồi Copilot nội bộ?' });
    fireEvent.change(within(revoke).getByLabelText('Lý do'), { target: { value: 'Lộ bí mật' } });
    fireEvent.click(within(revoke).getByRole('button', { name: 'Thu hồi' }));
    expect(await screen.findByText('Đã thu hồi Copilot nội bộ.')).toBeInTheDocument();
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
    expect(navigate).toHaveBeenCalledWith('account', 'CHANNEL_RULES');
  });
});

describe('MyClients on Hồ sơ', () => {
  it('agrees to a client granted in my name, with If-Match as listed', async () => {
    let ifMatch: string | null = null;
    server.use(
      http.post(/\/v1\/me\/clients\/[^/:]+:confirm$/, ({ request }) => {
        ifMatch = request.headers.get('If-Match');
        return undefined;
      }),
    );
    renderPage(<ProfilePage />);
    const row = (await screen.findByText('Power BI phòng Rủi ro')).closest('li')!;
    expect(within(row).getByText('Chờ bạn đồng ý')).toBeInTheDocument();
    fireEvent.click(within(row).getByRole('button', { name: 'Đồng ý' }));
    expect(
      await screen.findByText('Power BI phòng Rủi ro gọi được Data Agent bằng quyền của bạn.'),
    ).toBeInTheDocument();
    expect(ifMatch).toBe('"2026-10-12T03:15:00.123456Z"');
    await waitFor(() => expect(within(row).getByText('Hoạt động')).toBeInTheDocument());
    expect(within(row).queryByRole('button', { name: 'Đồng ý' })).not.toBeInTheDocument();
  });

  it('asks to look again when an admin changed the client since it was listed', async () => {
    server.use(
      http.post(/\/v1\/me\/clients\/[^/:]+:confirm$/, () =>
        HttpResponse.json(
          { code: 'precondition_failed', detail: 'If-Match must be the ETag.' },
          { status: 412 },
        ),
      ),
    );
    renderPage(<ProfilePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Đồng ý' }));
    expect(
      await screen.findByText('Power BI phòng Rủi ro vừa được quản trị viên sửa. Xem lại rồi đồng ý.'),
    ).toBeInTheDocument();
  });
});

describe('settings and channel rules', () => {
  it('shows the profile from the directory', async () => {
    renderPage(<ProfilePage />);
    expect(await screen.findByText('Đỗ Hải Yến')).toBeInTheDocument();
    expect(screen.getByText('Khối Quản lý rủi ro · Phòng Rủi ro tín dụng')).toBeInTheDocument();
    expect(screen.getByText('Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)')).toBeInTheDocument();
    expect(screen.getByText('1.234,56 · ngày dd/mm/yyyy · giờ 24h')).toBeInTheDocument();
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
