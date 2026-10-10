/**
 * Đánh giá & triển khai and Điều phối against the agent-ops mock: what the Figma frames show,
 * widening and rolling back through their dialogs with undo, adding an eval case, pausing a team.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse, type RequestHandler } from 'msw';
import type { ReactElement } from 'react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { rolloutsFixture } from '../../mocks/agentOps';
import { createHandlers } from '../../mocks/handlers';
import EvaluatePage from './EvaluatePage';
import OrchestrationPage from './OrchestrationPage';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
  routeId = undefined;
});
afterAll(() => server.close());

const renderWith = (page: ReactElement, ...overrides: RequestHandler[]) => {
  server.use(...createHandlers());
  // server.use prepends, so the overrides go last to win over the stateful mock.
  if (overrides.length) server.use(...overrides);
  return render(<ToastProvider>{page}</ToastProvider>);
};

describe('EvaluatePage', () => {
  it('shows the candidate against the baseline on the eval set and on live traffic', async () => {
    renderWith(<EvaluatePage />);
    expect(await screen.findByRole('heading', { name: /Agent Đối soát · v6/ })).toBeInTheDocument();
    expect(screen.getByText('Triển khai 20%')).toBeInTheDocument();
    expect(
      screen.getByText(
        'v6 thêm trigger theo job nạp và điều kiện dừng chi phí · đang chạy 20% lượt cùng v5',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Bộ đánh giá · 40 tình huống' }),
    ).toBeInTheDocument();
    const correct = screen.getByText('Kết luận đúng').closest('tr')!;
    expect(
      within(correct)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Kết luận đúng', '95,0%', '97,5%', '≥ 95%']);
    const cost = screen.getByText('Chi phí trung bình').closest('tr')!;
    expect(
      within(cost)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Chi phí trung bình', '9.200 đ', '8.400 đ', '≤ 15.000 đ']);
    expect(screen.getByText('v5 · 80%')).toBeInTheDocument();
    expect(screen.getByText('· Người dùng báo sai kết luận của v6 hai lần')).toBeInTheDocument();
  });

  it('widens through the confirm dialog, and "Lùi lại" restores the share', async () => {
    renderWith(<EvaluatePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tăng lên 100%' }));
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Tăng v6 lên 100% lượt chạy?')).toBeInTheDocument();
    expect(within(dialog).getByText(/Cổng chất lượng đã đạt 5\/5/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tăng lên 100%' }));

    expect(await screen.findByText('v6 đang nhận 100% lượt chạy')).toBeInTheDocument();
    expect(screen.getByText('Đã triển khai 100%')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Lùi lại' }));
    await waitFor(() => expect(screen.getByText('Triển khai 20%')).toBeInTheDocument());
  });

  it('rolls back through the danger dialog, and "Hoàn tác" brings the candidate back', async () => {
    renderWith(<EvaluatePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Quay lại v5' }));
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Lùi về v5?')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lùi về v5' }));

    expect(await screen.findByText('Đã lùi về v5 · v6 ngừng nhận lượt mới')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tăng lên 100%' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hoàn tác' }));
    await waitFor(() => expect(screen.getByText('Triển khai 20%')).toBeInTheDocument());
  });

  it('blocks widening while a criterion misses its threshold', async () => {
    const [first] = rolloutsFixture();
    first.eval_set.criteria[0].candidate = 90;
    renderWith(
      <EvaluatePage />,
      http.get('*/v1/rollouts', () => HttpResponse.json({ items: [first], next_page_token: '' })),
    );
    const widen = await screen.findByRole('button', { name: 'Tăng lên 100%' });
    expect(widen).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(widen);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('adds a case from a real run to the eval set', async () => {
    renderWith(<EvaluatePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Thêm tình huống' }));
    const dialog = screen.getByRole('dialog', { name: 'Thêm tình huống thử' });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Lấy từ' }));
    fireEvent.click(await within(dialog).findByRole('option', { name: 'Lượt chạy #427' }));
    expect(within(dialog).getByRole('textbox', { name: 'Đầu vào' })).toHaveValue(
      'Đối soát dư nợ T9 với báo cáo đã nộp',
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm vào bộ thử' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Cần nhập đầu vào và kỳ vọng.',
    );
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Kỳ vọng' }), {
      target: { value: 'Đề xuất derived v4 + hỏi người vì kỳ đã nộp' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm vào bộ thử' }));
    expect(await screen.findByText('Đã thêm tình huống vào bộ thử')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(
      screen.getByRole('heading', { name: 'Bộ đánh giá · 41 tình huống' }),
    ).toBeInTheDocument();
  });

  it('says so when no version is being tried', async () => {
    renderWith(
      <EvaluatePage />,
      http.get('*/v1/rollouts', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    expect(await screen.findByText('Chưa có phiên bản nào đang thử')).toBeInTheDocument();
  });

  it('refuses a crafted agent id from the URL without calling the API', async () => {
    routeId = '..%2Fadmin';
    renderWith(<EvaluatePage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Đường dẫn chứa mã không hợp lệ.');
  });
});

describe('OrchestrationPage', () => {
  it('shows the lead agent, the delegated tasks and the team cost', async () => {
    renderWith(<OrchestrationPage />);
    expect(
      await screen.findByRole('heading', { name: /Điều tra nợ xấu bán lẻ T9/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Agent Phân tích dẫn dắt, giao việc cho 3 agent khác/),
    ).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Giao việc' })).getAllByRole('listitem');
    expect(rows).toHaveLength(4);
    expect(within(rows[3]).getByText('Agent Tài liệu')).toBeInTheDocument();
    expect(within(rows[3]).getByText('được giao bởi Agent Phân tích')).toBeInTheDocument();
    expect(within(rows[3]).getByText('Chờ bạn')).toBeInTheDocument();
    expect(screen.getByText(/31\.200 đ · 4 lượt · 1[01] phút/)).toBeInTheDocument();
  });

  it('pauses and resumes the whole team, and opens the runs', async () => {
    renderWith(<OrchestrationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạm dừng tất cả' }));
    expect(await screen.findByText('Đã tạm dừng cả nhóm R-4480')).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Giao việc' })).getAllByRole('listitem');
    expect(within(rows[0]).getByText('Tạm dừng')).toBeInTheDocument();
    // Finished and waiting runs keep their status.
    expect(within(rows[1]).getByText('Xong')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp tục tất cả' }));
    expect(await screen.findByText('Cả nhóm R-4480 chạy tiếp')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem từng lượt' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUNS');
  });

  it('shows a load error with a retry inside the region', async () => {
    renderWith(
      <OrchestrationPage />,
      http.get('*/v1/orchestrations', () =>
        HttpResponse.json({ code: 'unavailable', detail: 'Hết giờ chờ.' }, { status: 503 }),
      ),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Không tải được lượt điều phối. Hết giờ chờ.',
    );
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});
