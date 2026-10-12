/**
 * Trung tâm thông báo against the v1 mock: grouping by day, the category filter, opening a row
 * (marks it read, leads to the resource) and the channel summary from the user's preferences.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { listNotifications, type AppNotification } from '../../lib/v1/notifications';
import { notificationPreferencesFixture, prefsWire } from '../../mocks/adminPlatform';
import { createNotificationHandlers } from '../../mocks/notifications';
import { NotificationDrawer } from './NotificationDrawer';
import { dayGroupOf, routeOf } from './notificationRoute';

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() =>
  server.resetHandlers(
    ...createNotificationHandlers(),
    http.get('*/v1/me/notification-prefs', () =>
      HttpResponse.json(prefsWire(notificationPreferencesFixture())),
    ),
  ),
);
afterAll(() => server.close());

const drawer = (props: Partial<Parameters<typeof NotificationDrawer>[0]> = {}) =>
  render(<NotificationDrawer open onClose={() => {}} onOpenTarget={() => {}} {...props} />);

describe('NotificationDrawer', () => {
  it('groups the feed by day and outlines what is unread', async () => {
    drawer();
    const dialog = screen.getByRole('dialog', { name: 'Thông báo' });
    expect(await within(dialog).findByText('Hôm nay')).toBeInTheDocument();
    expect(within(dialog).getByText('Hôm qua')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Chưa đọc:', { exact: false })).toHaveLength(2);
    expect(within(dialog).getByRole('radio', { name: 'Cần bạn · 3' })).toBeInTheDocument();
  });

  it('filters by category', async () => {
    drawer();
    await screen.findByText('Hôm nay');
    fireEvent.click(screen.getByRole('radio', { name: 'Hệ thống' }));
    expect(screen.getAllByRole('listitem')).toHaveLength(1);
    expect(screen.getByText(/mất kết nối DWH/)).toBeInTheDocument();
  });

  it('opening a row marks it read and leads to its resource', async () => {
    const onOpenTarget = vi.fn();
    const onClose = vi.fn();
    drawer({ onOpenTarget, onClose });
    fireEvent.click(await screen.findByRole('button', { name: /duyệt 214 khoản vay/ }));
    expect(onClose).toHaveBeenCalled();
    expect(onOpenTarget).toHaveBeenCalledWith({
      area: 'needs-you',
      page: 'PROPOSAL',
      id: 'proposal-214',
    });
    await waitFor(async () => {
      const feed = await listNotifications();
      expect(feed.items.find((n) => n.id === 'ntf-adjust-214')?.read_at).not.toBeNull();
    });
  });

  it('closes on Esc and shows where each kind is delivered', async () => {
    const onClose = vi.fn();
    drawer({ onClose });
    expect(await screen.findByText('Kênh nhận')).toBeInTheDocument();
    expect(screen.getByText('Agent cần tôi quyết định').nextSibling).toHaveTextContent(
      'trong ứng dụng + Teams + Email',
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});

describe('notification routing', () => {
  const item = (target: AppNotification['target']): AppNotification => ({
    id: 'n',
    category: 'agent',
    text: '',
    icon: 'check',
    created_at: '2026-10-10T08:00:00+07:00',
    read_at: null,
    target,
  });

  it('builds the route from the resource kind, never from a URL', () => {
    expect(routeOf(item({ kind: 'run', id: 'run-427' }))).toEqual({
      area: 'agents',
      page: 'RUN',
      id: 'run-427',
    });
    expect(routeOf(item({ kind: 'hitl', id: 'x' }))).toEqual({ area: 'needs-you' });
    expect(routeOf(item(null))).toBeNull();
  });

  it('names the day relative to now', () => {
    const now = new Date(2026, 9, 10, 9, 0);
    expect(dayGroupOf(new Date(2026, 9, 10, 1, 0).toISOString(), now)).toBe('Hôm nay');
    expect(dayGroupOf(new Date(2026, 9, 9, 23, 0).toISOString(), now)).toBe('Hôm qua');
    expect(dayGroupOf(new Date(2026, 9, 3, 12, 0).toISOString(), now)).toBe('03/10');
  });
});
