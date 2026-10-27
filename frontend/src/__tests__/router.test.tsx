/**
 * The router (router.tsx) under useAppState(): URLs are /<area>/<page>/<id> with the page
 * lowercased, links of earlier IAs and of the hash era land on their v3.1 page, and navigate()
 * is a history entry that Back undoes.
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { createBrowserHistory, createMemoryHistory, RouterProvider } from '@tanstack/react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AppStateProvider, useAppState } from '../AppStateContext';
import { SideRail } from '../components/shell/SideRail';
import { createAppRouter } from '../router';

vi.mock('../lib/AuthProvider', () => ({ useAuth: () => ({ user: null, me: null }) }));

function Where() {
  const { activeModule, subTab, routeId, navigate } = useAppState();
  return (
    <div>
      <output aria-label="where">
        {[activeModule, subTab[activeModule] ?? '', routeId ?? ''].join('|')}
      </output>
      <button onClick={() => navigate('admin', 'DATA_SPACES', 'ds-1')}>open</button>
      <button onClick={() => navigate('build', 'WORKFLOWS')}>old</button>
    </div>
  );
}

const Layout = () => (
  <AppStateProvider>
    <Where />
  </AppStateProvider>
);

const at = (path: string) => {
  const router = createAppRouter({
    history: createMemoryHistory({ initialEntries: [path] }),
    layout: Layout,
  });
  render(<RouterProvider router={router} />);
  return router;
};

const where = () => screen.getByRole('status', { name: 'where' }).textContent;

afterEach(() => window.history.replaceState(null, '', '/'));

describe('router', () => {
  it('lands on Trang chủ', async () => {
    const router = at('/');
    await waitFor(() => expect(router.state.location.pathname).toBe('/home'));
    expect(where()).toBe('home||');
  });

  it('reads the area, the page and the record from the path', async () => {
    at('/admin/data_spaces/a0000000-0000-0000-0000-000000000061');
    await waitFor(() =>
      expect(where()).toBe('admin|DATA_SPACES|a0000000-0000-0000-0000-000000000061'),
    );
  });

  it('lowercases a page and sends a page of an earlier IA to its v3.1 place', async () => {
    const upper = at('/admin/DATA_SPACES/ds-1');
    await waitFor(() => expect(upper.state.location.pathname).toBe('/admin/data_spaces/ds-1'));
  });

  it('drops the id when the page it belonged to moved elsewhere', async () => {
    const old = at('/build/workflows/wf-1');
    await waitFor(() => expect(old.state.location.pathname).toBe('/agents/orchestration'));
    expect(where()).toBe('agents|ORCHESTRATION|');
  });

  it('navigates as a history entry that Back undoes', async () => {
    const router = at('/home');
    await waitFor(() => expect(where()).toBe('home||'));
    fireEvent.click(screen.getByRole('button', { name: 'open' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/admin/data_spaces/ds-1'));
    expect(where()).toBe('admin|DATA_SPACES|ds-1');
    act(() => router.history.back());
    await waitFor(() => expect(where()).toBe('home||'));
    // A caller still naming an old module lands on the canonical page.
    fireEvent.click(screen.getByRole('button', { name: 'old' }));
    await waitFor(() => expect(router.state.location.pathname).toBe('/agents/orchestration'));
  });

  it('opens a link of the hash era as the path it names', async () => {
    window.history.replaceState(null, '', '/#/build/workflows');
    const router = createAppRouter({ history: createBrowserHistory(), layout: Layout });
    render(<RouterProvider router={router} />);
    await waitFor(() => expect(router.state.location.pathname).toBe('/agents/orchestration'));
  });

  it("makes the area rail real links: a click stays in the app, Ctrl-click is the browser's", async () => {
    const Rail = () => (
      <AppStateProvider>
        <Where />
        <SideRail
          label="Quản trị"
          area="admin"
          active="USERS"
          groups={[
            {
              label: 'Người & quyền',
              items: [
                { id: 'USERS', label: 'Người dùng' },
                { id: 'DATA_SPACES', label: 'Data Space' },
              ],
            },
          ]}
        />
      </AppStateProvider>
    );
    const router = createAppRouter({
      history: createMemoryHistory({ initialEntries: ['/admin/users'] }),
      layout: Rail,
    });
    render(<RouterProvider router={router} />);
    const link = await screen.findByRole('link', { name: 'Data Space' });
    expect(link).toHaveAttribute('href', '/admin/data_spaces');
    expect(screen.getByRole('link', { name: 'Người dùng' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    fireEvent.click(link, { ctrlKey: true });
    expect(router.state.location.pathname).toBe('/admin/users');
    fireEvent.click(link);
    await waitFor(() => expect(where()).toBe('admin|DATA_SPACES|'));
  });
});
