/**
 * Lượt chạy against the v1 mock: filtering the list, the story and controls of one run, and
 * editing the plan of a stopped run.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import AgentTeamPage from './AgentTeamPage';
import RunDetailPage from './RunDetailPage';
import RunsPage from './RunsPage';
import { runDuration, shortCost } from './runStatus';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, routeId, dataSpace: { id: 'ds-rr', name: 'Rủi ro tín dụng' } }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('RunsPage', () => {
  it('lists runs, filters by status and search, and opens one', async () => {
    wrap(<RunsPage />);
    expect(await screen.findByText('#427')).toBeInTheDocument();
    expect(screen.getAllByRole('row')).toHaveLength(13);

    fireEvent.click(screen.getByRole('radio', { name: 'Lỗi · 1' }));
    expect(screen.getAllByRole('row')).toHaveLength(2);
    expect(screen.getByText('Lỗi kết nối')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Tất cả' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Tìm lượt chạy' }), {
      target: { value: '#414' },
    });
    expect(screen.getAllByRole('row')).toHaveLength(2);

    fireEvent.click(screen.getByText('Soạn báo cáo rủi ro tháng 9'));
    expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-414');
  });
});

describe('RunDetailPage', () => {
  it('tells the run as sentences beside its plan, and switches to the steps', async () => {
    routeId = 'run-427';
    wrap(<RunDetailPage />);
    expect(
      await screen.findByText('Đọc 1,28 triệu khoản vay từ Core banking (dữ liệu 30/09).'),
    ).toBeInTheDocument();
    expect(screen.getByText('bây giờ')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: 'Tiến độ bước hiện tại' })).toHaveAttribute(
      'aria-valuenow',
      '60',
    );
    expect(screen.getByText('Kế hoạch · 5 bước')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Xem kỹ thuật' }));
    // The step under way opens first, with whose permissions it runs and the query it sent.
    const running = await screen.findByRole('button', { name: /tool\.query overdue_check/ });
    expect(running).toHaveAttribute('aria-pressed', 'true');
    expect(within(running).getByText('đang chạy')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'tool.query overdue_check' })).toBeInTheDocument();
    expect(screen.getByText('Đỗ Hải Yến · chính sách POL-002 áp dụng')).toBeInTheDocument();
    expect(screen.getByText(/WHERE l\.overdue_days <> s\.overdue_days/)).toBeInTheDocument();
    expect(screen.queryByText('Kế hoạch · 5 bước')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /llm\.reason/ }));
    expect(screen.getByRole('heading', { name: 'llm.reason' })).toBeInTheDocument();
    expect(screen.getByText(/· chạy 12 giây/)).toBeInTheDocument();
  });

  it('leads a failed run with the error, what stayed safe, and reruns it', async () => {
    let body: unknown;
    // A2's :retry; answered here so the test sees the body the screen sends.
    server.use(
      http.post('*/v1/runs/:action', async ({ params, request }) => {
        if (params.action !== 'run-409:retry') return undefined;
        body = await request.json();
        return HttpResponse.json({ id: 'run-430' }, { status: 202 });
      }),
    );
    routeId = 'run-409';
    wrap(<RunDetailPage />);
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Không kết nối được DWH bán lẻ sau 3 lần thử',
    );
    expect(screen.getByText('Lỗi · đã dừng an toàn')).toBeInTheDocument();
    expect(screen.getByText(/Tạm dừng, sẽ tự thử lại khi nguồn có tín hiệu/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kết nối DWH bán lẻ/ })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Xem kỹ thuật' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Chạy với nguồn dự phòng' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-430'));
    expect(body).toEqual({ use_fallback: true });
  });

  it('pauses, then cancels after confirmation', async () => {
    routeId = 'run-427';
    wrap(<RunDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Tạm dừng' }));
    const pause = screen.getByRole('alertdialog', { name: 'Tạm dừng Agent Đối soát?' });
    expect(within(pause).getByText(/dừng an toàn ở bước hiện tại/)).toBeInTheDocument();
    fireEvent.click(within(pause).getByRole('button', { name: 'Tạm dừng' }));
    expect(await screen.findByRole('button', { name: 'Chạy tiếp' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Huỷ lượt chạy' }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Huỷ lượt chạy' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Chạy tiếp' })).toBeNull());
    expect(screen.getByText('Đã huỷ')).toBeInTheDocument();
  });
});

describe('PlanEditDrawer', () => {
  it('edits, drops and adds steps of a stopped run, then applies the next plan version', async () => {
    routeId = 'run-418';
    wrap(<RunDetailPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Sửa kế hoạch' }));
    const drawer = screen.getByRole('dialog', { name: 'Can thiệp kế hoạch · lượt #418' });
    expect(
      within(drawer).getByText(/Agent đang dừng an toàn ở bước 3.*kết quả bước 1–2 được giữ/),
    ).toBeInTheDocument();
    expect(within(drawer).getAllByText('Giữ kết quả')).toHaveLength(2);
    expect(within(drawer).getByRole('button', { name: 'Áp dụng và chạy tiếp' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );

    const step3 = within(drawer).getByRole('listitem', { name: 'Bước Phân rã vùng × sản phẩm' });
    fireEvent.click(within(step3).getByRole('button', { name: /Thao tác với bước/ }));
    fireEvent.click(within(step3).getByRole('menuitem', { name: 'Sửa bước' }));
    fireEvent.change(within(step3).getByLabelText('Tên bước'), {
      target: { value: 'Phân rã vùng × sản phẩm · dùng NPL_RATIO v4' },
    });
    fireEvent.click(within(step3).getByRole('button', { name: 'Xong' }));
    expect(within(step3).getByText('Đã sửa')).toBeInTheDocument();

    const step6 = within(drawer).getByRole('listitem', { name: 'Bước Đưa vào bản tin tuần' });
    fireEvent.click(within(step6).getByRole('button', { name: /Thao tác với bước/ }));
    fireEvent.click(within(step6).getByRole('menuitem', { name: 'Bỏ bước' }));
    expect(within(step6).getByText('Bỏ')).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('button', { name: 'Thêm bước' }));
    fireEvent.change(within(drawer).getByLabelText('Tên bước'), {
      target: { value: 'So sánh cùng kỳ 2025 cho DH Bắc Bộ' },
    });
    fireEvent.click(within(drawer).getByRole('button', { name: 'Xong' }));
    expect(within(drawer).getByText('Thêm')).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('button', { name: 'Áp dụng và chạy tiếp' }));
    expect(
      await screen.findByText('Đã lưu kế hoạch phiên bản 2; Agent Phân tích chạy tiếp'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(await screen.findByText('Kế hoạch · 6 bước')).toBeInTheDocument();
    expect(screen.getByText('So sánh cùng kỳ 2025 cho DH Bắc Bộ')).toBeInTheDocument();
  });

  it('keeps the editor closed while the run is still working', async () => {
    routeId = 'run-427';
    wrap(<RunDetailPage />);
    expect(await screen.findByRole('button', { name: 'Sửa kế hoạch' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
  });
});

describe('run figures', () => {
  it('writes duration and cost the short way of the table', () => {
    const start = new Date(2026, 9, 10, 6, 0, 0).toISOString();
    const end = new Date(2026, 9, 10, 6, 1, 12).toISOString();
    expect(runDuration({ started_at: start, finished_at: end } as never)).toBe('1:12');
    expect(shortCost('8400')).toBe('8k');
    expect(shortCost('900')).toBe('900');
  });
});

// The pause and lock flows chain three requests; the full suite in parallel can be slow.
const SLOW = { timeout: 5000 };

describe('AgentTeamPage', () => {
  it('opens what a busy agent is doing now, and its configuration', async () => {
    wrap(<AgentTeamPage />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Thao tác với Agent Chất lượng dữ liệu' }),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Đang làm gì' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('agents', 'RUN', 'run-415'));

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Agent Chất lượng dữ liệu' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Cấu hình' }));
    expect(navigate).toHaveBeenCalledWith('agents', 'AGENT', 'agent-dq');
  });

  it('pauses a running agent’s run and locks the agent after confirmation', async () => {
    wrap(<AgentTeamPage />);
    fireEvent.click(
      await screen.findByRole('button', { name: 'Thao tác với Agent Đối soát' }, SLOW),
    );
    fireEvent.click(screen.getByRole('menuitem', { name: 'Tạm dừng lượt đang chạy' }));
    const pause = await screen.findByRole(
      'alertdialog',
      { name: 'Tạm dừng Agent Đối soát?' },
      SLOW,
    );
    fireEvent.click(within(pause).getByRole('button', { name: 'Tạm dừng' }));
    expect(await screen.findByText(/^Đã tạm dừng/, {}, SLOW)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Agent Phân tích' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Khoá agent' }));
    const lock = screen.getByRole('alertdialog', { name: 'Khoá Agent Phân tích?' });
    expect(
      within(lock).getByText(/1 yêu cầu HITL của agent chuyển cho chủ sở hữu/),
    ).toBeInTheDocument();
    fireEvent.click(within(lock).getByRole('button', { name: 'Khoá agent' }));
    expect(await screen.findByText('Đã khoá Agent Phân tích', {}, SLOW)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Agent Phân tích' }));
    expect(screen.getByRole('menuitem', { name: 'Mở khoá' })).toBeInTheDocument();
  });

  it('offers to create an agent or start from a pack template when there are none', async () => {
    server.use(
      http.get('*/v1/agents', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    wrap(<AgentTeamPage />);
    expect(await screen.findByText('Chưa có agent nào')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Dùng agent mẫu' }));
    expect(navigate).toHaveBeenCalledWith('data', 'PACKS');
  });
});

describe('RunDetailPage results', () => {
  it('shows what a report run found: each question with its sentence and chart', async () => {
    routeId = 'run-report';
    const step = (seq: number, question: string, summary: string, output: object) => ({
      id: `step-${seq}`,
      seq,
      kind: 'query',
      name: 'query.semantic',
      input: { sql: 'SELECT 1' },
      output: { question, ...output },
      summary,
      status: 'ok',
      attempt: 1,
      child_run_id: null,
      cost: '0',
      started_at: '2026-10-17T12:00:00Z',
      duration_ms: 40,
    });
    server.use(
      http.get('*/v1/runs/run-report', ({ request }) =>
        HttpResponse.json({
          id: 'run-report',
          agent_id: 'agent-x',
          agent_name: 'Báo cáo rủi ro tín dụng',
          config_version: 1,
          data_space_id: 'ds-rr',
          trigger_kind: 'manual',
          trigger_ref: null,
          initiated_by: null,
          parent_run_id: null,
          depth: 0,
          is_test: false,
          status: 'succeeded',
          current_step: 3,
          narrative: [{ at: '2026-10-17T12:00:01Z', text: 'Lập kế hoạch 2 phần.', state: 'done' }],
          cost: '0',
          tokens: 0,
          started_at: '2026-10-17T12:00:00Z',
          finished_at: '2026-10-17T12:00:05Z',
          error: null,
          stop_reason: 'completed',
          created_at: '2026-10-17T12:00:00Z',
          // The results need the steps whichever way the run is viewed.
          steps:
            new URL(request.url).searchParams.get('view') === 'technical'
              ? [
                  { ...step(1, '', '', {}), kind: 'plan', name: 'plan', output: {} },
                  step(
                    2,
                    'Tỷ lệ nợ xấu theo vùng tháng 9/2026',
                    'Miền Bắc: 2,37%; Miền Nam: 1,26%.',
                    {
                      columns: ['vung', 'value'],
                      rows: [
                        ['Miền Bắc', 2.37],
                        ['Miền Nam', 1.26],
                      ],
                      chart_spec: {
                        type: 'bar',
                        x: 'vung',
                        x_label: 'Vùng',
                        y_label: 'Tỷ lệ nợ xấu',
                        unit: '%',
                      },
                    },
                  ),
                  step(3, 'Tỷ lệ nợ xấu tháng 9/2026', 'Tỷ lệ nợ xấu tháng 9/2026 là 1,83%.', {
                    columns: ['value'],
                    rows: [[1.83]],
                    chart_spec: null,
                  }),
                ]
              : undefined,
        }),
      ),
    );
    wrap(<RunDetailPage />);
    const results = await screen.findByRole('region', { name: 'Kết quả · 2 phần' });
    expect(within(results).getByText('Tỷ lệ nợ xấu theo vùng tháng 9/2026')).toBeInTheDocument();
    expect(within(results).getByText('Miền Bắc: 2,37%; Miền Nam: 1,26%.')).toBeInTheDocument();
    expect(within(results).getByText('Tỷ lệ nợ xấu tháng 9/2026 là 1,83%.')).toBeInTheDocument();
    // One chart: the single number has nothing to compare.
    expect(within(results).getAllByRole('figure')).toHaveLength(1);
    expect(within(results).getByText('Tỷ lệ nợ xấu (%) theo Vùng')).toBeInTheDocument();
  });
});

describe('RunDetailPage audit suggestions', () => {
  it('turns a suggested rule into a quality rule when the person takes it', async () => {
    routeId = 'run-audit';
    const posted: unknown[] = [];
    server.use(
      http.get('*/v1/runs/run-audit', () =>
        HttpResponse.json({
          id: 'run-audit',
          agent_id: 'agent-q',
          agent_name: 'Kiểm toán dữ liệu',
          config_version: 1,
          data_space_id: 'ds-rr',
          trigger_kind: 'manual',
          trigger_ref: null,
          initiated_by: null,
          parent_run_id: null,
          depth: 0,
          is_test: false,
          status: 'succeeded',
          current_step: 2,
          narrative: [],
          cost: '0',
          tokens: 0,
          started_at: '2026-10-17T12:00:00Z',
          finished_at: '2026-10-17T12:00:05Z',
          error: null,
          stop_reason: 'completed',
          created_at: '2026-10-17T12:00:00Z',
          steps: [
            {
              id: 'step-2',
              seq: 2,
              kind: 'tool_call',
              name: 'quality.status',
              input: {},
              output: {
                dataset_id: 'ds-loans',
                parts: [],
                suggestions: [
                  {
                    kind: 'unique',
                    column: 'loan_id',
                    params: {},
                    severity: 'high',
                    text: 'Mã khoản vay không trùng.',
                  },
                ],
              },
              summary: 'Kiểm toán Khoản vay (2.400 dòng): 0 vấn đề cần xử lý, 1 cảnh báo.',
              status: 'ok',
              attempt: 1,
              child_run_id: null,
              cost: '0',
              started_at: '2026-10-17T12:00:00Z',
              duration_ms: 40,
            },
          ],
        }),
      ),
      http.post('*/v1/quality-rules', async ({ request }) => {
        posted.push(await request.json());
        return HttpResponse.json({ id: 'rule-1' }, { status: 201 });
      }),
    );
    wrap(<RunDetailPage />);
    const rules = await screen.findByRole('region', { name: 'Gợi ý quy tắc chất lượng' });
    expect(within(rules).getByText('Mã khoản vay không trùng.')).toBeInTheDocument();
    fireEvent.click(within(rules).getByRole('button', { name: 'Tạo quy tắc' }));
    await waitFor(() =>
      expect(posted).toEqual([
        {
          dataset_id: 'ds-loans',
          kind: 'unique',
          column_name: 'loan_id',
          params: {},
          severity: 'high',
        },
      ]),
    );
    expect(await within(rules).findByText('Đã tạo')).toBeInTheDocument();
  });
});
