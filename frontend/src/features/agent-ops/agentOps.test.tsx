/**
 * Đánh giá & triển khai and Điều phối against the A2 reads of the agent-ops mock: a deployment
 * compared on its eval run, widening with undo and rolling back with a reason, adding an eval
 * item, a lead run and its children paused and resumed on the lead.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse, type RequestHandler } from 'msw';
import type { ReactElement } from 'react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { deploymentFixture, evalRunFixture, ROLLOUT_AGENT_ID } from '../../mocks/agentOps';
import { createHandlers } from '../../mocks/handlers';
import EvaluatePage from './EvaluatePage';
import OrchestrationPage from './OrchestrationPage';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
// A rollout and a team are read in several dependent requests through the whole mock.
const SLOW = { timeout: 5000 };
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
  it('reads the deployment, the eval run and live traffic of the agent', async () => {
    routeId = ROLLOUT_AGENT_ID;
    renderWith(<EvaluatePage />);
    expect(await screen.findByRole('heading', { name: /Agent Thu nợ · v6/ }, SLOW)).toBeInTheDocument();
    expect(screen.getByText('Triển khai 50%')).toBeInTheDocument();
    expect(
      screen.getByText(
        'v6 thêm trigger theo job nạp và điều kiện dừng chi phí · đang chạy 50% lượt cùng v5',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Bộ đánh giá · 40 tình huống' }),
    ).toBeInTheDocument();
    const rate = screen.getByText('Tỷ lệ đạt').closest('tr')!;
    expect(
      within(rate)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Tỷ lệ đạt', '95,0%', '97,5%', '≥ 95%']);
    const changed = screen.getByText('Câu đổi kết quả so với bản trước').closest('tr')!;
    expect(
      within(changed)
        .getAllByRole('cell')
        .map((c) => c.textContent),
    ).toEqual(['Câu đổi kết quả so với bản trước', '—', '1 câu', '—']);
    expect(screen.getByText('v5 · 50%')).toBeInTheDocument();
    expect(screen.getByText('Chỉ số 7 ngày')).toBeInTheDocument();
    expect(screen.getByText('· Tỷ lệ lỗi tăng quá 5 điểm so với bản cũ')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Giữ/ })).toBeNull();
  });

  it('widens through the confirm dialog, and "Lùi lại" restores the share', async () => {
    routeId = ROLLOUT_AGENT_ID;
    renderWith(<EvaluatePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tăng lên 100%' }, SLOW));
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Tăng v6 lên 100% lượt chạy?')).toBeInTheDocument();
    expect(within(dialog).getByText(/Cổng chất lượng đã đạt 1\/1/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tăng lên 100%' }));

    expect(await screen.findByText('v6 đang nhận 100% lượt chạy')).toBeInTheDocument();
    expect(screen.getByText('Đã triển khai 100%')).toBeInTheDocument();
    // A completed deployment takes no traffic change, so there is nothing to undo.
    expect(screen.queryByRole('button', { name: 'Lùi lại' })).toBeNull();
  });

  it('undoes a partial widening back to the previous share', async () => {
    routeId = ROLLOUT_AGENT_ID;
    const [deployment] = [deploymentFixture()];
    renderWith(
      <EvaluatePage />,
      http.get(`*/v1/agents/${ROLLOUT_AGENT_ID}`, () =>
        HttpResponse.json({
          id: ROLLOUT_AGENT_ID,
          name: 'Agent Thu nợ',
          active_deployment: { ...deployment, traffic_pct: 10 },
        }),
      ),
      http.patch('*/v1/deployments/:id', async ({ request }) => {
        const { traffic_pct } = (await request.json()) as { traffic_pct: number };
        return HttpResponse.json({ ...deployment, traffic_pct });
      }),
    );
    fireEvent.click(await screen.findByRole('button', { name: 'Tăng lên 50%' }, SLOW));
    fireEvent.click(
      within(screen.getByRole('alertdialog')).getByRole('button', { name: 'Tăng lên 50%' }),
    );
    expect(await screen.findByText('v6 đang nhận 50% lượt chạy')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Lùi lại' }));
    await waitFor(() => expect(screen.getByText('Triển khai 10%')).toBeInTheDocument());
  });

  it('rolls back through the danger dialog only with a reason', async () => {
    routeId = ROLLOUT_AGENT_ID;
    renderWith(<EvaluatePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Quay lại v5' }, SLOW));
    const dialog = screen.getByRole('alertdialog');
    expect(within(dialog).getByText('Lùi về v5?')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lùi về v5' }));
    expect(await within(dialog).findByText('Ghi lý do quay lui.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Lý do quay lui'), {
      target: { value: 'Người dùng báo sai kết luận hai lần' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lùi về v5' }));

    expect(await screen.findByText('Đã lùi về v5 · v6 ngừng nhận lượt mới')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Tăng lên 100%' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Hoàn tác' })).toBeNull();
  });

  it('blocks widening while the pass rate misses its threshold', async () => {
    routeId = ROLLOUT_AGENT_ID;
    renderWith(
      <EvaluatePage />,
      http.get('*/v1/eval-runs/:id', () =>
        HttpResponse.json({ ...evalRunFixture(), pass_rate: 0.9 }),
      ),
    );
    const widen = await screen.findByRole('button', { name: 'Tăng lên 100%' }, SLOW);
    expect(widen).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(widen);
    expect(screen.queryByRole('alertdialog')).toBeNull();
  });

  it('adds a case to the eval set as an eval item', async () => {
    routeId = ROLLOUT_AGENT_ID;
    let sent: unknown;
    renderWith(<EvaluatePage />);
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && request.url.endsWith('/items'))
        sent = await request.clone().json();
    });
    fireEvent.click(await screen.findByRole('button', { name: 'Thêm tình huống' }, SLOW));
    const dialog = screen.getByRole('dialog', { name: 'Thêm tình huống thử' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm vào bộ thử' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      'Cần nhập đầu vào và kỳ vọng.',
    );
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Đầu vào' }), {
      target: { value: 'Khách quá hạn 3 ngày, đã nhắc hôm qua' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Kỳ vọng' }), {
      target: { value: 'Không nhắc lại trong 48 giờ' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm vào bộ thử' }));
    expect(await screen.findByText('Đã thêm tình huống vào bộ thử')).toBeInTheDocument();
    expect(sent).toEqual({
      input: { text: 'Khách quá hạn 3 ngày, đã nhắc hôm qua' },
      expected: { text: 'Không nhắc lại trong 48 giờ' },
      category: 'ask_at_right_time',
    });
    server.events.removeAllListeners();
    expect(
      screen.getByRole('heading', { name: 'Bộ đánh giá · 41 tình huống' }),
    ).toBeInTheDocument();
  });

  it('says so when the agent has no deployment in progress', async () => {
    routeId = 'agent-reconcile';
    renderWith(<EvaluatePage />);
    expect(await screen.findByText('Chưa có phiên bản nào đang thử', {}, SLOW)).toBeInTheDocument();
  });

  it('asks to open the screen from an agent without one', async () => {
    renderWith(<EvaluatePage />);
    expect(await screen.findByText(/Mở Đánh giá & triển khai từ một agent/)).toBeInTheDocument();
  });

  it('refuses a crafted agent id from the URL without calling the API', async () => {
    routeId = '..%2Fadmin';
    renderWith(<EvaluatePage />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Đường dẫn chứa mã không hợp lệ.');
  });
});

describe('OrchestrationPage', () => {
  it('finds the lead run, its delegated runs with their task, and the team cost', async () => {
    renderWith(<OrchestrationPage />);
    expect(
      await screen.findByRole('heading', { name: /Lượt điều phối của Agent Phân tích/ }, SLOW),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Agent Phân tích dẫn dắt, giao việc cho 3 agent khác/),
    ).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Giao việc' })).getAllByRole('listitem');
    expect(rows).toHaveLength(4);
    expect(within(rows[2]).getByText('So nhóm nợ trước/sau với số đã nộp')).toBeInTheDocument();
    expect(within(rows[2]).getByText('→ 19 khoản cơ cấu chuyển nhóm 2 → 3')).toBeInTheDocument();
    expect(within(rows[3]).getByText('Agent Giám sát rủi ro')).toBeInTheDocument();
    expect(within(rows[3]).getByText('được giao bởi Agent Phân tích')).toBeInTheDocument();
    expect(within(rows[3]).getByText('Chờ bạn')).toBeInTheDocument();
    expect(screen.getByText(/31\.200 đ · 4 lượt/)).toBeInTheDocument();
  });

  it('pauses and resumes the lead run, and opens the runs', async () => {
    routeId = 'run-4480';
    renderWith(<OrchestrationPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạm dừng tất cả' }, SLOW));
    expect(await screen.findByText(/^Đã tạm dừng cả nhóm #run-4480/, {}, SLOW)).toBeInTheDocument();
    const rows = within(screen.getByRole('list', { name: 'Giao việc' })).getAllByRole('listitem');
    expect(within(rows[0]).getByText('Tạm dừng')).toBeInTheDocument();
    // Finished and waiting runs keep their status.
    expect(within(rows[1]).getByText('Xong')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Tiếp tục tất cả' }));
    expect(await screen.findByText(/^Cả nhóm #run-4480 chạy tiếp/, {}, SLOW)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem từng lượt' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUNS');
  });

  it('shows a load error with a retry inside the region', async () => {
    renderWith(
      <OrchestrationPage />,
      http.get('*/v1/runs', () =>
        HttpResponse.json({ code: 'unavailable', detail: 'Hết giờ chờ.' }, { status: 503 }),
      ),
    );
    expect(await screen.findByRole('alert', {}, SLOW)).toHaveTextContent(
      'Không tải được lượt điều phối. Hết giờ chờ.',
    );
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});
