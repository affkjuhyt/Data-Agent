/**
 * R1-15: the onboarding checklist of a Data Space says, step by step, what is left by name —
 * a source not connected, a metric not approved, an eval run below the bar — and opens the screen
 * that does the work with that Data Space selected.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import DataSpaceSetupPage from './DataSpaceSetupPage';

const navigate = vi.fn();
const setDataSpace = vi.fn();
vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate, setDataSpace, routeId: 'ds-1' }),
}));

const at = '2026-10-30T08:00:00Z';
const page = (items: unknown[]) => HttpResponse.json({ items, next_page_token: null });
const source = (name: string, status: string, status_reason: string | null = null) => ({
  id: `src-${name}`,
  data_space_id: 'ds-1',
  name,
  kind: 'postgres',
  mode: 'federated',
  status,
  status_reason,
  created_at: at,
  updated_at: at,
});
const metric = (code: string, certified: boolean, status = 'active') => ({
  id: `m-${code}`,
  code,
  display_name: code === 'NPL' ? 'Tỷ lệ nợ xấu' : null,
  certified,
  status,
  data_space_id: 'ds-1',
  owner_id: 'u-1',
  current_version: 1,
  canonical_dataset_id: null,
  consumer_group: null,
  in_filed_report: false,
  pack_metric_id: null,
  created_at: at,
  updated_at: at,
});
const evalSet = (items: number, latest: { passed: number; item_count: number } | null) => ({
  id: 'set-1',
  data_space_id: 'ds-1',
  subject_type: 'context',
  subject_id: null,
  name: 'Câu hỏi pilot',
  pass_threshold: '0.90',
  min_items: 10,
  status: 'active',
  created_at: at,
  updated_at: at,
  item_count: items,
  latest_run: latest && {
    id: 'er-1',
    eval_set_id: 'set-1',
    subject_version: 'context',
    trigger: 'manual',
    trigger_ref: null,
    item_count: latest.item_count,
    passed: latest.passed,
    failed: latest.item_count - latest.passed,
    correct_refusals: 0,
    pass_rate: latest.passed / latest.item_count,
    baseline_run_id: null,
    regressions: 0,
    status: 'completed',
    started_at: at,
    finished_at: at,
    created_at: at,
  },
});

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  navigate.mockReset();
  setDataSpace.mockReset();
});
afterAll(() => server.close());

function space({
  sources,
  metrics,
  sets,
}: {
  sources: unknown[];
  metrics: unknown[];
  sets: unknown[];
}) {
  const asked: (string | null)[] = [];
  const scoped = (items: unknown[]) => ({ request }: { request: Request }) => {
    asked.push(request.headers.get('X-Data-Space'));
    return page(items);
  };
  server.use(
    http.get('*/v1/data-spaces', () =>
      page([
        {
          id: 'ds-1',
          code: 'DS-TD',
          name: 'Tín dụng',
          status: 'onboarding',
          steward_id: 'u-1',
          opened_at: null,
          description: null,
          my_roles: [],
          my_capabilities: ['steward'],
          steward_name: 'A',
          member_count: 2,
        },
      ]),
    ),
    http.get('*/v1/sources', scoped(sources)),
    http.get('*/v1/metrics', scoped(metrics)),
    http.get('*/v1/eval-sets', scoped(sets)),
  );
  return asked;
}

const step = async (title: string) => within(await screen.findByRole('listitem', { name: title }));

describe('Thiết lập Data Space', () => {
  it('names what is left at each step, for this Data Space', async () => {
    const asked = space({
      sources: [source('Core', 'active'), source('Kho cũ', 'needs_fix', 'sai mật khẩu')],
      metrics: [metric('NPL', false), metric('LOANS', true), metric('OLD', false, 'retired')],
      sets: [evalSet(12, { passed: 10, item_count: 12 })],
    });
    render(<DataSpaceSetupPage />);

    const questions = await step('Câu hỏi của khách');
    expect(questions.getByText('Xong')).toBeInTheDocument();
    expect(questions.getByText(/12 câu hỏi của khách/)).toBeInTheDocument();

    const sources = await step('Nguồn dữ liệu');
    expect(sources.getByText('1/2 nguồn truy vấn được')).toBeInTheDocument();
    expect(sources.getByText(/Kho cũ: cần sửa kết nối \(sai mật khẩu\)/)).toBeInTheDocument();

    const metrics = await step('Chỉ số');
    expect(metrics.getByText('Chưa xong')).toBeInTheDocument();
    expect(metrics.getByText('1/2 chỉ số đã duyệt')).toBeInTheDocument();
    expect(metrics.getByText(/Tỷ lệ nợ xấu/)).toBeInTheDocument();

    const evaluation = await step('Bộ đánh giá');
    expect(evaluation.getByText(/10\/12 câu đạt \(83,3%\), cần 90%/)).toBeInTheDocument();
    expect(evaluation.getByText(/2 câu chưa đạt/)).toBeInTheDocument();
    expect(screen.getByText(/Còn 2 bước/)).toBeInTheDocument();
    // Every read names the Data Space being set up, not the one of the top bar.
    expect(asked).toEqual(['ds-1', 'ds-1', 'ds-1']);

    fireEvent.click(metrics.getByRole('button', { name: 'Duyệt chỉ số' }));
    expect(setDataSpace).toHaveBeenCalledWith({ id: 'ds-1', name: 'Tín dụng' });
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC_CATALOG');
  });

  it('says a new Data Space has everything left, and when it is ready', async () => {
    space({ sources: [], metrics: [], sets: [] });
    const { unmount } = render(<DataSpaceSetupPage />);
    expect((await step('Câu hỏi của khách')).getByText('Chưa có bộ câu hỏi của khách')).toBeInTheDocument();
    expect((await step('Nguồn dữ liệu')).getByText('Chưa có nguồn nào')).toBeInTheDocument();
    expect(screen.getByText(/Còn 4 bước/)).toBeInTheDocument();
    unmount();

    space({
      sources: [source('Core', 'active')],
      metrics: [metric('NPL', true)],
      sets: [evalSet(15, { passed: 15, item_count: 15 })],
    });
    render(<DataSpaceSetupPage />);
    expect(await screen.findByText(/Data Space đã sẵn sàng/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Duyệt chỉ số' })).toBeNull();
  });
});
