/**
 * Khám phá and the full-screen artifact against the v1 mock, in A3's shapes: the certified
 * metrics and their dimensions, a query_run read back into the chart, a dimension the server
 * refuses (BR-81), the aggregate-only lock, drilling, asking in words, saving to a session, and
 * an answer opened as an artifact.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import ArtifactPage from './ArtifactPage';
import ExplorePage from './ExplorePage';
import { filterText, formatDelta, formatValue } from './format';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const accessRequests: unknown[] = [];
const tiles: unknown[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() =>
  server.resetHandlers(
    http.post('*/v1/access-requests', async ({ request }) => {
      accessRequests.push(await request.clone().json());
      return undefined;
    }),
    http.post('*/v1/workspaces/:id/tiles', async ({ params, request }) => {
      tiles.push({ workspace_id: params.id, ...((await request.clone().json()) as object) });
      return undefined;
    }),
    ...createHandlers(),
  ),
);
afterEach(() => {
  navigate.mockReset();
  accessRequests.length = 0;
  tiles.length = 0;
});
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);
const shelf = (name: string) => screen.getByRole('group', { name: `Kệ ${name}` });
const field = async (name: string) =>
  fireEvent.doubleClick(
    within(await screen.findByRole('complementary', { name: 'Chỉ số và chiều' })).getByRole(
      'button',
      { name: new RegExp(`^${name.replace(/[()]/g, '\\$&')}`) },
    ),
  );

/** Nợ xấu (%) by region, Q2 against Q3: the frame's default view, built from the field list. */
async function nplByRegion() {
  wrap(<ExplorePage />);
  await field('Nợ xấu (%)');
  await field('Vùng');
  await field('Quý');
  return screen.findByRole('heading', { name: 'Nợ xấu (%) theo vùng · Q2/2026 so với Q3/2026' });
}

describe('format', () => {
  it('writes figures the Vietnamese way', () => {
    expect(formatValue(1.8, 'percent')).toBe('1,80%');
    expect(formatValue(3412, 'count')).toBe('3.412');
    expect(formatDelta(0.61, 'percent')).toBe('+0,61');
    expect(formatDelta(-0.04, 'percent')).toBe('−0,04');
    expect(
      filterText(
        { dimension_id: 'quarter', op: 'in', values: ['q2', 'q3'] },
        {
          id: 'quarter',
          code: 'quy',
          label: 'Quý',
          group_label: 'Thời gian',
          values: [
            { value: 'q2', label: 'Q2/2026' },
            { value: 'q3', label: 'Q3/2026' },
          ],
          incompatible_metric_ids: [],
          incompatible_reason: null,
          parent_id: null,
          is_time: true,
        },
      ),
    ).toBe('∈ Q2, Q3/2026');
  });
});

