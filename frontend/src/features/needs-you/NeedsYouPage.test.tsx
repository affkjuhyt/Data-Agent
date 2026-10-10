/**
 * Cần bạn end to end against the v1 mock: the cards, choosing with a 5 s undo, and the empty state.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import NeedsYouPage from './NeedsYouPage';

vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate: vi.fn() }) }));

const resolved: string[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resolved.length = 0;
  vi.useRealTimers();
});
afterAll(() => server.close());

function useMockWithSpy() {
  server.use(
    http.post('*/v1/hitl-requests/:action', ({ params }) => {
      resolved.push(String(params.action));
      return undefined; // fall through to the stateful mock
    }),
    ...createHandlers(),
  );
}

const renderPage = () =>
  render(
    <ToastProvider>
      <NeedsYouPage />
    </ToastProvider>,
  );

describe('NeedsYouPage', () => {
  it('shows every open request with its question, reason and agent', async () => {
    useMockWithSpy();
    renderPage();
    expect(
      await screen.findByRole('heading', { name: '“Nợ xấu” nên tính theo định nghĩa nào?' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Vì sao agent hỏi: hai định nghĩa hợp lệ/)).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Nhóm 3–5 · NPL_RATIO v4/ })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getAllByRole('article')).toHaveLength(5);
    expect(screen.getByRole('radio', { name: 'Đang chờ · 5' })).toBeInTheDocument();
  });

  it('sends the choice after the 5 s undo window, and Hoàn tác keeps the card', async () => {
    useMockWithSpy();
    renderPage();
    await screen.findAllByRole('article');
    vi.useFakeTimers({ shouldAdvanceTime: true });

    fireEvent.click(screen.getByRole('radio', { name: /Nhóm 2–5 · nội bộ/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận lựa chọn' }));
    expect(
      screen.queryByRole('heading', { name: '“Nợ xấu” nên tính theo định nghĩa nào?' }),
    ).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hoàn tác' }));
    expect(
      screen.getByRole('heading', { name: '“Nợ xấu” nên tính theo định nghĩa nào?' }),
    ).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(6000));
    expect(resolved).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận lựa chọn' }));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    await waitFor(() => expect(resolved).toEqual(['hitl-npl-definition:resolve']));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(4));
  });

  it('says nothing waits when the inbox is empty', async () => {
    server.use(http.get('*/v1/*', () => HttpResponse.json({ items: [], next_page_token: '' })));
    renderPage();
    expect(
      await screen.findByRole('heading', { name: 'Không có việc nào cần bạn' }),
    ).toBeInTheDocument();
  });

  it('keeps the page usable when the inbox fails to load', async () => {
    server.use(
      http.get('*/v1/inbox', () =>
        HttpResponse.json({ code: 'unavailable', detail: 'Dịch vụ tạm dừng.' }, { status: 503 }),
      ),
      ...createHandlers(),
    );
    renderPage();
    expect(await screen.findByText(/Không tải được việc cần bạn/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Thử lại' })).toBeInTheDocument();
    expect(await screen.findByText('Agent hỏi bạn khi nào?')).toBeInTheDocument();
  });
});
