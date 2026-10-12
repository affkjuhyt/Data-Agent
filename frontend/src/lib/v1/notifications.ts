import { v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

/*
 * "v3.1 · Chi tiết · Trung tâm thông báo" (2072:3821, UC-08-02). The system design names the
 * notification channels a user picks (GET/PUT /v1/me/notification-preferences, lib/v1/adminPlatform)
 * and GET /v1/me/notifications (NotificationView, read below). PROPOSAL: :mark-read.
 */
export type NotificationCategory = 'needs_you' | 'agent' | 'system';

export interface AppNotification {
  id: string;
  category: NotificationCategory;
  /** What happened, one sentence: "Agent Đối soát cần bạn duyệt 214 khoản vay". */
  text: string;
  /** Picks the icon: a decision, a warning, a finished job, an agent suggestion, a new version. */
  icon: 'approve' | 'alert' | 'check' | 'agent' | 'layers';
  created_at: string;
  read_at: string | null;
  /**
   * Where the row leads. A resource kind and id rather than a URL, so the client builds the
   * route itself and a notification can never send the user to an arbitrary address.
   */
  target: { kind: 'hitl' | 'proposal' | 'run'; id: string } | null;
}

/** GET /v1/me/notifications as the api sends it (NotificationView); the mock adds the screen's fields. */
export type NotificationWire = components['schemas']['NotificationView'] &
  Partial<Pick<AppNotification, 'category' | 'text' | 'icon' | 'target'>>;

/** notification.kind: alert, hitl, digest, task_done, comment, share, report, system. */
const CATEGORY: Record<string, NotificationCategory> = {
  hitl: 'needs_you',
  alert: 'agent',
  task_done: 'agent',
  report: 'agent',
  digest: 'agent',
};

const ICON: Record<string, AppNotification['icon']> = {
  hitl: 'approve',
  alert: 'alert',
  task_done: 'check',
  report: 'layers',
  digest: 'layers',
  comment: 'agent',
  share: 'agent',
};

const readNotification = (raw: NotificationWire): AppNotification => ({
  id: raw.id,
  category: raw.category ?? CATEGORY[raw.kind] ?? 'system',
  text: raw.text ?? raw.title,
  icon: raw.icon ?? ICON[raw.kind] ?? 'alert',
  created_at: raw.created_at,
  read_at: raw.read_at,
  // Only a HITL request is a target the client routes by id; the rest stay on the feed.
  target:
    raw.target ??
    (raw.kind === 'hitl' && raw.source_ref ? { kind: 'hitl', id: raw.source_ref } : null),
});

export const listNotifications = async () => {
  const page = await v1Get<Page<NotificationWire>>('/me/notifications', { page_size: 100 });
  return { ...page, items: page.items.map(readNotification) };
};

export const markNotificationsRead = (ids: string[]) =>
  v1Send<{ marked: number }>('POST', '/me/notifications:mark-read', { ids });
