/**
 * Trang tri thức against the v1 mock: the index and its filters, the page with system-filled
 * figures and its claims, the version tab, and recompiling held back until a composer exists.
 */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { setDataSpace } from '../../lib/v1/client';
import { recompileKnowledgePage } from '../../lib/v1/knowledge';
import { createHandlers } from '../../mocks/handlers';
import KnowledgePageView from './KnowledgePageView';

const navigate = vi.fn();
let routeId = '';
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate, routeId }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  routeId = '';
  server.resetHandlers(...createHandlers());
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const page = () =>
  render(
    <ToastProvider>
      <KnowledgePageView />
    </ToastProvider>,
  );

describe('KnowledgePageView', () => {
  it('opens the first page, with filled figures and claims', async () => {
    page();
    expect(
      await screen.findByRole('heading', { level: 1, name: /Ngân hàng B/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        /v5 chứng nhận 08\/10\/2026 bởi Lê Thu Hà · 3 khẳng định còn hiệu lực · 1 hết hiệu lực/,
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '4,90%/năm' })).toHaveClass('text-info');
    expect(
      screen.getByRole('heading', { name: 'Khẳng định · 3 còn hiệu lực' }),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('row', { name: /Sáp nhập/ })).getByText('Hết hạn'),
    ).toBeInTheDocument();
  });

  it('filters the index by kind and search, and opens another page', async () => {
    page();
    const index = await screen.findByRole('navigation', { name: 'Trang tri thức' });
    await within(index).findByRole('button', { name: /Tỷ lệ CASA/ });
    fireEvent.click(within(index).getByRole('radio', { name: 'Chỉ số 2' }));
    expect(within(index).queryByRole('button', { name: /Ngân hàng K/ })).toBeNull();
    fireEvent.change(within(index).getByLabelText('Tìm trang'), { target: { value: 'casa' } });
    fireEvent.click(within(index).getByRole('button', { name: /Tỷ lệ CASA/ }));
    expect(navigate).toHaveBeenCalledWith('data', 'KNOWLEDGE', 'kp-casa');
  });

  it('lists versions and offers no recompile while the api has no composer', async () => {
    setDataSpace('ds-thi-truong');
    page();
    fireEvent.click(await screen.findByRole('tab', { name: /Phiên bản/ }));
    expect(await screen.findByText(/Biểu phí tháng 10/)).toBeInTheDocument();
    // POST /v1/knowledge-runs answers 501 until a page composer exists (PL-03).
    expect(screen.getByRole('button', { name: 'Biên soạn lại' })).toHaveAttribute('aria-disabled', 'true');
    setDataSpace(null);
  });

  it('asks for a Data Space before a knowledge run from the whole organisation', () => {
    // A run belongs to a Data Space (KnowledgeRunCreate.data_space_id), kept for the composer.
    setDataSpace(null);
    expect(() => recompileKnowledgePage('kp-casa')).toThrow(/Chọn một Data Space trước/);
  });
});
