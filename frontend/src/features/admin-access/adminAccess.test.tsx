/**
 * Quản trị · người, quyền và tuân thủ against the v1 mock: users and roles, a Data Space's
 * matrix and members, deciding access requests, policies, the audit log and DSRs.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
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
      body: expect.objectContaining({
        email: 'minh.pd@congty.vn',
        role_id: 'role-analyst',
        row_scope: null,
      }),
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
    const profiled = (value: string, count: number) => ({ value, count });
    server.use(
      http.get('*/v1/datasets', () =>
        HttpResponse.json({
          items: [{ id: 'ds-du-no', display_name: 'Dư nợ theo chi nhánh', table_name: 'du_no' }],
          next_page_token: '',
        }),
      ),
      http.get('*/v1/datasets/ds-du-no', () =>
        HttpResponse.json({
          id: 'ds-du-no',
          columns: [
            {
              name: 'region',
              display_name: 'Vùng',
              pii_label: null,
              profile: { top_values: [profiled('Bắc', 412), profiled('Trung', 203)] },
            },
            {
              name: 'so_cccd',
              display_name: null,
              pii_label: 'national_id',
              profile: { top_values: [profiled('***', 1)] },
            },
          ],
        }),
      ),
    );
    wrap(<DataSpacesPage />);
    expect(
      await screen.findByRole('heading', { name: 'Rủi ro tín dụng · thành viên và quyền' }),
    ).toBeInTheDocument();
    const analyst = await screen.findByRole('row', { name: /Phân tích viên/ });
    expect(within(analyst).getAllByText('cần duyệt')).toHaveLength(3);
    expect(screen.getByText('không bao giờ')).toBeInTheDocument();
    expect(screen.getByText('Thành viên · 66')).toBeInTheDocument();
    expect(await screen.findByText('Người xem · Hết hạn 30/10')).toBeInTheDocument();
    expect(
      await screen.findByText(/1 yêu cầu quyền đang chờ: Agent Báo cáo · xuất PDF chi tiết/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thêm thành viên' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm thành viên Data Space' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Người hoặc nhóm' }));
    fireEvent.click(await screen.findByRole('option', { name: 'GRP_RISK_ANALYST · 24 người' }));
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Vai trò' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Phân tích viên' }));
    // The scope comes from the space's own table: a column and the values its profile found.
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Phạm vi dòng' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Chỉ các dòng có giá trị đã chọn' }));
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Lấy giá trị từ bảng' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Dư nợ theo chi nhánh' }));
    fireEvent.click(await within(dialog).findByRole('combobox', { name: 'Cột' }));
    // The personal-data column is not offered.
    expect(screen.queryByRole('option', { name: /so_cccd/ })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('option', { name: 'Vùng (region)' }));
    fireEvent.click(await within(dialog).findByRole('checkbox', { name: 'Bắc' }));
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'Trung' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm' }));

    expect(
      await screen.findByText('Đã thêm GRP_RISK_ANALYST vào Rủi ro tín dụng'),
    ).toBeInTheDocument();
    const put = sent.find((s) => s.path === '/data-spaces/ds-rui-ro-tin-dung/members');
    const members = (put?.body as { members: { row_scope: unknown }[] }).members;
    expect(members).toHaveLength(6);
    // The members already there keep their own scope; the new one carries the chosen values.
    expect(members.map((m) => m.row_scope)).toEqual([
      null,
      null,
      { region: ['Bắc'] },
      null,
      null,
      { region: ['Bắc', 'Trung'] },
    ]);
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
    // DataSpaceCreate, then the chosen sources in their own PUT (A2).
    expect(sent).toContainEqual({
      path: '/data-spaces',
      body: expect.objectContaining({
        code: 'THE_TIN_DUNG',
        name: 'Thẻ tín dụng',
        steward_id: 'u-duc-long',
        default_policy: expect.objectContaining({ mask_pii: true, allow_external_channels: false }),
      }),
    });
    expect(sent).toContainEqual({
      path: '/data-spaces/ds-6/sources',
      body: { source_ids: ['src-core'] },
    });
  });
});

