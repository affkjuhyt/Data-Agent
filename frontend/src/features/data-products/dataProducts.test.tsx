/**
 * P06 second half against the v1 mock: the product catalog with its filters and drawer tabs, a
 * mart published into a product (a breaking change goes to approval) and its agent suggestion,
 * and the quality rules with a new rule.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import DataMartPage from './DataMartPage';
import DataProductsPage from './DataProductsPage';
import QualityRulesPage from './QualityRulesPage';

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

describe('DataProductsPage', () => {
  it('lists the products, filters them, and opens the first in the drawer', async () => {
    wrap(<DataProductsPage />);
    expect(await screen.findByRole('tab', { name: 'Của tôi (6)' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'Đang được dùng (5)' })).toBeInTheDocument();
    expect(screen.getByText('Thay đổi phá vỡ')).toBeInTheDocument();
    expect(screen.getByText('sales_margin · v2.3 → v3')).toBeInTheDocument();

    const drawer = screen.getByRole('complementary', { name: 'Chi tiết sản phẩm dữ liệu' });
    expect(await within(drawer).findByText('cost_price')).toBeInTheDocument();
    expect(within(drawer).getByText('sales_margin · v2.3 → v3 (nháp)')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: 'Chờ xuất bản (1)' }));
    expect(screen.getByText('Dự báo dòng tiền 13 tuần')).toBeInTheDocument();
    expect(screen.queryByText('Doanh thu thuần theo ngày')).not.toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('button', { name: 'Đóng' }));
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
  });

  it('walks the drawer tabs and drafts the next version', async () => {
    wrap(<DataProductsPage />);
    const drawer = await screen.findByRole('complementary');
    fireEvent.click(await within(drawer).findByRole('button', { name: 'Xem ảnh hưởng' }));
    expect(within(drawer).getByText('Kế hoạch tài chính')).toBeInTheDocument();
    expect(
      within(drawer).getByText('12 Data Space · 3 sẽ bị ảnh hưởng nếu phát hành v3'),
    ).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('tab', { name: 'Phiên bản' }));
    expect(within(drawer).getByText('Còn hỗ trợ')).toBeInTheDocument();
    fireEvent.click(within(drawer).getByRole('tab', { name: 'Chất lượng' }));
    expect(
      within(drawer).getByText('Độ mới: cập nhật trước 07:00 · 30/30 ngày'),
    ).toBeInTheDocument();

    fireEvent.click(within(drawer).getByRole('tab', { name: 'Contract' }));
    fireEvent.click(within(drawer).getByRole('button', { name: 'Tạo bản v3 nháp' }));
    expect(await screen.findByText('Đã tạo bản nháp · mở để chỉnh sửa')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        within(drawer).queryByRole('button', { name: 'Tạo bản v3 nháp' }),
      ).not.toBeInTheDocument(),
    );
  });
});

describe('ImportCatalogDialog', () => {
  it('requests use of products from another Data Space and flags PII', async () => {
    wrap(<DataProductsPage />);
    fireEvent.click(await screen.findByRole('button', { name: 'Nhập từ catalog' }));
    const dialog = screen.getByRole('dialog', { name: 'Nhập sản phẩm từ catalog' });
    expect(
      within(dialog).getByText('Dùng sản phẩm dữ liệu của Data Space khác; quyền theo chủ sở hữu.'),
    ).toBeInTheDocument();
    const submit = within(dialog).getByRole('button', { name: 'Yêu cầu dùng' });
    expect(submit).toHaveAttribute('aria-disabled', 'true');

    fireEvent.click(
      await within(dialog).findByRole('checkbox', { name: 'net_revenue · Kinh doanh bán lẻ · v4.1' }),
    );
    expect(within(dialog).queryByText(/chứa PII/)).not.toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole('checkbox', { name: 'customer_360 · CRM · v2.0' }));
    expect(
      within(dialog).getByText('customer_360 chứa PII; quyền đọc cần chủ sở hữu CRM duyệt.'),
    ).toBeInTheDocument();

    // The target defaults to the first Data Space; pick it explicitly by role, whatever its name.
    const target = within(dialog).getByRole('combobox', { name: 'Dùng cho' });
    await waitFor(() => expect(target).not.toBeDisabled());
    fireEvent.click(target);
    fireEvent.click(within(dialog).getAllByRole('option')[0]);

    fireEvent.click(submit);
    expect(await screen.findByText('Đã gửi 2 yêu cầu dùng cho chủ sở hữu')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('DataMartPage', () => {
  it('draws the first mart and sends a breaking change for approval', async () => {
    wrap(<DataMartPage />);
    expect(await screen.findByRole('heading', { name: 'mart.credit_risk' })).toBeInTheDocument();
    expect(screen.getByText('fact_loan_daily')).toBeInTheDocument();
    expect(screen.getByText('2.0 · thay đổi phá vỡ: bỏ cột branch_name')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Phát hành sản phẩm dữ liệu' }));
    expect(
      await screen.findByText('Đã gửi chứng nhận credit_risk_retail v2.0 · chờ chủ sở hữu'),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra' }));
    expect(navigate).toHaveBeenCalledWith('data', 'QUALITY');
  });

  it('applies the agent suggestion from the dashed dimension', async () => {
    wrap(<DataMartPage />);
    fireEvent.click(await screen.findByRole('button', { name: /dim_officer: chiều agent gợi ý/ }));
    const dialog = screen.getByRole('dialog', { name: 'Agent đề xuất data mart' });
    expect(within(dialog).getByLabelText('provision_rate · dùng trong COST_OF_RISK')).toBeChecked();
    fireEvent.click(within(dialog).getByRole('button', { name: 'Áp dụng' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(await screen.findByText('cán bộ tín dụng')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /dim_officer/ })).not.toBeInTheDocument();
  });
});

describe('QualityRulesPage', () => {
  it('lists the rules with their current result and adds one', async () => {
    wrap(<QualityRulesPage />);
    expect(
      await screen.findByText(
        '5 quy tắc · chạy sau mỗi lần nạp và mỗi lần pipeline chạy · 2 đang vi phạm',
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('Vi phạm · 0,12%')).toBeInTheDocument();
    expect(
      screen.getByRole('img', { name: '7 ngày: 5 đạt, 0 cảnh báo, 2 vi phạm' }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Thêm quy tắc' }));
    const dialog = screen.getByRole('dialog');
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Bảng' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Thu hồi nợ' }));
    fireEvent.change(within(dialog).getByLabelText('Cột'), { target: { value: 'rate' } });
    fireEvent.click(within(dialog).getByRole('combobox', { name: 'Kiểm tra' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Trong khoảng' }));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm quy tắc' }));
    expect(
      await within(dialog).findByText('Nhập giá trị nhỏ nhất và lớn nhất.'),
    ).toBeInTheDocument();
    fireEvent.change(within(dialog).getByLabelText('Nhỏ nhất'), { target: { value: '0' } });
    fireEvent.change(within(dialog).getByLabelText('Lớn nhất'), { target: { value: '25' } });
    fireEvent.click(within(dialog).getByLabelText('Cảnh báo'));
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm quy tắc' }));
    expect(
      await screen.findByText('Đã tạo “Trong khoảng · Thu hồi nợ.rate” · bạn là chủ sở hữu'),
    ).toBeInTheDocument();
    expect(await screen.findByText('Thu hồi nợ.rate')).toBeInTheDocument();
  });
});
