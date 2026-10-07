/**
 * Header entry of the real-time inbox: the unread count and, while the
 * connection is down, a note that notifications may be late. Hidden when the
 * inbox is off (no endpoint configured, or the server has none for this user).
 * The list itself is the Review / Alert inbox screens'.
 */
import { Bell } from 'lucide-react';
import { useNotifications } from '../../lib/notifications/NotificationProvider';

export const RECONNECTING_MESSAGE = 'Đang kết nối lại, thông báo có thể đến chậm';

export function NotificationBell() {
  const { status, unread, markAllRead } = useNotifications();
  if (status === 'unavailable') return null;

  return (
    <div className="flex items-center gap-2">
      {status === 'reconnecting' && (
        <span role="status" className="hidden md:inline text-[11px] text-[#8A5A00]">
          {RECONNECTING_MESSAGE}
        </span>
      )}
      <button
        onClick={markAllRead}
        aria-label={unread ? `Thông báo: ${unread} chưa đọc` : 'Thông báo'}
        title={status === 'reconnecting' ? RECONNECTING_MESSAGE : 'Thông báo'}
        className="relative w-8 h-8 flex items-center justify-center rounded-lg text-[#3F3F3F] hover:text-[#111111] hover:bg-[#FFF9E8] border border-transparent hover:border-[#BFA66A] transition-colors"
      >
        <Bell className="w-4 h-4" />
        {unread > 0 && (
          <span className="absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-[#9F1D1D] text-white text-[10px] font-bold leading-4 text-center">
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
    </div>
  );
}