describe('ExplorePage', () => {
  it('lists the certified metrics, runs the shelves and has the agent read the result', async () => {
    wrap(<ExplorePage />);
    expect(
      await screen.findByText('Kéo một chỉ số và một chiều vào kệ, hoặc hỏi bằng lời.'),
    ).toBeInTheDocument();
    screen.getByRole('complementary', { name: 'Chỉ số và chiều' });
    await field('Nợ xấu (%)');
    await field('Vùng');
    await field('Quý');
    expect(
      await screen.findByRole('heading', {
        name: 'Nợ xấu (%) theo vùng · Q2/2026 so với Q3/2026',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/^NPL_RATIO v4/)).toBeInTheDocument();
    expect(within(shelf('Chỉ số')).getByText('Nợ xấu (%)')).toBeInTheDocument();
    expect(within(screen.getByRole('table')).getByText('Tây Nguyên')).toBeInTheDocument();
    expect(await screen.findByText(/^Tăng ở \d\/6 vùng$/)).toBeInTheDocument();
  });

  it('shows why the server refuses a dimension, and recovers when it is removed', async () => {
    await nplByRegion();
    fireEvent.click(within(shelf('Chiều')).getByRole('button', { name: 'Thêm vào kệ Chiều' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Nhóm nợ' }));
    expect(await screen.findByText('Không chạy được truy vấn này')).toBeInTheDocument();
    expect(
      screen.getByText(/Chiều “Nhóm nợ” không dùng được với chỉ số “Nợ xấu \(%\)”/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ Nhóm nợ' }));
    expect(
      await screen.findByRole('heading', { name: /Nợ xấu \(%\) theo vùng/ }),
    ).toBeInTheDocument();
  });

  it('locks the rows for an aggregate-only role and sends an access request', async () => {
    server.use(
      http.get('*/v1/query-runs/:id/rows', ({ request }) =>
        new URL(request.url).searchParams.get('page_size') === '50'
          ? HttpResponse.json(
              {
                code: 'aggregate_only',
                status: 403,
                detail: 'Vai trò của bạn chỉ xem số tổng hợp.',
              },
              { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
            )
          : undefined,
      ),
    );
    await nplByRegion();
    fireEvent.click(screen.getByRole('button', { name: /^Xem 12 dòng kết quả$/ }));
    expect(
      await screen.findByRole('heading', { name: 'Bạn chỉ xem được số tổng hợp' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Yêu cầu quyền' }));
    expect(
      await screen.findByText('Đã gửi yêu cầu quyền tới người phụ trách.'),
    ).toBeInTheDocument();
    expect(accessRequests[0]).toMatchObject({
      capability: 'view_detail',
      data_space_id: 'ds-rui-ro-tin-dung',
    });
    fireEvent.click(screen.getByRole('button', { name: 'Xem theo vùng' }));
    expect(screen.getByRole('heading', { name: /Nợ xấu \(%\) theo vùng/ })).toBeInTheDocument();
  });

  it('opens the result rows from the query run', async () => {
    await nplByRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Xem 12 dòng kết quả' }));
    const rows = await screen.findByRole('region', { name: 'Dòng gốc' });
    expect(within(rows).getByText('12 dòng')).toBeInTheDocument();
    expect(within(rows).getByText('metric')).toBeInTheDocument();
  });

  it('drills into branches when a region column is clicked', async () => {
    await nplByRegion();
    fireEvent.click(screen.getByRole('button', { name: /^Đi sâu: Tây Nguyên/ }));
    await waitFor(() => expect(within(shelf('Chiều')).getByText('Chi nhánh')).toBeInTheDocument());
    expect(within(shelf('Lọc')).getByText('= Tây Nguyên')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: /theo chi nhánh/ })).toBeInTheDocument();
    expect(screen.getAllByText('CN Đắk Lắk').length).toBeGreaterThan(0);
  });

  it('fills the shelves from a question asked in words', async () => {
    wrap(<ExplorePage />);
    await screen.findByRole('complementary', { name: 'Chỉ số và chiều' });
    fireEvent.click(screen.getByRole('radio', { name: 'Hỏi bằng lời' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Hỏi bằng lời' }), {
      target: { value: 'dư nợ theo chi nhánh' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Điền kệ' }));
    await waitFor(() => expect(within(shelf('Chỉ số')).getByText('Dư nợ')).toBeInTheDocument());
    expect(within(shelf('Chiều')).getByText('Chi nhánh')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Kéo thả' })).toHaveAttribute('aria-checked', 'true');
  });

  it('opens the metric definition for "Vì sao số này?"', async () => {
    await nplByRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Vì sao số này?' }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC', 'exp-npl-ratio');
  });

  it('saves the result as a tile of a session', async () => {
    await nplByRegion();
    fireEvent.click(screen.getByRole('button', { name: 'Lưu vào phiên' }));
    fireEvent.click(await screen.findByRole('combobox', { name: 'Phiên đích' }));
    fireEvent.click(screen.getByRole('option', { name: 'Điều tra nợ xấu bán lẻ T9' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Đã lưu vào Điều tra nợ xấu bán lẻ T9')).toBeInTheDocument();
    expect(tiles[0]).toMatchObject({
      workspace_id: 'ws-npl-sept',
      kind: 'chart',
      spec: { metric_ids: ['exp-npl-ratio'], dimensions: ['dim-region', 'dim-quarter'] },
    });
  });
});

describe('ArtifactPage', () => {
  it('shows an answer full screen with its figures, SQL, and saves it to a session', async () => {
    routeId = 'ans-npl-region';
    wrap(<ArtifactPage />);
    expect(
      await screen.findByRole('heading', { name: 'Nợ xấu bán lẻ tăng ở đâu so với T8?' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Đã kiểm tra số')).toBeInTheDocument();
    expect(screen.getAllByText('0,61%').length).toBeGreaterThan(0);
    expect(screen.queryByText(/Lịch sử phiên bản/)).toBeNull();

    fireEvent.click(screen.getByRole('radio', { name: 'SQL' }));
    expect(await screen.findByText(/FROM semantic\.credit_risk_retail/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('link', { name: 'Theo dõi' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'WATCH_NEW');

    fireEvent.click(screen.getByRole('button', { name: 'Lưu' }));
    fireEvent.click(await screen.findByRole('combobox', { name: 'Phiên đích' }));
    fireEvent.click(screen.getByRole('option', { name: 'Điều tra nợ xấu bán lẻ T9' }));
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Đã lưu vào Điều tra nợ xấu bán lẻ T9')).toBeInTheDocument();
    expect(tiles[0]).toMatchObject({ kind: 'question', spec: { answer_id: 'ans-npl-region' } });
  });

  it('says the artifact is missing', async () => {
    routeId = 'art-unknown';
    server.use(
      http.get('*/v1/answers/art-unknown', () =>
        HttpResponse.json(
          { code: 'not_found', status: 404, detail: 'Answer not found.' },
          { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    wrap(<ArtifactPage />);
    expect(await screen.findByText(/Không tải được kết quả/)).toBeInTheDocument();
  });
});
