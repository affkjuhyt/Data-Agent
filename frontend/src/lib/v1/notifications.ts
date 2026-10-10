import { v1Get, v1Send, type Page } from './client';

/*
 * "v3.1 · Chi tiết · Trung tâm thông báo" (2072:3821, UC-08-02). The system design names the
 * notification channels a user picks (GET/PUT /v1/me/notification-preferences, lib/v1/adminPlatform)
 * but not the feed itself. PROPOSAL: GET /v1/me/notifications and :mark-read, newest first.
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

export const listNotifications = () =>
  v1Get<Page<AppNotification>>('/me/notifications', { page_size: 100 });

export const markNotificationsRead = (ids: string[]) =>
  v1Send<{ marked: number }>('POST', '/me/notifications:mark-read', { ids });
