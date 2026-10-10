/**
 * Khám phá and the full-screen artifact against the v1 mock: the default view of the frame,
 * a query the server refuses, the aggregate-only lock, drilling, asking in words, and the
 * artifact's versions and stale usages.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http } from 'msw';
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
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() =>
  server.resetHandlers(
    http.post('*/v1/access-requests', async ({ request }) => {
      accessRequests.push(await request.clone().json());
      return undefined;
    }),
    ...createHandlers(),
  ),
);
afterEach(() => {
  navigate.mockReset();
  accessRequests.length = 0;
});
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);
const shelf = (name: string) => screen.getByRole('group', { name: `Kệ ${name}` });

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
          label: 'Quý',
          group_label: 'Thời gian',
          values: [
            { value: 'q2', label: 'Q2/2026' },
            { value: 'q3', label: 'Q3/2026' },
          ],
          incompatible_metric_ids: [],
          incompatible_reason: null,
        },
      ),
    ).toBe('∈ Q2, Q3/2026');
  });
});

describe('ExplorePage', () => {
  it('opens on the dataset view of the frame, with the agent reading it', async () => {
    wrap(<ExplorePage />);
    expect(
      await screen.findByRole('heading', { name: 'Nợ xấu bán lẻ theo vùng · Q2 so với Q3/2026' }),
    ).toBeInTheDocument();
    expect(within(shelf('Chỉ số')).getByText('Nợ xấu (%)')).toBeInTheDocument();
    expect(within(shelf('Chiều')).getByText('Vùng')).toBeInTheDocument();
    expect(within(shelf('Lọc')).getByText('= Bán lẻ')).toBeInTheDocument();
    expect(within(shelf('Lọc')).getByText('∈ Q2, Q3/2026')).toBeInTheDocument();
    const table = screen.getByRole('table');
    expect(within(table).getByText('2,32%')).toBeInTheDocument();
    expect(within(table).getByText('+0,61')).toHaveClass('text-error');
    expect(await screen.findByText('Nợ xấu tăng ở 5/6 vùng')).toBeInTheDocument();
    expect(screen.getByText('Khớp số đã nộp và Báo cáo rủi ro tháng 9')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: 'Bộ dữ liệu: Rủi ro tín dụng bán lẻ' }),
    ).toBeInTheDocument();
  });

  it('shows why the server refuses a dimension, and recovers when it is removed', async () => {
    wrap(<ExplorePage />);
    await screen.findByRole('heading', { name: /Nợ xấu bán lẻ theo vùng/ });
    fireEvent.click(within(shelf('Chiều')).getByRole('button', { name: 'Thêm vào kệ Chiều' }));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Nhóm nợ' }));
    expect(await screen.findByText('Không chạy được truy vấn này')).toBeInTheDocument();
    expect(
      screen.getByText(/Chiều “Nhóm nợ” không dùng được với chỉ số “Nợ xấu \(%\)”/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ Nhóm nợ' }));
    expect(
      await screen.findByRole('heading', { name: /Nợ xấu bán lẻ theo vùng/ }),
    ).toBeInTheDocument();
  });

  it('locks the detail rows for an aggregate-only role and sends an access request', async () => {
    wrap(<ExplorePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Xem 3.412 dòng gốc' }));
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
    expect(screen.getByRole('heading', { name: /Nợ xấu bán lẻ theo vùng/ })).toBeInTheDocument();
  });

  it('drills from a suggested next step and from a column', async () => {
    wrap(<ExplorePage />);
    fireEvent.click(await screen.findByRole('button', { name: /Đi sâu: DH Bắc Bộ theo sản phẩm/ }));
    await waitFor(() => expect(within(shelf('Chiều')).getByText('Sản phẩm')).toBeInTheDocument());
    expect(within(shelf('Lọc')).getByText('= DH Bắc Bộ')).toBeInTheDocument();
    expect(
      await screen.findByRole('heading', { name: /Nợ xấu bán lẻ theo sản phẩm/ }),
    ).toBeInTheDocument();
  });

  it('drills into branches when a region column is clicked', async () => {
    wrap(<ExplorePage />);
    fireEvent.click(await screen.findByRole('button', { name: /^Đi sâu: Tây Nguyên/ }));
    await waitFor(() => expect(within(shelf('Chiều')).getByText('Chi nhánh')).toBeInTheDocument());
    expect(await screen.findByRole('heading', { name: /theo chi nhánh/ })).toBeInTheDocument();
    expect(screen.getAllByText('CN Đắk Lắk').length).toBeGreaterThan(0);
  });

  it('fills the shelves from a question asked in words', async () => {
    wrap(<ExplorePage />);
    await screen.findByRole('heading', { name: /Nợ xấu bán lẻ theo vùng/ });
    fireEvent.click(screen.getByRole('radio', { name: 'Hỏi bằng lời' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Hỏi bằng lời' }), {
      target: { value: 'dư nợ theo chi nhánh theo tháng' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Điền kệ' }));
    await waitFor(() => expect(within(shelf('Chỉ số')).getByText('Dư nợ')).toBeInTheDocument());
    expect(within(shelf('Chiều')).getByText('Tháng')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Kéo thả' })).toHaveAttribute('aria-checked', 'true');
  });

  it('opens the metric definition for "Vì sao số này?"', async () => {
    wrap(<ExplorePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Vì sao số này?' }));
    expect(navigate).toHaveBeenCalledWith('data', 'METRIC', 'metric-npl-ratio');
  });
});

describe('ArtifactPage', () => {
  it('shows the artifact, restores an older version and updates a stale usage', async () => {
    routeId = 'art-npl-region';
    wrap(<ArtifactPage />);
    expect(
      await screen.findByRole('heading', { name: 'Đóng góp vào mức tăng NPL · theo vùng' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Đã kiểm tra số')).toBeInTheDocument();
    expect(
      screen.getByText(
        'DH Bắc Bộ +0,92 → +0,61 · trọng tâm chuyển từ thẻ tín dụng sang vay tiêu dùng',
      ),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', {
        name: /Bản tin tuần 41 đang dùng v2 đã cũ\. Cập nhật lên v3\?/,
      }),
    );
    expect(await screen.findByText('· Bản tin tuần 41 · bản chụp v3')).toBeInTheDocument();
    expect(screen.queryByText(/đang dùng v2 đã cũ/)).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Khôi phục v2' }));
    expect(
      await screen.findByText('Khôi phục v2: Theo định nghĩa nội bộ nhóm 2–5'),
    ).toBeInTheDocument();
    const current = screen.getAllByRole('listitem').find((li) => li.getAttribute('aria-current'));
    expect(current).toHaveTextContent('v4Khôi phục v2');

    fireEvent.click(screen.getByRole('radio', { name: 'SQL' }));
    expect(screen.getByText(/FROM semantic\.credit_risk_retail/)).toBeInTheDocument();
  });

  it('says the artifact is missing', async () => {
    routeId = 'art-unknown';
    wrap(<ArtifactPage />);
    expect(await screen.findByText(/Không tải được kết quả/)).toBeInTheDocument();
  });
});
