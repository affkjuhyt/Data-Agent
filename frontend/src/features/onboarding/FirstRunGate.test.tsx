import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { createHandlers } from '../../mocks/handlers';
import { FirstRunGate } from './FirstRunGate';

const app = vi.hoisted(() => ({ navigate: vi.fn(), user: { name: 'Đỗ Hải Yến' } }));
vi.mock('../../AppStateContext', () => ({ useAppState: () => app }));
vi.mock('../../lib/AuthProvider', () => ({ useAuth: () => ({ logout: vi.fn() }) }));

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
beforeEach(() => {
  sessionStorage.clear();
  server.use(...createHandlers());
});
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const renderGate = () =>
  render(
    <ToastProvider>
      <FirstRunGate>{(spaceId) => <p>app:{spaceId || 'org'}</p>}</FirstRunGate>
    </ToastProvider>,
  );

describe('FirstRunGate', () => {
  it('asks for a Data Space when the viewer did not choose to reopen the last one', async () => {
    server.use(
      http.get('*/v1/me/data-spaces', () =>
        HttpResponse.json({ items: [{ id: 'ds-1', name: 'Rủi ro tín dụng', access: 'member', last_used_text: 'hôm nay' }], open_last_used: false }),
      ),
      http.get('*/v1/me/onboarding', () => HttpResponse.json({ state: 'done', steps: [], context_text: '', templates: [] })),
    );
    renderGate();
    expect(await screen.findByRole('button', { name: 'Mở Rủi ro tín dụng' })).toBeInTheDocument();
    expect(screen.queryByText(/^app:/)).toBeNull();
  });

  it('opens the last space straight away when asked to, and the app when onboarding is done', async () => {
    server.use(
      http.get('*/v1/me/data-spaces', () =>
        HttpResponse.json({ items: [{ id: 'ds-1', name: 'Rủi ro tín dụng', access: 'member', last_used_text: 'hôm nay' }], open_last_used: true }),
      ),
      http.get('*/v1/me/onboarding', () => HttpResponse.json({ state: 'done', steps: [], context_text: '', templates: [] })),
    );
    renderGate();
    expect(await screen.findByText('app:ds-1')).toBeInTheDocument();
  });

  it('never locks the app behind setup screens the backend cannot serve', async () => {
    server.use(
      http.get('*/v1/me/data-spaces', () => new HttpResponse(null, { status: 404 })),
      http.get('*/v1/me/onboarding', () => new HttpResponse(null, { status: 404 })),
    );
    renderGate();
    expect(await screen.findByText('app:org')).toBeInTheDocument();
  });
});
