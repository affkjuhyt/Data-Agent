import type { AreaId } from '../../components/shell/areas';
import type { AppNotification } from '../../lib/v1/notifications';

export interface NotificationRoute {
  area: AreaId;
  page?: string;
  id?: string;
}

/** Where a notification row leads; null when it only informs. */
export function routeOf(item: AppNotification): NotificationRoute | null {
  const target = item.target;
  if (!target) return null;
  switch (target.kind) {
    case 'proposal':
      return { area: 'needs-you', page: 'PROPOSAL', id: target.id };
    case 'run':
      return { area: 'agents', page: 'RUN', id: target.id };
    case 'hitl':
      // The request's own screen depends on its kind; the list shows it with its options.
      return { area: 'needs-you' };
  }
}

/** "Hôm nay", "Hôm qua", then the date for anything older. */
export function dayGroupOf(createdAt: string, now: Date): string {
  const day = new Date(createdAt);
  const startOf = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((startOf(now) - startOf(day)) / 86_400_000);
  if (days <= 0) return 'Hôm nay';
  if (days === 1) return 'Hôm qua';
  return `${String(day.getDate()).padStart(2, '0')}/${String(day.getMonth() + 1).padStart(2, '0')}`;
}
