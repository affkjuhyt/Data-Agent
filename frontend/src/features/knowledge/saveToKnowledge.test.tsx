/**
 * Lưu câu trả lời thành tri thức against the v1 mock: the answer with the page the knowledge
 * index suggests, sending goes to review, and BR-167 comes back as an error in the dialog.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import { SaveToKnowledgeDialog } from './SaveToKnowledgeDialog';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => server.resetHandlers(...createHandlers()));
afterAll(() => server.close());

const dialog = (answerId: string, onClose = vi.fn()) => {
  render(
    <ToastProvider>
      <SaveToKnowledgeDialog answerId={answerId} onClose={onClose} />
    </ToastProvider>,
  );
  return onClose;
};

describe('SaveToKnowledgeDialog', () => {
  it('shows the answer with the page the index suggests, and sends it for review', async () => {
    const onClose = dialog('answer-npl-t9');
    const box = screen.getByRole('alertdialog', { name: 'Lưu thành tri thức' });
    expect(await within(box).findByText(/tăng 0,12 điểm so với T8/)).toBeInTheDocument();
    expect(within(box).getByRole('button', { name: 'Trang đích' })).toHaveTextContent(
      'Nợ xấu bán lẻ (chỉ số NPL_RATIO)',
    );
    fireEvent.click(within(box).getByRole('button', { name: 'Gửi đề xuất' }));
    expect(await screen.findByText(/Đã gửi đề xuất/)).toBeInTheDocument();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('shows BR-167 when the answer holds a figure with no query run', async () => {
    dialog('answer-typed-number');
    const box = screen.getByRole('alertdialog', { name: 'Lưu thành tri thức' });
    fireEvent.click(await within(box).findByRole('button', { name: 'Trang đích' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Ngân hàng B' }));
    fireEvent.click(within(box).getByRole('button', { name: 'Gửi đề xuất' }));
    expect(await within(box).findByText(/BR-167/)).toBeInTheDocument();
  });
});
