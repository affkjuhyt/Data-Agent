/**
 * P06 Chuẩn bị dữ liệu against the v1 API contract: the SQL editor and saving a dataset, the pipeline
 * list and its schedule, cleaning by conversation, saving the output, approving a write-back,
 * and a lineage graph laid out from the API.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import LineagePage from './LineagePage';
import { layoutLineage } from './lineageLayout';
import PipelinePage from './PipelinePage';
import PipelineSavePage from './PipelineSavePage';
import PipelinesPage from './PipelinesPage';
import SqlPage from './SqlPage';
import WritebackPage from './WritebackPage';
import { getLineage } from '../../lib/v1/dataPrep';

const navigate = vi.fn();
let routeId: string | undefined;
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = undefined;
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

describe('SqlPage', () => {
  it('discovers the real table schema, runs the query, and saves it as a dataset', async () => {
    wrap(<SqlPage />);
    expect(await screen.findByRole('button', { name: 'mart.loans_dedup_0930' })).toBeInTheDocument();
    await waitFor(() =>
      expect((screen.getByLabelText('Câu truy vấn SQL') as HTMLTextAreaElement).value).toContain(
        'FROM mart.loans_dedup_0930',
      ),
    );

    fireEvent.click(screen.getByRole('button', { name: /Chạy/ }));
    expect(await screen.findByText('Truy vấn xong · 20 dòng · 0,8 giây')).toBeInTheDocument();
    expect(screen.getByText('Quyền: Rủi ro tín dụng · cột customer_id đã che')).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: 'ĐB sông Hồng' })).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: 'Dùng NPL_RATIO v4' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu thành dataset' }));
    const dialog = await screen.findByRole('dialog', { name: 'Lưu thành dataset' });
    // The Data Space defaults to the first one the caller has, once the list loads.
    await waitFor(() =>
      expect(within(dialog).getByRole('combobox', { name: 'Data Space' })).not.toHaveTextContent(
        'Đang tải',
      ),
    );
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lưu' }));
    expect(await screen.findByText('Đã tạo bảng loans_npl_by_region')).toBeInTheDocument();
  });

  it('refuses a statement that writes', async () => {
    wrap(<SqlPage />);
    fireEvent.change(screen.getByLabelText('Câu truy vấn SQL'), {
      target: { value: 'DELETE FROM ld_loans' },
    });
    fireEvent.keyDown(screen.getByLabelText('Câu truy vấn SQL'), { key: 'Enter', metaKey: true });
    expect(await screen.findByText('Trình SQL chỉ chạy câu đọc dữ liệu.')).toBeInTheDocument();
  });
});

describe('PipelinesPage', () => {
  it('lists pipelines, refuses to run one waiting for its input, and saves a schedule', async () => {
    wrap(<PipelinesPage />);
    expect(
      await screen.findByText(
        '5 pipeline · chạy bằng Temporal · 4 thành công lần gần nhất · 1 đang chờ job nạp',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('raw.customers + crm → prep.customers')).toBeInTheDocument();
    expect(screen.getByText('06:12 · 4 phút')).toBeInTheDocument();
    expect(screen.getByText('01/10')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Giao dịch thẻ theo ngày' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Chạy ngay/ }));
    expect(
      await screen.findByText(/Không chạy được: Job nạp đầu vào chưa xong/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thao tác với Mart rủi ro tín dụng' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Lịch chạy/ }));
    const dialog = await screen.findByRole('dialog', { name: 'Lịch chạy · mart.credit_risk' });
    expect(
      await within(dialog).findByText(/Chạy ngay khi job nạp Core banking xong/),
    ).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Hằng ngày lúc 06:30' }));
    expect(await within(dialog).findByText(/Chạy lúc 06:30/)).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Lưu lịch' }));
    expect(await screen.findByText('Đã lên lịch · chạy hằng ngày lúc 06:30')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Gộp khách hàng trùng' }));
    expect(navigate).toHaveBeenCalledWith('data', 'PIPELINE', 'pl-customers');
  });
});

describe('PipelinePage', () => {
  it('shows the steps with rows in and out, previews a step, and adds one by conversation', async () => {
    routeId = 'pl-loans-retail';
    wrap(<PipelinePage />);
    expect(
      await screen.findByText('Đầu vào raw.ld_loans · 1.284.562 dòng · xem trước trên 1.000 dòng'),
    ).toBeInTheDocument();
    const row2 = screen.getByRole('row', { name: /Bỏ dòng trùng loan_id/ });
    expect(within(row2).getByText('−8')).toBeInTheDocument();
    expect(screen.getAllByText('mới')).toHaveLength(3);
    expect(screen.getByText('Xem trước bước 4 trên nguồn')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('row', { name: /Chuẩn hoá branch_code/ }));
    expect(await screen.findByText('Xem trước bước 5 trên nguồn')).toBeInTheDocument();
    expect(await screen.findByText('012045')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Xem SQL' }));
    expect(screen.getByText(/LPAD\(REPLACE\(branch_code/)).toBeInTheDocument();

    const composer = screen.getByPlaceholderText('Mô tả bước tiếp theo…');
    fireEvent.change(composer, { target: { value: 'Đổi tên cột outstanding_amt thành du_no' } });
    fireEvent.keyDown(composer, { key: 'Enter' });
    expect(await screen.findByText(/Đã thêm bước 7/)).toBeInTheDocument();
    expect(
      screen.getByRole('cell', { name: 'Đổi tên cột outstanding_amt thành du_no' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Lưu thành bảng' }));
    expect(navigate).toHaveBeenCalledWith('data', 'PIPELINE_SAVE', 'pl-loans-retail');
  });
});

describe('PipelineSavePage', () => {
  it('saves the output table with its schedule and goes back to the list', async () => {
    routeId = 'pl-loans-retail';
    wrap(<PipelineSavePage />);
    expect(await screen.findByDisplayValue('prep.loans_retail')).toBeInTheDocument();
    expect(screen.getByText('6 · tính mới')).toBeInTheDocument();
    expect(screen.getByText(/Pipeline “Mart rủi ro tín dụng” sẽ đọc bảng này/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: 'Theo giờ' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu và lên lịch' }));
    expect(await screen.findByText('Đã tạo bảng prep.loans_retail')).toBeInTheDocument();
    expect(screen.getByText('Đã lên lịch · chạy mỗi 2 giờ')).toBeInTheDocument();
    expect(navigate).toHaveBeenCalledWith('data', 'PIPELINES');
  });
});

describe('WritebackPage', () => {
  it('opens the write-back waiting for approval and approves step 1 of 2', async () => {
    wrap(<WritebackPage />);
    expect(await screen.findByText('Ghi ngược hạng tín dụng vào CRM')).toBeInTheDocument();
    expect(screen.getByText('1.240 dòng · hiển thị 5')).toBeInTheDocument();
    expect(
      screen.getByText(/31 khách hàng đang có yêu cầu điều chỉnh thủ công/),
    ).toBeInTheDocument();
    expect(screen.getByText('Sau bước 1')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Duyệt bước 1/2' }));
    expect(await screen.findByText('Đã duyệt bước 1/2 · chờ Ngô Thanh Bình')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Duyệt bước/ })).not.toBeInTheDocument();
  });

  it('needs a reason to reject', async () => {
    routeId = 'wb-0042';
    wrap(<WritebackPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Từ chối' }));
    const dialog = screen.getByRole('dialog', { name: 'Từ chối lệnh ghi ngược' });
    expect(within(dialog).getByRole('button', { name: 'Từ chối' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.change(within(dialog).getByLabelText('Lý do từ chối'), {
      target: { value: 'Chờ chốt kỳ tháng 9 rồi mới ghi' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Từ chối' }));
    expect(await screen.findByText('Đã từ chối WB-0042')).toBeInTheDocument();
    // The header status and the first approver both read "Đã từ chối".
    expect(screen.getAllByText('Đã từ chối')).toHaveLength(2);
    expect(screen.queryByRole('button', { name: /Duyệt bước/ })).not.toBeInTheDocument();
  });
});

describe('LineagePage', () => {
  it('draws the graph from the API and explains the selected node', async () => {
    wrap(<LineagePage />);
    // Without a target the first dataset of the Data Space is drawn.
    expect(
      await screen.findByRole('heading', { name: 'NPL_RATIO v4 đi từ đâu' }),
    ).toBeInTheDocument();
    const graph = screen.getByRole('group', { name: 'Sơ đồ dòng dữ liệu' });
    expect(within(graph).getAllByRole('button')).toHaveLength(9);

    fireEvent.click(within(graph).getByRole('button', { name: /prep\.loans_retail/ }));
    // What made the node, read from the edge into it.
    expect(screen.getByText('pipeline')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Đổi đối tượng' }));
    const dialog = await screen.findByRole('dialog', { name: 'Đổi đối tượng' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xem dòng dữ liệu' }));
    expect(navigate).toHaveBeenCalledWith('data', 'LINEAGE', 'metric-npl');
  });

  it('lays nodes out by depth, consumers beside the focus', async () => {
    const layout = layoutLineage(await getLineage('metric-npl'));
    const at = (id: string) => layout.nodes.find((n) => n.id === id);
    expect(at('src-core')?.x).toBe(20);
    expect(at('raw-sales')?.x).toBe(200);
    expect(at('mart-credit')?.x).toBe(560);
    expect(at('metric-npl')?.x).toBe(740);
    // Consumers share the focus column, one above and one below.
    expect(at('report-t9')?.x).toBe(740);
    expect(at('report-t9')!.y).toBeLessThan(at('metric-npl')!.y);
    expect(at('agent-risk')!.y).toBeGreaterThan(at('metric-npl')!.y);
    // The mart sits between its two inputs.
    expect(at('mart-credit')!.y).toBeGreaterThan(at('prep-loans')!.y);
    expect(at('mart-credit')!.y).toBeLessThan(at('raw-sales')!.y);
    // Freshness per node is not tracked yet, so no edge is drawn as late.
    expect(layout.edges.filter((e) => e.late)).toEqual([]);
  });
});
