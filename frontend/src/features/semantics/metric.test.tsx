/**
 * Chỉ số · Định nghĩa against the v1 mock: the version in use, who owns it, opening the draft
 * through "Sửa" and backtesting it on six periods.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createMetricHandlers } from '../../mocks/metrics';
import MetricPage from './MetricPage';

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate: vi.fn(), routeId: 'metric-npl-ratio' }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createMetricHandlers()));
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <MetricPage />
    </ToastProvider>,
  );

describe('MetricPage', () => {
  it('shows the certified definition, its use and its stewards', async () => {
    page();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Nợ xấu \(%\) · NPL_RATIO/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/v4 đang dùng · chứng nhận bởi Phạm Đức Long 12\/06/),
    ).toBeInTheDocument();
    expect(screen.getByText(/dùng ở 23 artifact, 5 agent, 2 báo cáo định kỳ/)).toBeInTheDocument();
    expect(screen.getByText('Thấp hơn là tốt')).toBeInTheDocument();
    expect(screen.getByText('Số cuối kỳ, theo ngày nghiệp vụ')).toBeInTheDocument();
    expect(screen.getByText(/is_internal_loan = false/)).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /Chủ sở hữu Phạm Đức Long/ })).toBeInTheDocument();
  });

  it('opens the draft from "Sửa" and backtests it on six periods', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Sửa → bản nháp v5' }));
    expect(screen.getByRole('heading', { name: 'Định nghĩa · v5' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy thử trên 6 kỳ' }));
    expect(await screen.findByText('Đang chạy thử v5 trên 6 kỳ')).toBeInTheDocument();
    const v5 = await screen.findByRole('button', { name: /v5 Khoản cơ cấu lần 2/ });
    expect(await within(v5).findByText('Đang chạy thử')).toBeInTheDocument();
  });

  it('edits a draft that is already the version on screen', async () => {
    let expression = 'quang_duong_xanh / so_chuyen_xanh';
    const version = () => ({
      id: 'mv-qd-v1',
      metric_id: 'metric-npl-ratio',
      additivity: 'none',
      allowed_dimensions: [],
      filters: [],
      description: null,
      legal_basis: null,
      proposal_id: null,
      valid_from: null,
      valid_to: null,
      certified_by: null,
      version: 1,
      display_name: 'Quãng đường bình quân',
      expression,
      unit: 'dặm',
      decimals: 2,
      direction: 'neutral',
      period_semantics: 'sum_over_period',
      time_grain: 'month',
      time_dimension_id: null,
      dimension_labels: [],
      change_note: 'Nhập từ tệp',
      status: 'draft',
      certified_at: null,
      certified_by_name: null,
      created_at: '2026-11-01T00:00:00Z',
    });
    server.use(
      http.get('*/v1/metrics/:id', () =>
        HttpResponse.json({
          id: 'metric-npl-ratio',
          code: 'QUANG_DUONG_TB_XANH',
          status: 'draft',
          current_version: null,
          versions: [version()],
          stewardship: [],
          usage: { data_product_ids: [], knowledge_page_ids: [] },
          display_name: 'Quãng đường bình quân',
          data_space_id: 'ds',
          owner_id: 'u',
          canonical_dataset_id: null,
          consumer_group: null,
          in_filed_report: false,
          certified: false,
          created_at: '2026-11-01T00:00:00Z',
          updated_at: '2026-11-01T00:00:00Z',
        }),
      ),
      http.patch('*/v1/metric-versions/:id', async ({ request }) => {
        const body = (await request.json()) as { expression: string };
        expression = body.expression;
        return HttpResponse.json(version());
      }),
    );
    const user = userEvent.setup();
    page();
    await user.click(await screen.findByRole('button', { name: 'Sửa bản nháp v1' }));
    const formula = screen.getByRole('textbox', { name: 'Công thức' });
    await user.clear(formula);
    await user.type(formula, 'quang_duong_xanh / so_chuyen_xanh * 1.6');
    await user.click(screen.getByRole('button', { name: 'Lưu bản nháp' }));
    expect(await screen.findByText('quang_duong_xanh / so_chuyen_xanh * 1.6')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Sửa bản nháp v1' })).not.toBeInTheDocument();
  });
});
