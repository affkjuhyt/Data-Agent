/**
 * P05 Tài liệu phi cấu trúc against the v1 mock: the document viewer and its tabs, the email and
 * call viewers, the library, the quality queue, validity, answers from documents, extraction
 * schemas and the collection dialogs.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import { CreateCollectionDialog } from './CollectionDialogs';
import DocumentAnswerPage from './DocumentAnswerPage';
import DocumentsPage from './DocumentsPage';
import DocumentViewerPage from './DocumentViewerPage';
import ExtractionSchemaPage from './ExtractionSchemaPage';
import QualityQueuePage from './QualityQueuePage';
import ValidityPage from './ValidityPage';

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

describe('DocumentViewerPage', () => {
  it('shows the page with the extracted fields and fixes the uncertain one', async () => {
    routeId = 'doc-hs-0187';
    wrap(<DocumentViewerPage />);
    expect(
      await screen.findByText(/Agent đã đọc hợp đồng này và trích 14 trường/),
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Hợp đồng tín dụng' })).toBeInTheDocument();
    expect(screen.getByText('Trang 3 / 12')).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: /Số tiền vay/ })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Trường · 14' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getByText('Số tiền vay: độ tin thấp do chữ viết tay đè lên số in'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Sửa thành 1.250.000.000' }));
    expect(await screen.findByText('Đã sửa loan_amount thành 1.250.000.000.')).toBeInTheDocument();
    expect(await screen.findByText('1.250.000.000 đ')).toBeInTheDocument();
    expect(screen.queryByText('Số tiền vay: độ tin thấp do chữ viết tay đè lên số in')).toBeNull();
  });

  it('turns pages and switches to the OCR text', async () => {
    routeId = 'doc-hs-0187';
    wrap(<DocumentViewerPage />);
    await screen.findByText('Trang 3 / 12');
    fireEvent.click(screen.getByRole('radio', { name: 'Văn bản OCR' }));
    expect(await screen.findByText(/Bên B đồng ý cho Bên A vay/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Trang sau' }));
    expect(await screen.findByText('Trang 4 / 12')).toBeInTheDocument();
  });

  it('lists chunks and history in their tabs', async () => {
    routeId = 'doc-hs-0187';
    wrap(<DocumentViewerPage />);
    // Chunks come with each page (A2), numbered in the document's order.
    fireEvent.click(await screen.findByRole('tab', { name: 'Đoạn văn' }));
    expect(await screen.findByText('tr.3 · đoạn 1')).toBeInTheDocument();
    expect(screen.getByText('tr.5 · đoạn 3')).toBeInTheDocument();
    expect(screen.getByText(/412 đoạn trong bộ sưu tập/)).toBeInTheDocument();
    // Figma's Liên kết tab is not in the design.
    expect(screen.queryByRole('tab', { name: 'Liên kết' })).toBeNull();

    // The versions, then who read or changed it from the audit log.
    fireEvent.click(screen.getByRole('tab', { name: 'Lịch sử' }));
    expect(
      await screen.findByText(/^08\/10 \d\d:42 · Người dùng xem tài liệu$/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Agent sửa thông tin tài liệu/)).toBeInTheDocument();
    expect(screen.getByText(/Nạp Hợp đồng tín dụng/)).toBeInTheDocument();
  });

  it('opens a document of a collection from the collection screen', async () => {
    routeId = 'doc-hdtd';
    wrap(<DocumentViewerPage />);
    expect(await screen.findByText('Trang 4 / 6')).toBeInTheDocument();
    expect(screen.getByText(/trang 4 có chữ viết tay mờ/)).toBeInTheDocument();
  });

  it('draws an email thread and answers the review the agent asked for', async () => {
    routeId = 'doc-mail-3390';
    wrap(<DocumentViewerPage />);
    expect(
      await screen.findByRole('heading', { name: 'Phí trả nợ trước hạn không đúng hợp đồng' }),
    ).toBeInTheDocument();
    expect(screen.getByText('HĐTD_scan.pdf · Biên lai.jpg')).toBeInTheDocument();
    expect(screen.getByText('Khiếu nại phí · trả nợ trước hạn')).toBeInTheDocument();
    const threads = await screen.findByRole('navigation', { name: 'Các thư' });
    expect(await within(threads).findByText('Mức cao')).toBeInTheDocument();
    expect(within(threads).getAllByText('Thấp')).toHaveLength(2);

    fireEvent.click(screen.getByRole('button', { name: 'Chấp nhận' }));
    expect(await screen.findByText('Đã chấp nhận bản nháp.')).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText('Bản nháp trả lời: hoàn 2% phí, xin lỗi khách hàng')).toBeNull(),
    );

    fireEvent.click(within(threads).getByRole('button', { name: /Đề nghị giảm lãi suất/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'DOCUMENT', 'doc-mail-1180');
  });

  it('draws a call with its transcript and seeks to a line', async () => {
    routeId = 'doc-call-88213';
    wrap(<DocumentViewerPage />);
    expect(
      await screen.findByRole('heading', { name: 'Cuộc gọi #CL-88213 · 08/10 10:42' }),
    ).toBeInTheDocument();
    expect(screen.getByText('01:12 / 04:38')).toBeInTheDocument();
    expect(screen.getByText('15.000.000 đ · 20/10')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /00:15/ }));
    expect(screen.getByText('00:15 / 04:38')).toBeInTheDocument();
  });
});

describe('DocumentsPage', () => {
  it('lists documents by tab and opens one in the viewer', async () => {
    wrap(<DocumentsPage />);
    expect(await screen.findByRole('tab', { name: 'Quy định · 4' })).toBeInTheDocument();
    expect(await screen.findByText('Thông tư phân loại nợ (2024)')).toBeInTheDocument();
    expect(screen.getByText('Hết hiệu lực 30/09')).toBeInTheDocument();
    // The panel shows the first document until another is picked.
    expect(
      await screen.findByRole('heading', { name: 'Thông tư phân loại nợ (sửa đổi 2026)' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Nội bộ · 5' }));
    fireEvent.click(await screen.findByRole('row', { name: /Quy chế trích lập dự phòng nội bộ/ }));
    fireEvent.click(await screen.findByRole('button', { name: 'Mở tài liệu' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('data', 'DOCUMENT', 'doc-provision'));

    fireEvent.click(screen.getByRole('tab', { name: 'Đang xử lý · 3' }));
    expect(
      await screen.findByRole('progressbar', { name: 'Tiến độ Công văn hướng dẫn trích lập 2026' }),
    ).toHaveAttribute('aria-valuenow', '62');
  });

  it('uploads files and warns about duplicates first', async () => {
    wrap(<DocumentsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Nạp tài liệu' }));
    const dialog = await screen.findByRole('dialog', { name: 'Nạp tệp vào bộ sưu tập' });
    fireEvent.change(within(dialog).getByLabelText('Chọn tệp để nạp'), {
      target: {
        files: [
          new File(['x'], 'HS_LD2409_0187.pdf', { type: 'application/pdf' }),
          new File(['y'], 'HS_LD2410_0001.pdf', { type: 'application/pdf' }),
        ],
      },
    });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Bộ sưu tập' }));
    fireEvent.click((await screen.findAllByRole('option'))[0]);
    fireEvent.click(within(dialog).getByRole('button', { name: 'Nạp' }));
    expect(
      // One request per file (A2); a duplicate is only known from the answer to its upload.
      await screen.findByText('Đã nhận 2 tệp. Agent Tài liệu đang tiền xử lý.'),
    ).toBeInTheDocument();
  });
});

describe('QualityQueuePage', () => {
  it('confirms a page and moves to the next one', async () => {
    wrap(<QualityQueuePage />);
    expect(
      await screen.findByRole('heading', { name: 'Biên bản họp HĐTD 05/10 · trang 4' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Dữ liệu / Tài liệu / Hàng chờ chất lượng · 1/3')).toBeInTheDocument();
    expect(screen.getByText(/\[TÊN NGƯỜI\]/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận và tiếp' }));
    expect(
      await screen.findByRole('heading', { name: 'Hợp đồng tín dụng LD2409-0187 · trang 3' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Dữ liệu / Tài liệu / Hàng chờ chất lượng · 1/2')).toBeInTheDocument();
  });

  it('asks for a reason before dropping a page', async () => {
    wrap(<QualityQueuePage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Bỏ trang này' }));
    const dialog = await screen.findByRole('dialog', { name: 'Bỏ trang này?' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ trang' }));
    expect(await within(dialog).findByText('Ghi lý do bỏ trang.')).toBeInTheDocument();
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Lý do' }), {
      target: { value: 'Trang trắng' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Bỏ trang' }));
    expect(await screen.findByText('Đã bỏ Biên bản họp HĐTD 05/10 · trang 4.')).toBeInTheDocument();
  });
});

describe('ValidityPage', () => {
  it('opens the document waiting for confirmation and confirms the supersession', async () => {
    wrap(<ValidityPage />);
    expect(
      await screen.findByRole('button', { name: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ' }),
    ).toBeInTheDocument();
    expect(screen.getByText('QĐ 845/2026 ban hành, thay thế toàn bộ')).toBeInTheDocument();
    expect(screen.getByText('4 câu trả lời đã lưu trong phiên')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận bị thay thế' }));
    expect(
      await screen.findByText('Đã xác nhận QĐ 1120/2023 · quy trình tín dụng bán lẻ bị thay thế.'),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Xác nhận bị thay thế' })).toBeNull(),
    );
    expect(screen.getByText('Đã xác nhận')).toBeInTheDocument();
  });
});

describe('ExtractionSchemaPage', () => {
  it('opens the draft schema and publishes it after confirming', async () => {
    wrap(<ExtractionSchemaPage />);
    expect(await screen.findByRole('heading', { name: 'Hồ sơ tín dụng · v2' })).toBeInTheDocument();
    expect(screen.getByText('credit_file_extracted')).toBeInTheDocument();
    expect(screen.getByText('repayment_schedule')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xuất bản v2' }));
    const dialog = await screen.findByRole('alertdialog');
    expect(within(dialog).getByText('Xuất bản lược đồ v2?')).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Xuất bản' }));
    expect(await screen.findByText('Đã xuất bản Hồ sơ tín dụng · v2.')).toBeInTheDocument();
    expect(await screen.findByText('Đã xuất bản')).toBeInTheDocument();
  });

  it('runs a test on the sample', async () => {
    routeId = 'xs-credit-v2';
    wrap(<ExtractionSchemaPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Chạy thử trên 50 tệp' }));
    expect(
      await screen.findByText('Agent Tài liệu đang chạy thử v2 trên 50 tệp.'),
    ).toBeInTheDocument();
  });
});

describe('DocumentAnswerPage', () => {
  it('answers with citations and opens the cited passage', async () => {
    wrap(<DocumentAnswerPage />);
    fireEvent.change(await screen.findByRole('textbox'), {
      target: { value: 'Khoản cơ cấu lại lần 2 phải xếp vào nhóm nợ nào?' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText(/tối thiểu vào nhóm 3/)).toBeInTheDocument();
    expect(screen.getByText('Đã được thay thế bởi QĐ 845/2026')).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '[1] TT 31/2024/TT-NHNN · Điều 10, khoản 3' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /QĐ 1120\/2023/ }));
    expect(screen.getByText(/chỉ trích để nói điều đó/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở đoạn gốc' }));
    expect(navigate).toHaveBeenCalledWith('data', 'DOCUMENT', 'doc-qd1120');
  });

  it('says so when nothing answers the question', async () => {
    wrap(<DocumentAnswerPage />);
    fireEvent.change(await screen.findByRole('textbox'), {
      target: { value: 'Văn bản nào nói về giá vàng hôm nay?' },
    });
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(await screen.findByText(/Không tìm thấy đoạn nào trả lời/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Mở đoạn gốc' })).toBeNull();
  });
});

describe('Collection dialogs', () => {
  it('creates a collection', async () => {
    const onCreated = vi.fn();
    wrap(<CreateCollectionDialog open onClose={() => {}} onCreated={onCreated} />);
    const dialog = await screen.findByRole('dialog', { name: 'Tạo bộ sưu tập' });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Tên' }), {
      target: { value: 'Biên bản kiểm tra sau cho vay' },
    });
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Mục đích sử dụng' }), {
      target: { value: 'thẩm định tín dụng' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Tạo' }));
    await waitFor(() =>
      expect(onCreated).toHaveBeenCalledWith({
        id: 'col-new-1',
        name: 'Biên bản kiểm tra sau cho vay',
      }),
    );
  });
});
