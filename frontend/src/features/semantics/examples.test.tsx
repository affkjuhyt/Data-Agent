/**
 * Câu hỏi mẫu & đồng nghĩa against the v1 mock: the pending suggestion opens first, it is marked
 * right or its SQL corrected, and a new example is refused until its SQL runs.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createExampleHandlers } from '../../mocks/examples';
import ExamplesPage from './ExamplesPage';

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ navigate: vi.fn(), tenant: { tenantName: 'Rủi ro tín dụng' } }),
}));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createExampleHandlers()));
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <ExamplesPage />
    </ToastProvider>,
  );

describe('ExamplesPage', () => {
  it('lists examples and synonyms, opening the first suggestion waiting for review', async () => {
    page();
    expect(
      await screen.findByText(/4 câu mẫu · 3 cách gọi khác trong Rủi ro tín dụng/),
    ).toBeInTheDocument();
    expect(screen.getByText('“nợ xấu”, “NPL”, “tỷ lệ nợ xấu”')).toBeInTheDocument();
    const detail = screen.getByRole('complementary', { name: 'Câu hỏi mẫu đang xem' });
    expect(
      within(detail).getByText(/hỏi 7 lần trong 30 ngày và 2 lần bị báo sai/),
    ).toBeInTheDocument();
    expect(within(detail).getByText(/branch_region = 'Hải Phòng'/)).toBeInTheDocument();
  });

  it('marks the suggestion right, which moves the next one into view', async () => {
    page();
    const detail = await screen.findByRole('complementary', { name: 'Câu hỏi mẫu đang xem' });
    fireEvent.click(within(detail).getByRole('button', { name: 'Đánh dấu đúng' }));
    expect(await screen.findByText('Đã đánh dấu đúng')).toBeInTheDocument();
    // The panel is keyed by the example, so it is a new element once the selection moves on.
    expect(await screen.findByRole('heading', { name: 'Nợ xấu nhóm 2–5' })).toBeInTheDocument();
  });

  it('refuses a new example whose SQL does not run', async () => {
    page();
    fireEvent.click(await screen.findByRole('button', { name: 'Thêm câu hỏi mẫu' }));
    const dialog = screen.getByRole('alertdialog', { name: 'Thêm câu hỏi mẫu' });
    fireEvent.change(within(dialog).getByLabelText('Câu hỏi'), {
      target: { value: 'Dư nợ theo chi nhánh' },
    });
    fireEvent.change(within(dialog).getByLabelText('SQL trả lời đúng'), {
      target: { value: 'DROP TABLE x' },
    });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Thêm' }));
    expect(await within(dialog).findByText(/SQL không chạy được/)).toBeInTheDocument();
  });
});
