/**
 * Phiên làm việc end to end against the v1 mock: the list, the notebook (stale band, agent
 * proposal, comments), sharing, and the report review with its pre-publish checks.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ReportReviewPage from './ReportReviewPage';
import SessionsPage from './SessionsPage';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
  routeId = undefined;
});
afterAll(() => server.close());

const renderWithToast = (node: React.ReactNode) => render(<ToastProvider>{node}</ToastProvider>);

describe('SessionsPage', () => {
  it('lists the sessions and opens one by its id', async () => {
    renderWithToast(<SessionsPage />);
    fireEvent.click(await screen.findByRole('button', { name: /Điều tra nợ xấu bán lẻ T9/ }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'SESSIONS', 'ws-npl-sept');
  });

  it('says there is no session yet when the list is empty', async () => {
    server.use(
      http.get('*/v1/workspaces', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    renderWithToast(<SessionsPage />);
    expect(await screen.findByRole('heading', { name: 'Chưa có phiên nào' })).toBeInTheDocument();
  });

  it('shows the notebook, re-runs the stale tiles and accepts the agent proposal', async () => {
    routeId = 'ws-npl-sept';
    renderWithToast(<SessionsPage />);
    expect(await screen.findAllByRole('article')).toHaveLength(5);
    expect(screen.getByText(/tham số phiên: kỳ = T9\/2026, phạm vi = Bán lẻ/)).toBeInTheDocument();
    expect(screen.getByText(/Ô 2 và 4 dùng số cũ và cần chạy lại/)).toBeInTheDocument();
    expect(screen.getAllByText('Cần chạy lại')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Chạy lại 2 ô' }));
    await waitFor(() => expect(screen.queryByText('Cần chạy lại')).toBeNull());

    const proposal = screen.getByRole('article', { name: /Ô 5 · Kiểm tra khoản cơ cấu/ });
    expect(within(proposal).getByText('Đề xuất của agent')).toBeInTheDocument();
    fireEvent.click(within(proposal).getByRole('button', { name: 'Thêm ô' }));
    await waitFor(() =>
      expect(within(proposal).queryByRole('button', { name: 'Thêm ô' })).toBeNull(),
    );
  });

  it('shows the comments and the task of a tile, and adds a comment', async () => {
    routeId = 'ws-npl-sept';
    renderWithToast(<SessionsPage />);
    const panel = await screen.findByRole('complementary', { name: 'Bình luận' });
    expect(
      within(panel).getByRole('heading', { name: 'Ô 2 · Nợ xấu theo vùng' }),
    ).toBeInTheDocument();
    expect(await within(panel).findByText(/Bản v4 chưa tính cơ cấu lần 2/)).toBeInTheDocument();
    expect(
      within(panel).getByText('Xác nhận định nghĩa nợ xấu cho khoản cơ cấu lần 2'),
    ).toBeInTheDocument();

    const box = within(panel).getByRole('textbox', { name: 'Bình luận' });
    fireEvent.change(box, { target: { value: 'Đã xem, đồng ý.' } });
    fireEvent.submit(box.closest('form')!);
    expect(await within(panel).findByText('Đã xem, đồng ý.')).toBeInTheDocument();
  });

  it('shares the session and previews what a narrow-scope recipient sees', async () => {
    routeId = 'ws-npl-sept';
    renderWithToast(<SessionsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Chia sẻ' }));
    const dialog = await screen.findByRole('dialog', {
      name: /Chia sẻ “Điều tra nợ xấu bán lẻ T9”/,
    });
    expect(await within(dialog).findByText('Lê Minh Châu')).toBeInTheDocument();
    expect(within(dialog).getByText(/câu kết luận về DH Bắc Bộ được ẩn/)).toBeInTheDocument();

    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Thêm người, nhóm' }), {
      target: { value: 'Trần' },
    });
    fireEvent.click(await within(dialog).findByRole('button', { name: /Trần Bảo An/ }));
    expect(within(dialog).getByText('Trần Bảo An')).toBeInTheDocument();

    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Chia sẻ' }).at(-1)!);
    expect(await screen.findByText('Đã chia sẻ “Điều tra nợ xấu bán lẻ T9”')).toBeInTheDocument();
  });

  it('opens the session report that is waiting for review', async () => {
    routeId = 'ws-npl-sept';
    renderWithToast(<SessionsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo báo cáo' }));
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith('analysis', 'REPORT', 'report-risk-sept'),
    );
  });

  it('keeps the page usable when the session fails to load', async () => {
    routeId = 'ws-missing';
    renderWithToast(<SessionsPage />);
    expect(await screen.findByText(/Không tải được phiên làm việc/)).toBeInTheDocument();
  });
});

describe('ReportReviewPage', () => {
  it('shows the report with its checks and keeps Phát hành closed while one fails', async () => {
    routeId = 'report-risk-sept';
    renderWithToast(<ReportReviewPage />);
    expect(
      await screen.findByRole('heading', { name: 'Nợ xấu bán lẻ tháng 9/2026' }),
    ).toBeInTheDocument();
    expect(screen.getByText('chưa gắn nguồn')).toBeInTheDocument();
    expect(screen.getByText('“19 khoản” chưa gắn nguồn')).toBeInTheDocument();
    expect(screen.getByText('Chờ duyệt')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Phát hành' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });

  it('publishes once every check passes', async () => {
    routeId = 'report-risk-sept';
    // A copy whose checks all pass; publishing answers with it published.
    const passing = {
      id: 'report-risk-sept',
      workspace_id: 'ws-npl-sept',
      status: 'pending_review',
      drafted_by: 'Agent Báo cáo',
      tile_count: 4,
      drafted_at: new Date().toISOString(),
      reviewer_name: 'Phạm Đức Long',
      document: {
        heading: 'Nợ xấu bán lẻ tháng 9/2026',
        summary: '',
        figures: [],
        recommendation: '',
      },
      checks: [{ ok: true, title: 'Số có căn cứ', detail: '12/12 số gắn với ô hoặc chỉ số' }],
      recipients_text: 'GRP-RISK-ANALYST',
    };
    const published: string[] = [];
    server.use(
      http.get('*/v1/session-reports/:id', () => HttpResponse.json(passing)),
      http.post('*/v1/session-reports/:action', ({ params }) => {
        published.push(String(params.action));
        return HttpResponse.json({ ...passing, status: 'published' });
      }),
    );
    renderWithToast(<ReportReviewPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Phát hành' }));
    expect(await screen.findByText('Đã phát hành')).toBeInTheDocument();
    expect(published).toEqual(['report-risk-sept:publish']);
  });

  it('asks for a note before requesting changes', async () => {
    routeId = 'report-risk-sept';
    renderWithToast(<ReportReviewPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Yêu cầu sửa' }));
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }));
    expect(screen.getByText(/Ghi điều cần sửa/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Điều cần sửa' }), {
      target: { value: 'Gắn “19 khoản” với ô 5.' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi yêu cầu' }));
    expect(await screen.findByText('Đã yêu cầu sửa')).toBeInTheDocument();
  });
});
