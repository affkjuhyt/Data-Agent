/**
 * Forecast, model and job screens on the api's shapes: predictions, accuracy, backtest and the
 * job's event stream. An empty space offers to define a model.
 */
import type { ReactElement } from 'react';
import { render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { queryClient } from '../../lib/v1/queryClient';
import ForecastJobPage from './ForecastJobPage';
import ForecastModelPage from './ForecastModelPage';
import ForecastPage from './ForecastPage';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const MODEL_ID = 'model-doanh-thu';
const VERSION_ID = 'version-3';
const JOB_ID = 'job-doanh-thu';

const modelDetail = () => ({
  id: MODEL_ID,
  metric_code: 'DOANH_THU',
  frequency: 'month',
  status: 'active',
  scope: {},
  owner_id: 'u-owner',
  current_version_id: VERSION_ID,
  retrain_cron: null,
  data_space_id: 'ds-aw',
  created_at: '2026-09-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  degraded: false,
  rolling_mase: '0.81',
  backtest_mase: '0.64',
  current_version: {
    id: VERSION_ID,
    model_id: MODEL_ID,
    version: 3,
    algorithm: 'ets',
    status: 'pending_approval',
    training_from: '2024-01-01',
    training_to: '2026-09-01',
    n_points: 33,
    n_exog: 0,
    pooled_series: 1,
    features: [],
    model_card: { 'Mục tiêu': 'Doanh thu tháng' },
    params: {},
    ensemble_of: null,
    data_snapshot_refs: {},
    backtest_id: 'bt-1',
    approved_at: null,
    approved_by: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
  },
});

const prediction = {
  id: 'pred-1',
  job_id: JOB_ID,
  model_version_id: VERSION_ID,
  scenario_id: null,
  period: '2026-11-01',
  point: '428.6',
  lo80: '421.9',
  hi80: '435.3',
  lo95: '418.2',
  hi95: '439.0',
  cutoff_at: '2026-09-30T00:00:00Z',
  created_at: '2026-10-10T03:42:00Z',
  interval_method: 'conformal',
  exog_uncertainty: 'none',
  is_experimental: false,
  contributions: {},
};

const jobDetail = (status: string) => ({
  id: JOB_ID,
  metric_code: 'DOANH_THU',
  frequency: 'month',
  horizon: 12,
  status,
  cutoff_at: '2026-09-30T00:00:00Z',
  created_at: '2026-10-10T03:30:00Z',
  finished_at: null,
  eta_at: '2026-10-10T03:42:00Z',
  model_version_id: null,
  data_space_id: 'ds-aw',
  requested_by: 'u-me',
  question_id: null,
  agent_run_id: null,
  notify_channels: [],
  assumptions: [],
  scope: {},
  progress: {},
  labels: [],
  predictions: [],
});

let releaseEvents = () => {};
let eventsGate = Promise.resolve();

const server = setupServer(
  http.get('*/v1/forecast-models', () =>
    HttpResponse.json({
      items: [{ id: MODEL_ID, metric_code: 'DOANH_THU', frequency: 'month', status: 'active' }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/forecast-models/:id/predictions', () =>
    HttpResponse.json({
      items: [prediction],
      next_page_token: '',
      model_version_id: VERSION_ID,
      labels: ['approved'],
      assumptions: [],
    }),
  ),
  http.get('*/v1/forecast-models/:id/accuracy', () =>
    HttpResponse.json({
      items: [
        {
          id: 'acc-1',
          model_version_id: VERSION_ID,
          period: '2026-09-01',
          point: '418.9',
          actual: '420.5',
          abs_scaled_error: '0.4',
          in80: true,
          in95: false,
          rolling_mase: '0.81',
          recorded_at: '2026-10-02T00:00:00Z',
        },
      ],
      next_page_token: '',
      rolling_mase: '0.81',
      backtest_mase: '0.64',
      degraded: false,
    }),
  ),
  http.get('*/v1/forecast-models/:id', () => HttpResponse.json(modelDetail())),
  http.get('*/v1/forecast-model-versions/:id', () => HttpResponse.json(modelDetail().current_version)),
  http.get('*/v1/forecast-model-versions/:id/backtest', () =>
    HttpResponse.json({
      id: 'bt-1',
      model_version_id: VERSION_ID,
      frequency: 'month',
      horizon: 12,
      mase: '0.64',
      in_sample_mase: null,
      cutoffs: ['2026-06-01', '2026-07-01'],
      baseline_metrics: { mase: 1, smape: 0.12, coverage80: 0.8, coverage95: 0.95 },
      metrics: { smape: 0.08, coverage80: 0.83, coverage95: 0.96 },
      candidates: [{ algorithm: 'ets', mase: 0.64, smape: 0.08, coverage80: 0.83, coverage95: 0.96 }],
      started_at: '2026-10-01T00:00:00Z',
      finished_at: '2026-10-01T00:10:00Z',
      created_at: '2026-10-01T00:00:00Z',
    }),
  ),
  http.get('*/v1/forecasts/:id', () => HttpResponse.json(jobDetail('training'))),
  http.get('*/v1/forecasts/:id/events', async () => {
    await eventsGate;
    return new HttpResponse(
      ['id: 1', 'event: done', 'data: {"status":"ready"}', ''].join('\n'),
      { headers: { 'Content-Type': 'text/event-stream' } },
    );
  }),
  http.get('*/v1/forecast-ledger', () => HttpResponse.json({ items: [], next_page_token: '' })),
);

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  queryClient.clear();
  server.resetHandlers();
  eventsGate = new Promise((resolve) => {
    releaseEvents = resolve;
  });
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const show = (page: ReactElement) => render(<ToastProvider>{page}</ToastProvider>);

describe('forecast screens on the api', () => {
  it('reads a model forecast from its predictions when the space has a model', async () => {
    show(<ForecastPage />);
    expect(
      await screen.findByRole('heading', { level: 1, name: /DOANH_THU · T11\/2026/ }),
    ).toBeInTheDocument();
    const table = screen.getByRole('table', { name: 'Dự báo theo kỳ' });
    expect(within(table).getByText('421,9 – 435,3')).toBeInTheDocument();
    expect(within(table).getByText('428,6')).toBeInTheDocument();
  });

  it('shows accuracy and the backtest horizon on the model page', async () => {
    routeId = MODEL_ID;
    show(<ForecastModelPage />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Mô hình dự báo · DOANH_THU' })).toBeInTheDocument();
    const backtest = await screen.findByRole('table', { name: 'Backtest theo tầm' });
    expect(within(backtest).getByText('12 kỳ')).toBeInTheDocument();
    expect(within(backtest).getByText('0,64')).toBeInTheDocument();
    const accuracy = screen.getByRole('table', { name: 'Sai số của v3 theo kỳ' });
    expect(within(accuracy).getByText('T9/2026')).toBeInTheDocument();
    expect(within(accuracy).getByText('420,5')).toBeInTheDocument();
    expect(within(accuracy).getByText('Không')).toBeInTheDocument();
  });

  it('follows the job until the event stream says it is ready', async () => {
    routeId = JOB_ID;
    show(<ForecastJobPage />);
    const training = await screen.findByText('Huấn luyện các mô hình ứng viên');
    expect(training.closest('button')).toHaveAttribute('aria-current', 'step');
    expect(screen.getByText('Chỉ số: DOANH_THU')).toBeInTheDocument();
    releaseEvents();
    expect(await screen.findByRole('link', { name: 'Xem kết quả dự báo' })).toBeInTheDocument();
  });

  it.each([
    ['forecast', ForecastPage],
    ['model', ForecastModelPage],
    ['job', ForecastJobPage],
  ] as const)('offers to create a model when %s has nothing to show', async (_name, Page) => {
    server.use(
      http.get('*/v1/forecast-models', () =>
        HttpResponse.json({ items: [], next_page_token: '' }),
      ),
    );
    show(<Page />);
    expect(await screen.findByRole('heading', { name: 'Chưa có mô hình dự báo' })).toBeInTheDocument();
    const create = screen.getByRole('link', { name: 'Tạo mô hình dự báo' });
    create.click();
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('analysis', 'FORECAST_MODEL_NEW'));
  });
});
