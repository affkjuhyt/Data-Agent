import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { EmptyState } from '../../design-system/EmptyState';
import { FilterChips } from '../../design-system/FilterChips';
import { Icon } from '../../design-system/Icon';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getNotificationPreferences,
  type NotificationChannel,
  type NotificationPreferences,
} from '../../lib/v1/adminPlatform';
import {
  listNotifications,
  markNotificationsRead,
  type AppNotification,
  type NotificationCategory,
} from '../../lib/v1/notifications';
import { useV1 } from '../../lib/v1/useV1';
import { dayGroupOf, routeOf, type NotificationRoute } from './notificationRoute';

/*
 * "v3.1 · Chi tiết · Trung tâm thông báo" (2072:3821, UC-08-02): a 440px drawer from the right
 * over a 32% scrim, opened from the bell. Head, category filter, rows grouped by day (unread ones
 * outlined), and at the bottom where each kind of notification is delivered. Esc, the scrim and
 * ✕ close it; focus starts on ✕ and returns to the bell.
 */
type Filter = 'all' | NotificationCategory;

const CHANNEL_LABEL: Record<NotificationChannel, string> = {
  in_app: 'trong ứng dụng',
  teams: 'Teams',
  zalo: 'Zalo',
  email: 'Email',
  digest: 'bản tổng hợp',
};

export function NotificationDrawer({
  open,
  onClose,
  onOpenTarget,
}: {
  open: boolean;
  onClose: () => void;
  onOpenTarget: (route: NotificationRoute) => void;
}) {
  if (!open) return null;
  return <DrawerBody onClose={onClose} onOpenTarget={onOpenTarget} />;
}

function DrawerBody({
  onClose,
  onOpenTarget,
}: {
  onClose: () => void;
  onOpenTarget: (route: NotificationRoute) => void;
}) {
  const feed = useV1(listNotifications, []);
  const preferences = useV1(getNotificationPreferences, []);
  const [filter, setFilter] = useState<Filter>('all');
  // Rows opened in this sitting lose their unread outline at once, before the feed reloads.
  const [readHere, setReadHere] = useState<Set<string>>(new Set());
  const panelRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    return () => previous?.focus();
  }, []);

  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = panelRef.current?.querySelectorAll<HTMLElement>('button');
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const open = (item: AppNotification) => {
    if (!item.read_at && !readHere.has(item.id)) {
      setReadHere((ids) => new Set(ids).add(item.id));
      // Best effort: failing to mark read must not keep the user from the thing itself.
      markNotificationsRead([item.id]).catch(() => {});
    }
    const route = routeOf(item);
    if (route) {
      onClose();
      onOpenTarget(route);
    }
  };

  const items = feed.data?.items ?? [];
  const needsYou = items.filter((n) => n.category === 'needs_you').length;

  return (
    <div className="fixed inset-0 z-[var(--z-drawer)]" onKeyDown={onKeyDown}>
      <button
        type="button"
        tabIndex={-1}
        aria-label="Đóng thông báo"
        onClick={onClose}
        className="absolute inset-0 bg-foreground opacity-[0.32]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="notification-title"
        className="absolute inset-y-0 right-0 flex w-[440px] max-w-full flex-col gap-da-lg overflow-y-auto border border-border bg-card p-da-xl"
      >
        <div className="flex items-start gap-da-md">
          <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
            <h2 id="notification-title" className="text-foreground da-heading-2">
              Thông báo
            </h2>
            <p className="text-muted-foreground da-caption">
              Từ agent, yêu cầu HITL và hệ thống. Chọn kênh nhận cho từng loại.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Đóng"
            onClick={onClose}
            className="inline-flex shrink-0 rounded-sm text-foreground"
          >
            <Icon name="x" />
          </button>
        </div>

        <FilterChips<Filter>
          label="Lọc thông báo"
          value={filter}
          onChange={setFilter}
          items={[
            { value: 'all', label: 'Tất cả' },
            { value: 'needs_you', label: needsYou > 0 ? `Cần bạn · ${needsYou}` : 'Cần bạn' },
            { value: 'agent', label: 'Agent' },
            { value: 'system', label: 'Hệ thống' },
          ]}
        />

        <AsyncRegion
          state={feed}
          what="thông báo"
          isEmpty={(page) => page.items.every((n) => filter !== 'all' && n.category !== filter)}
          empty={
            <EmptyState
              icon="bell"
              title="Không có thông báo"
              description="Khi agent cần bạn hoặc có việc xong, thông báo hiện ở đây."
            />
          }
        >
          {(page) => (
            <NotificationGroups
              items={page.items.filter((n) => filter === 'all' || n.category === filter)}
              readHere={readHere}
              onOpen={open}
            />
          )}
        </AsyncRegion>

        <span className="flex-1" aria-hidden />
        {preferences.data && <ChannelSummary preferences={preferences.data} />}
      </div>
    </div>
  );
}

function NotificationGroups({
  items,
  readHere,
  onOpen,
}: {
  items: AppNotification[];
  readHere: Set<string>;
  onOpen: (item: AppNotification) => void;
}) {
  const now = new Date();
  const groups: { label: string; items: AppNotification[] }[] = [];
  for (const item of items) {
    const label = dayGroupOf(item.created_at, now);
    const last = groups[groups.length - 1];
    if (last?.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }

  return (
    <>
      {groups.map((group) => (
        <section key={group.label} className="flex flex-col gap-da-lg">
          <h3 className="uppercase text-muted-foreground da-overline">{group.label}</h3>
          <ul className="flex flex-col gap-da-lg">
            {group.items.map((item) => {
              const unread = !item.read_at && !readHere.has(item.id);
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(item)}
                    className={cn(
                      'flex w-full items-start gap-da-md rounded-md border px-da-md py-da-sm text-left transition-colors duration-[120ms] ease-out hover:bg-subtle',
                      unread ? 'border-border' : 'border-transparent',
                    )}
                  >
                    <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-full text-foreground">
                      <Icon name={item.icon} />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col">
                      <span className="text-foreground da-body-small">
                        {unread && <span className="sr-only">Chưa đọc: </span>}
                        {item.text}
                      </span>
                      <span className="text-muted-foreground da-caption">
                        {formatTime(item.created_at)}
                      </span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </>
  );
}

/** "Kênh nhận": the channels each kind of notification goes to, from the user's preferences. */
function ChannelSummary({ preferences }: { preferences: NotificationPreferences }) {
  return (
    <div className="flex flex-col gap-da-xs rounded-md border border-border p-da-md">
      <p className="text-muted-foreground da-label">Kênh nhận</p>
      <dl className="flex flex-col gap-da-xs">
        {preferences.events.map((event) => {
          const channels = (Object.keys(CHANNEL_LABEL) as NotificationChannel[]).filter(
            (channel) => event.channels[channel],
          );
          return (
            <div key={event.key} className="flex items-start gap-da-md">
              <dt className="w-[140px] shrink-0 text-muted-foreground da-caption">{event.label}</dt>
              <dd className="min-w-0 flex-1 text-foreground da-body-small">
                {channels.length > 0
                  ? channels.map((channel) => CHANNEL_LABEL[channel]).join(' + ')
                  : 'Tắt'}
              </dd>
            </div>
          );
        })}
      </dl>
    </div>
  );
}