describe('PermissionComparePage', () => {
  it('reads one table of the Data Space under a role and a person, column by column', async () => {
    routeId = 'ds-rui-ro-tin-dung';
    wrap(<PermissionComparePage />);
    expect(
      await screen.findByText('Chọn một bảng và tối đa hai vai trò hoặc người'),
    ).toBeInTheDocument();
    const pick = async (label: string, option?: RegExp) => {
      fireEvent.click(screen.getByRole('combobox', { name: label }));
      const options = await screen.findAllByRole('option');
      fireEvent.click(option ? options.find((o) => option.test(o.textContent ?? ''))! : options[0]);
    };
    await pick('Bảng');
    await pick('Bên trái', /^Vai trò · /);
    await pick('Bên phải', /^Người · /);

    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(2));
    const [role, person] = screen.getAllByRole('article');
    expect(
      within(role).getByText("Chỉ các dòng thoả region = 'Bắc' · 1 chính sách áp dụng"),
    ).toBeInTheDocument();
    expect(within(role).getByRole('row', { name: 'national_id Ẩn' })).toBeInTheDocument();
    expect(
      within(role).getByRole('row', { name: 'customer_name Che · che một phần' }),
    ).toBeInTheDocument();
    expect(within(person).getByText(/^Mọi dòng/)).toBeInTheDocument();
    expect(
      within(person).getByRole('row', { name: 'national_id Che · chỉ 4 số cuối' }),
    ).toBeInTheDocument();
    const body = sent.filter((x) => x.path === '/permissions:simulate').at(-1)?.body as Record<
      string,
      unknown
    >;
    expect(body.role_ids).toHaveLength(1);
    expect(body.user_ids).toHaveLength(1);
    expect(body).toHaveProperty('dataset_id');
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
    fireEvent.change(within(dialog).getByRole('textbox'), {
      target: { value: 'Ngoài phạm vi công việc' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('admin', 'ACCESS_REQUESTS', 'ar-0092'),
    );
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
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Cấp hẹp hơn' }),
    );
    await waitFor(() =>
      expect(sent).toContainEqual({
        path: '/access-requests/ar-0091:decide',
        body: expect.objectContaining({
          decision: 'narrow',
          granted_scope: expect.stringContaining('Số tổng hợp'),
        }),
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
    expect(
      within(dialog).getByText(/23 yêu cầu tuần này được tự xử lý nhờ quy tắc/),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tắt chính sách' }));
    expect(await within(dialog).findByText('Ghi lý do tắt chính sách.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Lý do tắt' }), {
      target: { value: 'Chi nhánh đã gộp' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tắt chính sách' }));
    await waitFor(() => expect(within(row).getByText('Đã tắt')).toBeInTheDocument());
  });

  it('shows the rule, simulates one person and the 7-day backtest', async () => {
    routeId = 'pol-007';
    wrap(<PoliciesPage />);
    const rule = (await screen.findByRole('textbox', { name: 'Quy tắc' })) as HTMLTextAreaElement;
    expect(rule.value).toContain('Chỉ cho thấy dòng: branch_code IN user.ad.branches');
    expect(
      screen.getByText(/Chính sách theo dòng · Data Space Kinh doanh bán lẻ · đang bật/),
    ).toBeInTheDocument();
    expect(await screen.findByText('1.102.311')).toBeInTheDocument();
    expect(screen.getByText('18.420')).toBeInTheDocument();
    expect(screen.getByText('hỏi “dư nợ bán lẻ theo chi nhánh tháng 9”')).toBeInTheDocument();
    expect(screen.getByText('2.140 / 18.302')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Lưu thay đổi' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

describe('AuditPage', () => {
  it('lists the log with blocked actions in warning, and filters by outcome', async () => {
    wrap(<AuditPage />);
    expect(await screen.findByText('Bị chặn: xem cột id_number')).toHaveClass('text-warning');
    expect(screen.getAllByRole('row')).toHaveLength(7);
    fireEvent.click(screen.getByRole('combobox', { name: 'Kết quả' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Kết quả: bị chặn' }));
    await waitFor(() => expect(screen.getAllByRole('row')).toHaveLength(2));
    expect(screen.getByText('Bị chặn: xem cột id_number')).toBeInTheDocument();
  });

  it("exports the day's rows as the api's filters and follows the job until it is signed", async () => {
    wrap(<AuditPage />);
    await screen.findByText('Bị chặn: xem cột id_number');
    fireEvent.click(screen.getByRole('button', { name: 'Xuất cho kiểm toán' }));
    expect(await screen.findByText(/Tệp CSV 6 dòng đã ký/)).toBeInTheDocument();
    const body = sent.find((s) => s.path === '/audit:export')?.body as {
      filters: Record<string, string>;
      format: string;
    };
    expect(body.format).toBe('csv');
    expect(Object.keys(body.filters).sort()).toEqual(['from', 'to']);
  });

  it('says an export over 100.000 rows waits for a second person', async () => {
    wrap(<AuditPage />);
    await screen.findByText('Bị chặn: xem cột id_number');
    fireEvent.change(screen.getByLabelText('Ngày'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Xuất cho kiểm toán' }));
    expect(
      await screen.findByText(/Bản xuất 1\.240\.000 dòng vượt 100\.000 dòng trong ngày/),
    ).toBeInTheDocument();
  });

  it('verifies the hash chain and lists the security events', async () => {
    wrap(<AuditPage />);
    await screen.findByText('Bị chặn: xem cột id_number');
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm chuỗi băm' }));
    expect(await screen.findByText(/Chuỗi băm nguyên vẹn/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Sự kiện bảo mật' }));
    expect(await screen.findByText('Chèn lệnh vào tài liệu')).toBeInTheDocument();
    expect(screen.getByText('Cao')).toBeInTheDocument();
    expect(screen.getByText('audit_log · denied: 14 · window_minutes: 10')).toBeInTheDocument();
  });
});

describe('PrivacyPage', () => {
  const DSR = '0192f1a2-7c3d-7e4f-8a9b-000000000014';
  const item = (n: number) => `0192f1a2-7c3d-7e4f-8a9b-00000000010${n}`;

  it('decides every found item: deletes the ticked ones, keeps the rest with a legal basis', async () => {
    routeId = DSR;
    wrap(<PrivacyPage />);
    expect(await screen.findByText('Yêu cầu xoá dữ liệu')).toBeInTheDocument();
    expect(screen.getByText(/người xử lý Nguyễn Khánh Linh/)).toBeInTheDocument();
    expect(screen.getByText('Không · nghĩa vụ lưu trữ báo cáo')).toHaveClass('text-warning');
    // E1: what lives in a source system is reported there, never deleted here.
    expect(screen.getByText('Không · báo cho hệ thống nguồn')).toBeInTheDocument();
    expect(
      screen.getByRole('checkbox', { name: 'Hệ thống nguồn · core_banking.customers:CIF 0041872' }),
    ).toBeDisabled();
    const memory = 'Bộ nhớ agent · agent.memory:Agent Phân tích';
    // The deletable items are selected in an effect after the request renders.
    await waitFor(() => expect(screen.getByRole('checkbox', { name: memory })).toBeChecked());
    fireEvent.click(screen.getByRole('checkbox', { name: memory }));

    fireEvent.click(screen.getByRole('button', { name: 'Xoá đã chọn' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Duyệt kế hoạch xoá DSR-000014?' });
    expect(within(dialog).getByText(/Xoá dữ liệu ở 2 nơi/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Duyệt và thực hiện' }));
    expect(
      await within(dialog).findByText('Ghi căn cứ pháp lý cho mọi nơi được giữ lại.'),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText(`Căn cứ pháp lý giữ ${memory}`), {
      target: { value: 'đang có tranh chấp' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Duyệt và thực hiện' }));
    await waitFor(() => expect(screen.getByText('Đã xong')).toBeInTheDocument());
    expect(sent).toContainEqual({
      path: `/dsr/${DSR}:execute`,
      body: {
        items: [
          { id: item(1), action: 'delete' },
          { id: item(2), action: 'delete' },
          { id: item(3), action: 'retain', legal_basis: 'đang có tranh chấp' },
          { id: item(4), action: 'retain', legal_basis: 'nghĩa vụ lưu trữ báo cáo' },
          { id: item(5), action: 'provide' },
        ],
      },
    });
  });

  it('creates a request with the day it was received', async () => {
    wrap(<PrivacyPage />);
    await screen.findByText('DSR-000014');
    fireEvent.click(screen.getByRole('button', { name: 'Tạo yêu cầu' }));
    const dialog = screen.getByRole('dialog', { name: 'Tạo yêu cầu của chủ thể dữ liệu' });
    fireEvent.change(within(dialog).getByPlaceholderText(/Số CCCD/), {
      target: { value: '001088012345' },
    });
    fireEvent.change(within(dialog).getByLabelText('Ngày nhận'), {
      target: { value: '2026-10-20' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo yêu cầu' }));
    const created = '0192f1a2-7c3d-7e4f-8a9b-000000000018';
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('admin', 'PRIVACY', created));
    const body = sent.find((x) => x.path === '/dsr')?.body as Record<string, string>;
    expect(body.request_kind).toBe('access');
    expect(new Date(body.received_at).getDate()).toBe(20);
    // 30 days from the day it was received, the dialog's default.
    expect((Date.parse(body.due_at) - Date.parse(body.received_at)) / 86_400_000).toBe(30);
    expect(body).not.toHaveProperty('channel');
  });
});
