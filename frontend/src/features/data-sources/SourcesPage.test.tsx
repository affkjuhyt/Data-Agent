/**
 * Dữ liệu · Nguồn against the v1 mock: the list as "P04 · Nguồn · Danh sách" draws it, the
 * empty state ("Kết nối dữ liệu · Chưa có nguồn"), the table profile, jobs and a collection.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type { ReactNode } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import CollectionPage from './CollectionPage';
import JobsPage from './JobsPage';
import SourcesPage from './SourcesPage';
import TableProfilePage from './TableProfilePage';
import UploadPage from './UploadPage';

const app = vi.hoisted(() => ({ navigate: vi.fn(), routeId: undefined as string | undefined }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => {
  server.resetHandlers();
  app.navigate.mockReset();
  app.routeId = undefined;
});
afterAll(() => server.close());

const renderPage = (page: ReactNode) => render(<ToastProvider>{page}</ToastProvider>);

describe('SourcesPage', () => {
  it('lists sources with mode, status, freshness and Data Spaces', async () => {
    renderPage(<SourcesPage />);
    const core = (await screen.findByText('Core banking')).closest('tr')!;
    expect(within(core).getByText('oracle · core-db.noibo')).toBeInTheDocument();
    expect(within(core).getByText('Nạp về · CDC')).toBeInTheDocument();
    expect(within(core).getByText('Đang đồng bộ')).toBeInTheDocument();
    expect(within(core).getByText(/· trễ 12 giây$/)).toBeInTheDocument();
    expect(within(core).getByText('Rủi ro tín dụng +2')).toBeInTheDocument();
    expect(screen.getByText('Cần sửa · đăng nhập lỗi')).toBeInTheDocument();
    expect(
      screen.getByText('12 nguồn · 11 đúng lịch · 1 cần sửa · mọi kết nối dùng tài khoản chỉ đọc'),
    ).toBeInTheDocument();

    fireEvent.click(core);
    expect(app.navigate).toHaveBeenCalledWith('data', 'SOURCE', 'src-core');
  });

  it('filters by search and mode', async () => {
    renderPage(<SourcesPage />);
    await screen.findByText('Core banking');
    fireEvent.change(screen.getByLabelText('Tìm nguồn'), { target: { value: 'edw' } });
    expect(screen.queryByText('Core banking')).toBeNull();
    expect(screen.getByText('Kho dữ liệu doanh nghiệp')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Tìm nguồn'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('combobox', { name: 'Lọc theo chế độ' }));
    fireEvent.click(screen.getByRole('option', { name: 'Chế độ: truy vấn tại chỗ' }));
    expect(screen.queryByText('Core banking')).toBeNull();
    expect(screen.getByText('DWH bán lẻ')).toBeInTheDocument();
  });

  it('shows the connector catalogue when there is no source yet', async () => {
    server.use(
      http.get('*/v1/sources', () => HttpResponse.json({ items: [], next_page_token: '' })),
    );
    renderPage(<SourcesPage />);
    expect(await screen.findByRole('heading', { name: 'Kết nối dữ liệu' })).toBeInTheDocument();
    expect(
      await screen.findByText(
        'Chưa có nguồn nào. Chọn một connector bên trái để bắt đầu — thường mất khoảng 5 phút.',
      ),
    ).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Oracle/ }));
    expect(app.navigate).toHaveBeenCalledWith('data', 'CONNECT', 'oracle');
  });

  it('says why the list did not load, with a retry', async () => {
    server.use(
      http.get('*/v1/sources', () =>
        HttpResponse.json({ code: 'unavailable', detail: 'Hết giờ.' }, { status: 503 }),
      ),
    );
    renderPage(<SourcesPage />);
    expect(await screen.findByText(/Không tải được danh sách nguồn/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
  });
});

