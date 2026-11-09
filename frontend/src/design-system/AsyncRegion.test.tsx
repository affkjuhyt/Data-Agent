import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { V1Error, type ForbiddenAccess } from '../lib/v1/client';
import { createHandlers } from '../mocks/handlers';
import { AsyncRegion } from './AsyncRegion';
import { ToastProvider } from './Toast';

vi.mock('../AppStateContext', () => ({
  useAppState: () => ({ navigate: vi.fn(), routeId: undefined, user: { name: 'Test' } }),
}));

const access: ForbiddenAccess = {
  title: 'Bạn chưa có quyền với dữ liệu của Kế toán',
  detail: 'Tài nguyên này thuộc Data Space Kế toán.',
  data_space_id: 'ds-accounting',
  owner_name: 'Trần Quốc An',
  options: [{ capability: 'view_aggregate', label: 'Xem tổng hợp' }],
  reason_hint: 'Mở từ đường dẫn được chia sẻ',
  fallback_text: null,
};

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('AsyncRegion', () => {
  it('shows Thiếu quyền when the load error carries an access block', async () => {
    server.use(
      http.post('*/v1/access-requests', () => HttpResponse.json({ id: 'req-1', code: 'AR-1' })),
    );
    const error = new V1Error(403, 'forbidden', 'Forbidden', undefined, access);
    render(
      <ToastProvider>
        <AsyncRegion
          state={{ data: undefined, error, loading: false, reload: () => undefined }}
          what="nguồn"
        >
          {() => null}
        </AsyncRegion>
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Bạn chưa có quyền với dữ liệu của Kế toán' }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Không tải được nguồn/)).not.toBeInTheDocument();
  });

  it('shows Thiếu quyền for a 403 without an access block, naming the missing capability', () => {
    const error = new V1Error(403, 'forbidden', 'Missing capability: compliance.');
    render(
      <ToastProvider>
        <AsyncRegion
          state={{ data: undefined, error, loading: false, reload: () => undefined }}
          what="yêu cầu của chủ thể dữ liệu"
        >
          {() => null}
        </AsyncRegion>
      </ToastProvider>,
    );
    expect(
      screen.getByRole('heading', { name: 'Bạn chưa có quyền xem yêu cầu của chủ thể dữ liệu' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Cần quyền Tuân thủ \(compliance\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Không tải được/)).not.toBeInTheDocument();
    // An org-wide role is granted by an admin, not asked for in a Data Space.
    expect(screen.queryByRole('button', { name: 'Gửi yêu cầu' })).not.toBeInTheDocument();
  });

  it('keeps the load banner for a plain error', () => {
    render(
      <ToastProvider>
        <AsyncRegion
          state={{
            data: undefined,
            error: new Error('mạng chậm'),
            loading: false,
            reload: () => undefined,
          }}
          what="nguồn"
        >
          {() => null}
        </AsyncRegion>
      </ToastProvider>,
    );
    expect(screen.getByText(/Không tải được nguồn/)).toBeInTheDocument();
    expect(screen.getByText(/mạng chậm/)).toBeInTheDocument();
  });
});
