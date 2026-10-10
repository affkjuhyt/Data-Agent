/**
 * Quản trị · người, quyền và tuân thủ against the v1 mock: users and roles, a Data Space's
 * matrix and members, deciding access requests, policies, the audit log and DSRs.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AccessRequestsPage from './AccessRequestsPage';
import AuditPage from './AuditPage';
import CreateDataSpacePage from './CreateDataSpacePage';
import DataSpacesPage from './DataSpacesPage';
import PermissionComparePage from './PermissionComparePage';
import PoliciesPage from './PoliciesPage';
import PrivacyPage from './PrivacyPage';
import UsersPage from './UsersPage';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, routeId }),
}));

const sent: { path: string; body: unknown }[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = undefined;
  sent.length = 0;
  server.resetHandlers(
    // Record every write, then fall through to the stateful mock.
    http.all('*/v1/*', async ({ request }) => {
      if (request.method !== 'GET')
        sent.push({
          path: new URL(request.url).pathname.replace(/^\/v1/, ''),
          body: request.headers.get('Content-Type') ? await request.clone().json() : undefined,
        });
      return undefined;
    }),
    ...createHandlers(),
  );
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('UsersPage', () => {
  it('lists people and agents with role, Data Space, scope and expiry, and what each role may do', async () => {
    wrap(<UsersPage />);
    expect(await screen.findByText('Đỗ Hải Yến')).toBeInTheDocument();
    expect(screen.getByText(/^412 người từ Active Directory · đồng bộ 06:00/)).toBeInTheDocument();
    expect(screen.getByText('3 chi nhánh Hà Nội')).toHaveClass('text-warning');
    expect(screen.getByText('31/12')).toBeInTheDocument();
    expect(screen.getByText('Agent Đối soát')).toBeInTheDocument();
    expect(await screen.findByText('Vai trò làm được gì')).toBeInTheDocument();
    const admin = screen.getByRole('row', { name: /Quản trị hệ thống/ });
    expect(within(admin).getAllByLabelText('Có')).toHaveLength(1);
  });

  it('invites someone into a Data Space', async () => {
    wrap(<UsersPage />);
    await screen.findByText('Đỗ Hải Yến');
    fireEvent.click(screen.getByRole('button', { name: 'Thêm vào Data Space' }));
    const dialog = screen.getByRole('dialog', { name: 'Mời người dùng' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gửi lời mời' }));
    expect(within(dialog).getByText('Nhập email, chọn vai trò và Data Space.')).toBeInTheDocument();

    fireEvent.change(within(dialog).getByPlaceholderText('ten@congty.vn'), {
      target: { value: 'minh.pd@congty.vn' },
    });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Vai trò' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Chuyên viên phân tích' }));
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Data Space' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Rủi ro tín dụng' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gửi lời mời' }));

    expect(await screen.findByText('Đã gửi lời mời tới minh.pd@congty.vn')).toBeInTheDocument();
    expect(sent).toContainEqual({
      path: '/admin/invitations',
      body: expect.objectContaining({ email: 'minh.pd@congty.vn', role_id: 'role-analyst', row_scope: null }),
    });
  });
});

describe('DataSpacesPage', () => {
  it('lists Data Spaces and opens one', async () => {
    wrap(<DataSpacesPage />);
    fireEvent.click(await screen.findByText('Kinh doanh bán lẻ'));
    expect(navigate).toHaveBeenCalledWith('admin', 'DATA_SPACES', 'ds-ban-le');
  });

  it('shows the permission matrix, members and pending requests, and adds a member', async () => {
    routeId = 'ds-rui-ro-tin-dung';
    wrap(<DataSpacesPage />);
    expect(
      await screen.findByRole('heading', { name: 'Rủi ro tín dụng · thành viên và quyền' }),
    ).toBeInTheDocument();
    const analyst = await screen.findByRole('row', { name: /Phân tích viên/ });
    expect(within(analyst).getAllByText('cần duyệt')).toHaveLength(3);
    expect(screen.getByText('không bao giờ')).toBeInTheDocument();
    expect(screen.getByText('Thành viên · 66')).toBeInTheDocument();
    expect(await screen.findByText('Người xem · Hết hạn 30/10')).toBeInTheDocument();
    expect(await screen.findByText(/1 yêu cầu quyền đang chờ: Agent Báo cáo · xuất PDF chi tiết/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thêm thành viên' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm thành viên Data Space' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Người hoặc nhóm' }));
    fireEvent.click(await screen.findByRole('option', { name: 'GRP_RISK_ANALYST · 24 người' }));
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Vai trò' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Phân tích viên' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm' }));

    expect(await screen.findByText('Đã thêm GRP_RISK_ANALYST vào Rủi ro tín dụng')).toBeInTheDocument();
    const put = sent.find((s) => s.path === '/data-spaces/ds-rui-ro-tin-dung/members');
    expect((put?.body as { items: unknown[] }).items).toHaveLength(6);
  });

  it('refuses an id that is not a plain id before calling the API', async () => {
    routeId = '..%2Fadmin';
    wrap(<DataSpacesPage />);
    expect(await screen.findAllByText(/Đường dẫn chứa mã không hợp lệ/)).not.toHaveLength(0);
  });
});

describe('CreateDataSpacePage', () => {
  it('needs a name and a steward, then creates and opens the Data Space', async () => {
    wrap(<CreateDataSpacePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo Data Space' }));
    expect(screen.getByText('Nhập tên và chọn người phụ trách dữ liệu.')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('Ví dụ: Rủi ro tín dụng'), {
      target: { value: 'Thẻ tín dụng' },
    });
    fireEvent.click(screen.getByRole('combobox', { name: 'Người phụ trách dữ liệu' }));
    fireEvent.change(await screen.findByRole('textbox', { name: /Tìm trong Người phụ trách/ }), {
      target: { value: 'Phạm Đức' },
    });
    fireEvent.click(await screen.findByRole('option', { name: 'Phạm Đức Long' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Core banking' }));
    fireEvent.click(await screen.findByRole('checkbox', { name: /GRP_RISK_HEAD/ }));
    expect(screen.getByText(/Đồng bộ từ nhóm AD: GRP_RISK_HEAD \(6 người\)/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tạo Data Space' }));

    await waitFor(() => expect(navigate).toHaveBeenCalledWith('admin', 'DATA_SPACES', 'ds-6'));
    expect(sent).toContainEqual({
      path: '/data-spaces',
      body: expect.objectContaining({
        name: 'Thẻ tín dụng',
        steward_id: 'u-duc-long',
        source_ids: ['src-core'],
        group_ids: ['grp-risk-head'],
        status: 'onboarding',
        default_policy: expect.objectContaining({ mask_pii: true, allow_external_channels: false }),
      }),
    });
  });
});

describe('PermissionComparePage', () => {
  it('shows one table under two roles and asks for access from a refused action', async () => {
    routeId = 'ds-rui-ro-tin-dung';
    wrap(<PermissionComparePage />);
    expect(await screen.findByText('Cùng bảng ld_loans · hai vai trò')).toBeInTheDocument();
    expect(screen.getByText('Chỉ thấy vùng Bắc · PII bị che')).toBeInTheDocument();
    expect(screen.getAllByText('Tổng hợp')).toHaveLength(3);
    const viewer = screen.getByRole('article', { name: /Người xem · Lê Minh Tâm/ });
    fireEvent.click(within(viewer).getByRole('button', { name: 'Chạy agent' }));
    await waitFor(() =>
      expect(sent).toContainEqual({
        path: '/access-requests',
        body: expect.objectContaining({ capability: 'run_agent', data_space_id: 'ds-rui-ro-tin-dung' }),
      }),
    );
  });
});

describe('AccessRequestsPage', () => {
  it('opens the first request of the queue with its impact and the agent suggestion', async () => {
    wrap(<AccessRequestsPage />);
    expect(
      await screen.findByRole('heading', { name: /Lê Minh Châu xin xem nợ xấu toàn vùng Bắc Bộ/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('30 ngày · tới', { exact: false })).toBeInTheDocument();
    expect(screen.getByText(/4 người cùng nhóm đã được cấp quyền tương tự/)).toBeInTheDocument();
    expect(screen.getByText('3 + số tổng hợp 2 vùng')).toBeInTheDocument();
    expect(screen.getByText('đang xem')).toBeInTheDocument();
    expect(screen.getByText('hạn hôm nay')).toBeInTheDocument();
    expect(screen.getByText('2 ngày')).toBeInTheDocument();
  });

  it('needs a reason to reject, then moves to the next request', async () => {
    wrap(<AccessRequestsPage />);
    await screen.findByRole('heading', { name: /Lê Minh Châu/ });
    fireEvent.click(screen.getByRole('button', { name: 'Từ chối' }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(within(dialog).getByText('Ghi lý do để người xin biết vì sao.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox'), { target: { value: 'Ngoài phạm vi công việc' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('admin', 'ACCESS_REQUESTS', 'ar-0092'));
    expect(sent).toContainEqual({
      path: '/access-requests/ar-0091:decide',
      body: { decision: 'reject', note: 'Ngoài phạm vi công việc' },
    });
  });

  it('narrows to the suggested scope', async () => {
    routeId = 'ar-0091';
    wrap(<AccessRequestsPage />);
    await screen.findByRole('heading', { name: /Lê Minh Châu/ });
    fireEvent.click(screen.getByRole('button', { name: 'Cấp hẹp hơn' }));
    fireEvent.click(within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cấp hẹp hơn' }));
    await waitFor(() =>
      expect(sent).toContainEqual({
        path: '/access-requests/ar-0091:decide',
        body: expect.objectContaining({ decision: 'narrow', granted_scope: expect.stringContaining('Số tổng hợp') }),
      }),
    );
  });
});

describe('PoliciesPage', () => {
  it('lists policies and turns one off after confirming its consequence', async () => {
    wrap(<PoliciesPage />);
    const row = await screen.findByRole('row', { name: /Chi nhánh chỉ thấy khách hàng của mình/ });
    fireEvent.click(within(row).getByRole('button', { name: 'Thao tác với POL-007' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Tắt chính sách' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Tắt chính sách này?' });
    expect(within(dialog).getByText(/23 yêu cầu tuần này được tự xử lý nhờ quy tắc/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tắt chính sách' }));
    await waitFor(() => expect(within(row).getByText('Đã tắt')).toBeInTheDocument());
  });

  it('shows the rule, simulates one person and the 7-day backtest', async () => {
    routeId = 'pol-007';
    wrap(<PoliciesPage />);
    const rule = (await screen.findByRole('textbox', { name: 'Quy tắc' })) as HTMLTextAreaElement;
    expect(rule.value).toContain('Chỉ cho thấy dòng: branch_code IN user.ad.branches');
    expect(screen.getByText(/Chính sách theo dòng · Data Space Kinh doanh bán lẻ · đang bật/)).toBeInTheDocument();
    expect(await screen.findByText('1.102.311')).toBeInTheDocument();
    expect(screen.getByText('18.420')).toBeInTheDocument();
    expect(screen.getByText('hỏi “dư nợ bán lẻ theo chi nhánh tháng 9”')).toBeInTheDocument();
    expect(screen.getByText('2.140 / 18.302')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lưu thay đổi' })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('AuditPage', () => {
  it('lists the log with blocked actions in warning, and filters by channel', async () => {
    wrap(<AuditPage />);
    expect(await screen.findByText('Bị chặn: xem cột id_number')).toHaveClass('text-warning');
    expect(screen.getAllByRole('row')).toHaveLength(7);
    fireEvent.click(screen.getByRole('combobox', { name: 'Kênh' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Kênh: MCP' }));
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(2));
    expect(screen.getByText('Gọi công cụ get_metric')).toBeInTheDocument();
  });
});

describe('PrivacyPage', () => {
  it('deletes what may be deleted and keeps what the law requires', async () => {
    routeId = 'dsr-014';
    wrap(<PrivacyPage />);
    expect(await screen.findByText('Khách hàng yêu cầu xoá dữ liệu cá nhân')).toBeInTheDocument();
    expect(screen.getByText('Không · nghĩa vụ lưu trữ báo cáo')).toHaveClass('text-warning');
    expect(screen.getByRole('checkbox', { name: 'Nhật ký kiểm toán' })).toBeDisabled();
    expect(screen.getByRole('checkbox', { name: 'Câu hỏi và trả lời trong phiên' })).toBeChecked();

    fireEvent.click(screen.getByRole('button', { name: 'Xoá đã chọn' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Duyệt kế hoạch xoá DSR-014?' });
    expect(within(dialog).getByText(/Xoá 18 mục ở 3 nơi/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Duyệt và thực hiện' }));
    await waitFor(() => expect(screen.getByText('Đã xong')).toBeInTheDocument());
    expect(sent).toContainEqual({ path: '/dsr/dsr-014:execute', body: { item_ids: ['it-1', 'it-2', 'it-3'] } });
  });

  it('creates a request from the list', async () => {
    wrap(<PrivacyPage />);
    await screen.findByText('DSR-014');
    fireEvent.click(screen.getByRole('button', { name: 'Tạo yêu cầu' }));
    const dialog = screen.getByRole('dialog', { name: 'Tạo yêu cầu của chủ thể dữ liệu' });
    fireEvent.change(within(dialog).getByPlaceholderText(/Số CCCD/), { target: { value: '001088012345' } });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo yêu cầu' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('admin', 'PRIVACY', 'dsr-016'));
  });
});