describe('TableProfilePage', () => {
  it('profiles each column and shows the agent remarks', async () => {
    app.routeId = 'tbl-ld-loans';
    renderPage(<TableProfilePage />);
    expect(await screen.findByRole('heading', { name: 'LD_LOANS' })).toBeInTheDocument();
    expect(screen.getByText(/^1\.284\.562 dòng · 24 cột/)).toBeInTheDocument();
    const flag = screen.getByText('restructure_flag').closest('tr')!;
    expect(within(flag).getByText('62%')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: 'Agent Chuẩn bị dữ liệu nhận xét' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Quyền' }));
    expect(screen.getByText('Mục này đang được làm.')).toBeInTheDocument();
  });

  it('refuses a crafted id without calling the API', async () => {
    app.routeId = '..%2Fadmin';
    renderPage(<TableProfilePage />);
    expect(await screen.findByText(/Đường dẫn chứa mã không hợp lệ/)).toBeInTheDocument();
  });
});

describe('JobsPage', () => {
  it('shows each job with its 24-hour strip and filters problems', async () => {
    renderPage(<JobsPage />);
    expect(await screen.findByText('retail_sales')).toBeInTheDocument();
    expect(screen.getByText('Lỗi · đăng nhập')).toBeInTheDocument();
    expect(screen.getByText('1,28 tr')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: 'Có vấn đề 2' }));
    expect(screen.queryByText('ld_loans')).toBeNull();
    expect(screen.getByText('card_transactions')).toBeInTheDocument();
  });

  it('resumes a paused job', async () => {
    renderPage(<JobsPage />);
    const row = (await screen.findByText('card_transactions')).closest('tr')!;
    fireEvent.click(within(row).getByRole('button', { name: 'Thao tác với card_transactions' }));
    fireEvent.click(within(row).getByRole('menuitem', { name: 'Tiếp tục' }));
    await waitFor(() => expect(within(row).getByText('Đang chạy')).toBeInTheDocument());
  });
});

describe('CollectionPage', () => {
  it('lists documents and opens one on the side', async () => {
    app.routeId = 'col-regulation';
    renderPage(<CollectionPage />);
    expect(await screen.findByRole('heading', { name: 'Văn bản pháp quy' })).toBeInTheDocument();
    expect(screen.getByText(/412 tài liệu · đồng bộ từ SharePoint/)).toBeInTheDocument();
    fireEvent.click(await screen.findByText('TT 31/2024/TT-NHNN · phân loại nợ'));
    const panel = await screen.findByRole('complementary', { name: 'Chi tiết tài liệu' });
    expect(await within(panel).findByText('Đề xuất NPL_RATIO v5')).toBeInTheDocument();
    expect(within(panel).getByText('1 chỉ số · 4 báo cáo · 3 agent')).toBeInTheDocument();
  });
});

describe('UploadPage', () => {
  it('reads a spreadsheet, lets the columns be edited, and creates the table', async () => {
    renderPage(<UploadPage />);
    const file = new File(['x'], 'chi_tieu_chi_nhanh_Q4.xlsx');
    fireEvent.change(screen.getByLabelText('Chọn tệp CSV hoặc Excel'), {
      target: { files: [file] },
    });
    expect(
      await screen.findByRole('heading', { name: 'chi_tieu_chi_nhanh_Q4.xlsx' }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Agent nhận ra dòng tiêu đề ở dòng 3 \(dòng 1–2 là tiêu đề báo cáo\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('upload.chi_tieu_chi_nhanh (mới)')).toBeInTheDocument();
    expect(screen.getByText(/01\/10\/2026 · 3 dòng sai/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Tên cột cho “Mã CN”'), { target: { value: 'Ma CN' } });
    expect(screen.getByLabelText('Tên cột cho “Mã CN”')).toHaveValue('ma_cn');
    fireEvent.click(screen.getByRole('button', { name: 'Tạo bảng' }));
    await waitFor(() => expect(app.navigate).toHaveBeenCalledWith('data', 'JOBS'));
  });
});
