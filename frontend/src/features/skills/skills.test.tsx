/**
 * P03 Kỹ năng, library side, against the v1 mock: the library and its filters, sharing a
 * certified skill, reviewing a pending version through its skill_publish proposal, the agent's
 * suggestions, and the tool catalogue.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import SkillLibraryPage from './SkillLibraryPage';
import SkillSuggestionsPage from './SkillSuggestionsPage';
import SkillsToolsPage from './SkillsToolsPage';
import SkillVersionsPage from './SkillVersionsPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('SkillLibraryPage', () => {
  it('lists the skills, filters by origin and opens one to run', async () => {
    wrap(<SkillLibraryPage />);
    expect(await screen.findByRole('button', { name: 'Phân rã biến động' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Tất cả 11' })).toBeChecked();
    expect(screen.getByText('412 lượt')).toBeInTheDocument();
    expect(screen.getByText('Ngừng · thay bằng v3')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Toàn tổ chức 3' }));
    expect(screen.queryByRole('button', { name: 'Dự báo thử 13 tuần' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'So sánh kỳ' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'So sánh kỳ' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_RUN', 'sk-period-compare');
  });

  it('creates an empty draft and opens it in the editor', async () => {
    wrap(<SkillLibraryPage />);
    await screen.findByRole('button', { name: 'Phân rã biến động' });
    fireEvent.click(screen.getByRole('button', { name: 'Kỹ năng mới' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_EDITOR', 'sk-new-1'),
    );
  });

  it('shares a certified skill; only certified skills offer it', async () => {
    wrap(<SkillLibraryPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Thao tác với Dự báo thử 13 tuần' }));
    expect(screen.queryByRole('menuitem', { name: 'Chia sẻ' })).not.toBeInTheDocument();
    // An open menu is modal: it closes before another one opens.
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Phân rã biến động' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Chia sẻ' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Chia sẻ cho' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Toàn tổ chức' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Chia sẻ' }));
    expect(
      await screen.findByText('Đã gửi quản trị viên duyệt chia sẻ Phân rã biến động'),
    ).toBeInTheDocument();
  });
});

describe('SkillVersionsPage', () => {
  it('opens the first skill waiting for review and publishes it after confirming', async () => {
    wrap(<SkillVersionsPage />);
    expect(
      await screen.findByText('3 phiên bản · v3 chờ duyệt · v2 đang dùng'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Kiểm định 12/12 đạt')).toBeInTheDocument();
    expect(screen.getByText('Data Space')).toBeInTheDocument();
    expect(screen.getByText(/v2 ngừng dùng sau 30 ngày/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Duyệt và phát hành v3' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(
      within(dialog).getByText(
        'Agent đang dùng v2 không tự nâng cấp; chủ sở hữu từng agent sẽ được thông báo.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xuất bản' }));
    expect(await screen.findByText('Đã phát hành Phân nhóm khách hàng v3')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole('button', { name: 'Duyệt và phát hành v3' }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByText('Ngừng 07/11 · thay bằng v3')).toBeInTheDocument();
  });

  it('cannot approve once the publish proposal is no longer open', async () => {
    routeId = 'sk-segment';
    server.use(http.get('*/v1/inbox', () => HttpResponse.json({ items: [], next_page_token: '' })));
    wrap(<SkillVersionsPage />);
    expect(await screen.findByRole('button', { name: 'Duyệt và phát hành v3' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('asks for a note before sending changes back to the author', async () => {
    routeId = 'sk-segment';
    wrap(<SkillVersionsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Yêu cầu sửa' }));
    const dialog = await screen.findByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gửi yêu cầu' }));
    expect(await within(dialog).findByText('Ghi điều cần sửa.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Điều cần sửa'), {
      target: { value: 'Giữ lại biến thu nhập cho nhóm tự doanh' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Gửi yêu cầu' }));
    expect(await screen.findByText('Đã trả v3 về Đỗ Hải Yến')).toBeInTheDocument();
  });

  it('says so when nothing waits for review', async () => {
    // Approve the only pending version first, then the rail entry has nothing to show.
    const { unmount } = wrap(<SkillVersionsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Duyệt và phát hành v3' }));
    fireEvent.click(
      within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Xuất bản' }),
    );
    await screen.findByText('Đã phát hành Phân nhóm khách hàng v3');
    unmount();
    wrap(<SkillVersionsPage />);
    expect(await screen.findByText('Không có phiên bản nào chờ duyệt')).toBeInTheDocument();
  });
});

describe('SkillSuggestionsPage', () => {
  it('shows the repeated chains, their occurrences, and creates a skill from the first session', async () => {
    wrap(<SkillSuggestionsPage />);
    expect(await screen.findByText('Đối soát số với báo cáo đã nộp')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Lấy số đã nộp → lấy số nội bộ cùng kỳ → so từng chỉ tiêu → khoanh vùng chênh lệch',
      ),
    ).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Xem các lần lặp' })[0]);
    expect(
      screen.getByRole('list', { name: 'Các lần lặp của Đối soát số với báo cáo đã nộp' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: 'Tạo kỹ năng' })[0]);
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_FROM_SESSION', 'ws-filed-recon-q3');
  });

  it('dismisses a suggestion for 90 days', async () => {
    wrap(<SkillSuggestionsPage />);
    await screen.findByText('Kiểm tra khoản cơ cấu trong kỳ');
    fireEvent.click(screen.getAllByRole('button', { name: 'Bỏ qua' })[1]);
    expect(
      await screen.findByText(
        'Đã bỏ qua. Agent không đề xuất lại “Kiểm tra khoản cơ cấu trong kỳ” trong 90 ngày.',
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(
        screen.queryByRole('heading', { name: 'Kiểm tra khoản cơ cấu trong kỳ' }),
      ).not.toBeInTheDocument(),
    );
  });
});

describe('SkillsToolsPage', () => {
  it('opens on the tool catalogue, and shows skills by card and tools by level', async () => {
    wrap(<SkillsToolsPage />);
    expect(await screen.findByText('query.run')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: /Công cụ/ })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('Truy vấn qua semantic layer')).toBeInTheDocument();
    const send = screen.getByText('notify.send').closest('tr')!;
    expect(within(send).getByText('Gửi ra ngoài')).toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: /Kết nối MCP/ })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Đăng ký công cụ' })).toBeNull();

    fireEvent.click(screen.getByRole('tab', { name: /Kỹ năng/ }));
    expect(await screen.findByText('Công cụ theo mức quyền')).toBeInTheDocument();
    expect(screen.getByText('Chỉ quản trị viên; agent không được gán')).toBeInTheDocument();
    expect(await screen.findByText('Agent đề xuất kỹ năng mới')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Đối soát hai nguồn/ }));
    expect(navigate).toHaveBeenCalledWith('agents', 'SKILL_RUN', 'sk-reconcile');
  });
});
