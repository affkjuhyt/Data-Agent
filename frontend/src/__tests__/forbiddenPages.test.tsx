/**
 * Detail pages opened by id show the designed "Thiếu quyền" state (features/access) when the
 * server refuses the read with an access block, and the plain load error otherwise.
 */
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import type { ComponentType } from 'react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../design-system/Toast';
import DocumentViewerPage from '../features/documents/DocumentViewerPage';
import ForecastPage from '../features/forecast/ForecastPage';
import SourceDetailPage from '../features/data-sources/SourceDetailPage';
import NotebookHost from '../features/sessions/SessionsPage';
import { createHandlers } from '../mocks/handlers';

const app = vi.hoisted(() => ({
  navigate: vi.fn(),
  routeId: 'locked-1' as string | undefined,
  user: { name: 'Đỗ Hải Yến' },
  tenant: { tenantId: null },
}));
vi.mock('../AppStateContext', () => ({ useAppState: () => app }));

const forbidden = () =>
  HttpResponse.json(
    {
      title: 'Forbidden',
      status: 403,
      code: 'forbidden',
      detail: 'Bạn chưa có quyền với Data Space Kế toán.',
      access: {
        title: 'Bạn chưa có quyền với dữ liệu của Kế toán',
        detail: 'Tài nguyên này thuộc Data Space Kế toán. Chủ sở hữu: Trần Quốc An.',
        data_space_id: 'ds-accounting',
        owner_name: 'Trần Quốc An',
        options: [{ capability: 'view_aggregate', label: 'Xem tổng hợp' }],
        reason_hint: 'Mở từ đường dẫn được chia sẻ',
        fallback_text: null,
      },
    },
    { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
  );

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
beforeEach(() => server.use(...createHandlers()));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const cases: [string, ComponentType, string][] = [
  ['document viewer', DocumentViewerPage, '*/v1/documents/locked-1'],
  ['forecast', ForecastPage, '*/v1/forecasts/locked-1'],
  // A2 has no GET /v1/sources/{id}: the page reads the list and the source's datasets.
  ['source', SourceDetailPage, '*/v1/datasets'],
  ['session notebook', NotebookHost, '*/v1/workspaces/locked-1'],
];

describe('detail pages refused by the server', () => {
  it.each(cases)('the %s page shows Thiếu quyền with the owner and a request form', async (_n, Page, path) => {
    server.use(http.get(path, forbidden));
    render(
      <ToastProvider>
        <Page />
      </ToastProvider>,
    );
    expect(
      await screen.findByRole('heading', { name: 'Bạn chưa có quyền với dữ liệu của Kế toán' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Chủ sở hữu: Trần Quốc An/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Gửi yêu cầu' })).toBeInTheDocument();
  });

  it('keeps the plain load error for a 403 without an access block', async () => {
    server.use(
      http.get('*/v1/datasets', () =>
        HttpResponse.json(
          { title: 'Forbidden', status: 403, code: 'forbidden', detail: 'Không được phép.' },
          { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    );
    render(
      <ToastProvider>
        <SourceDetailPage />
      </ToastProvider>,
    );
    expect(await screen.findByText(/Không được phép/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Gửi yêu cầu' })).toBeNull();
  });
});
