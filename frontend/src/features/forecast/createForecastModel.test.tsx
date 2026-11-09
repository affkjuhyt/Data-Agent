/**
 * Defining a forecast model: certified metrics only, members of each allowed dimension, month
 * by default, horizon 1–24, then the training job.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { queryClient } from '../../lib/v1/queryClient';
import CreateForecastModelPage from './CreateForecastModelPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId: '' }) }));

const JOB_ID = 'job-train-1';
let posted: unknown;

const metric = (code: string, name: string, certified: boolean) => ({
  id: `metric-${code}`,
  code,
  display_name: name,
  certified,
  status: 'active',
  current_version: 2,
  data_space_id: 'ds-aw',
  owner_id: 'u-owner',
  canonical_dataset_id: null,
  consumer_group: null,
  in_filed_report: false,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
});

const server = setupServer(
  http.get('*/v1/metrics', ({ request }) => {
    const certified = new URL(request.url).searchParams.get('certified');
    const items = [metric('DOANH_THU', 'Doanh thu', true), metric('DRAFT_METRIC', 'Chỉ số nháp', false)];
    return HttpResponse.json({
      items: certified === 'true' ? items.filter((item) => item.certified) : items,
      next_page_token: '',
    });
  }),
  http.get('*/v1/metrics/:id', () =>
    HttpResponse.json({
      id: 'metric-DOANH_THU',
      code: 'DOANH_THU',
      status: 'active',
      current_version: 2,
      versions: [
        {
          id: 'mv-2',
          version: 2,
          status: 'certified',
          display_name: 'Doanh thu',
          allowed_dimensions: ['dim-cat'],
        },
      ],
    }),
  ),
  http.get('*/v1/entities', () =>
    HttpResponse.json({
      items: [
        {
          key: 'sales',
          name: 'Bán hàng',
          primary_dataset: { id: 'dataset-sales', name: 'FactInternetSales' },
        },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/entities/:key', () =>
    HttpResponse.json({
      key: 'sales',
      name: 'Bán hàng',
      primary_dataset: { id: 'dataset-sales', name: 'FactInternetSales' },
      dimensions: [
        {
          id: 'dim-cat',
          code: 'product_category',
          name: 'Nhóm sản phẩm',
          expression: 'product_category',
          is_time: false,
          parent_dimension_id: null,
        },
        {
          id: 'dim-time',
          code: 'order_month',
          name: 'Tháng',
          expression: 'order_month',
          is_time: true,
          parent_dimension_id: null,
        },
      ],
    }),
  ),
  http.get('*/v1/datasets/:id', () =>
    HttpResponse.json({
      id: 'dataset-sales',
      columns: [
        {
          name: 'product_category',
          profile: {
            top_values: [
              { value: 'Bikes', count: 40 },
              { value: 'Components', count: 12 },
            ],
          },
        },
      ],
    }),
  ),
  http.post('*/v1/forecast-models', async ({ request }) => {
    posted = await request.json();
    return HttpResponse.json(
      {
        id: 'model-new',
        job_id: JOB_ID,
        metric_code: 'DOANH_THU',
        frequency: 'month',
        status: 'active',
      },
      { status: 201 },
    );
  }),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  posted = undefined;
  queryClient.clear();
});
afterEach(() => {
  navigate.mockReset();
  server.resetHandlers();
});
afterAll(() => server.close());

const show = () => render(<ToastProvider><CreateForecastModelPage /></ToastProvider>);

describe('CreateForecastModelPage', () => {
  it('offers certified metrics, month and horizon 12, and the dimension members', async () => {
    show();
    expect(await screen.findByRole('heading', { level: 1, name: 'Tạo mô hình dự báo' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Chỉ số' })).toHaveTextContent('Doanh thu · DOANH_THU');
    expect(screen.queryByText(/Chỉ số nháp/)).not.toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Tháng' })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Quý' })).not.toBeChecked();
    expect(screen.getByLabelText('Tầm')).toHaveValue(12);
    const scope = await screen.findByRole('group', { name: 'Nhóm sản phẩm' });
    expect(within(scope).getByRole('checkbox', { name: 'Bikes' })).not.toBeChecked();
    expect(within(scope).getByRole('checkbox', { name: 'Components' })).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Tháng' })).not.toBeInTheDocument();
  });

  it('refuses a horizon outside 1 to 24 and does not create a model', async () => {
    show();
    expect(await screen.findByRole('button', { name: 'Chỉ số' })).toHaveTextContent(
      'Doanh thu · DOANH_THU',
    );
    fireEvent.change(screen.getByLabelText('Tầm'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo mô hình' }));
    expect(await screen.findByText('Tầm từ 1 đến 24 kỳ.')).toBeInTheDocument();
    expect(posted).toBeUndefined();
    expect(navigate).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Tầm'), { target: { value: '25' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo mô hình' }));
    expect(await screen.findByText('Tầm từ 1 đến 24 kỳ.')).toBeInTheDocument();
    expect(posted).toBeUndefined();
  });

  it('asks for a metric when none is certified', async () => {
    server.use(
      http.get('*/v1/metrics', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'Tạo mô hình' }));
    expect(await screen.findByText('Chọn một chỉ số đã chứng nhận.')).toBeInTheDocument();
    expect(posted).toBeUndefined();
  });

  it('posts the model with its scope and opens the training job', async () => {
    show();
    const scope = await screen.findByRole('group', { name: 'Nhóm sản phẩm' });
    fireEvent.click(within(scope).getByRole('checkbox', { name: 'Bikes' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Quý' }));
    fireEvent.change(screen.getByLabelText('Tầm'), { target: { value: '4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Tạo mô hình' }));
    await waitFor(() => expect(posted).toEqual({
      metric_code: 'DOANH_THU',
      frequency: 'quarter',
      horizon: 4,
      scope: { product_category: ['Bikes'] },
    }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_JOB', JOB_ID);
  });
});
