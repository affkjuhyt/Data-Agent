/**
 * The two detail pages of Cần bạn against the v1 mock: deciding a proposal and choosing between
 * two sources, both leaving the request resolved.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { listInbox } from '../../lib/v1/api';
import { inboxFixture } from '../../mocks/fixtures';
import type { ProposalWire } from '../../lib/v1/types';
import { createHandlers } from '../../mocks/handlers';
import ConflictPage from './ConflictPage';
import ProposalPage from './ProposalPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('ProposalPage', () => {
  it('shows the change, its effect and the checks, and accepting resolves the request', async () => {
    routeId = 'pr-0318';
    wrap(<ProposalPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /NPL_RATIO v5/ }),
    ).toBeInTheDocument();
    expect(screen.getByText('Bản đề xuất v5')).toBeInTheDocument();
    expect(screen.getByText('+0,12 điểm · 214 khoản')).toBeInTheDocument();
    expect(screen.getByText('Bộ câu hỏi chuẩn 96,8% (v4: 96,5%)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    const open = await listInbox({ status: 'open' });
    expect(open.items.map((r) => r.id)).not.toContain('hitl-npl-v5');
  });

  it('asks for a reason before rejecting', async () => {
    routeId = 'pr-0318';
    wrap(<ProposalPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Từ chối' }));
    const dialog = screen.getByRole('alertdialog');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(
      await screen.findByText('Ghi lý do từ chối để agent biết cần sửa gì.'),
    ).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByRole('textbox', { name: 'Lý do từ chối (bắt buộc)' }), {
      target: { value: 'QĐ chưa áp cho kỳ đã nộp' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
  });
});

/** A proposal as the api sends it: no PROPOSAL display fields, jsonb the producer chose. */
const fromApi = (eval_run: ProposalWire['eval_run']): ProposalWire => ({
  id: '0192f0aa-0000-7000-8000-000000000001',
  data_space_id: '0192f0aa-0000-7000-8000-0000000000d5',
  kind: 'metric_change',
  target_service: 'semantic',
  target_ref: null,
  title: 'Đổi định nghĩa CASA_RATIO',
  diff: { sql: 'balance_type IN (1, 2)' },
  reason: null,
  evidence: [],
  impact: {},
  impact_level: 'medium',
  proposed_by: '0192f0aa-0000-7000-8000-0000000000a1',
  assignee_id: null,
  eval_run_id: eval_run?.id ?? null,
  eval_run,
  changed_results: [],
  reviews: [],
  requires_compliance: false,
  status: 'pending_review',
  expires_at: new Date(Date.now() + 86_400_000).toISOString(),
  applied_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
});

describe('ProposalPage on the api', () => {
  it('shows an unshaped diff as sent and asks for the regressions to be reviewed (BR-28)', async () => {
    let body: unknown;
    const proposal = fromApi({
      id: '0192f0aa-0000-7000-8000-0000000000e1',
      status: 'done',
      item_count: 10,
      passed: 8,
      failed: 2,
      pass_rate: '0.8',
      regressions: 2,
      baseline_run_id: null,
      finished_at: new Date().toISOString(),
    });
    server.use(
      http.get('*/v1/proposals/:id', () => HttpResponse.json(proposal)),
      http.post('*/v1/proposals/:action', async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ ...proposal, status: 'accepted' });
      }),
    );
    routeId = proposal.id;
    wrap(<ProposalPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /CASA_RATIO/ }),
    ).toBeInTheDocument();
    expect(screen.getByText(/balance_type IN \(1, 2\)/)).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Ảnh hưởng' })).not.toBeInTheDocument();
    expect(screen.getByText('8/10 câu đánh giá đạt')).toBeInTheDocument();
    expect(screen.getByText('2 câu đổi kết quả so với bản trước')).toBeInTheDocument();

    const accept = screen.getByRole('button', { name: 'Chấp nhận' });
    expect(accept).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByLabelText(/Tôi đã xem từng câu đổi kết quả/));
    fireEvent.click(accept);
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    expect(body).toEqual({ decision: 'accept', reviewed_regressions: true });
  });

  it('says when the proposal has not been evaluated', async () => {
    server.use(http.get('*/v1/proposals/:id', () => HttpResponse.json(fromApi(null))));
    routeId = 'p-none';
    wrap(<ProposalPage />);
    expect(await screen.findByText('Đề xuất này chưa chạy bộ đánh giá.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chấp nhận' })).not.toHaveAttribute('aria-disabled');
  });
});

describe('ConflictPage', () => {
  it('shows both numbers and sends the chosen source with the rule', async () => {
    routeId = 'hitl-core-vs-dwh';
    wrap(<ConflictPage />);
    expect(await screen.findByText('2.296,4 tỷ')).toBeInTheDocument();
    expect(screen.getByText('2.298,2 tỷ')).toBeInTheDocument();
    expect(screen.getByText('Chênh 1,8 tỷ (0,08%)')).toBeInTheDocument();
    expect(screen.getByLabelText(/Dùng Core banking/)).toBeChecked();
    expect(screen.getByLabelText(/Ghi nhớ thành quy tắc/)).toBeChecked();

    fireEvent.click(screen.getByLabelText('Dùng DWH bán lẻ (theo số đã tổng hợp cho báo cáo)'));
    fireEvent.click(screen.getByRole('button', { name: 'Chọn phương án' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
    const open = await listInbox({ status: 'open' });
    expect(open.items.map((r) => r.id)).not.toContain('hitl-core-vs-dwh');
  });

  it('draws a conflict whose context has none of the drawn blocks, and still decides it', async () => {
    const conflict = inboxFixture().find((r) => r.id === 'hitl-core-vs-dwh')!;
    server.use(
      http.get('*/v1/hitl-requests/:id', () =>
        HttpResponse.json({ ...conflict, context: {}, agent: undefined }),
      ),
    );
    routeId = conflict.id;
    wrap(<ConflictPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: conflict.title }),
    ).toBeInTheDocument();
    expect(screen.queryByText('Agent đã kiểm tra')).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Dùng Core banking/)).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Chọn phương án' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('needs-you'));
  });
});
