/**
 * The real-time inbox: one connection per tab that brings new review cases and
 * alerts for this user, so the screens that show them refresh within seconds
 * without a page reload. Only the affected queries are refreshed.
 *
 * Which events reach which user is the server's decision (only cases and
 * alerts the user has authority over); the client shows what it is sent.
 *
 * The stream contract is provisional until the inbox endpoint exists (NOP-236):
 * `event:` is the Kafka topic (contract `EventType`), `id:` the event_id and
 * `data:` the `EventEnvelope`. `toNotification` is the one place that reads it.
 * Unread is counted in this tab until the server keeps an unread count.
 */
import { useQueryClient, type QueryKey } from '@tanstack/react-query';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { authHeaders } from '../apiClient';
import type { components } from '../api/contracts';
import { queryKeys } from '../api/queryKeys';
import { INDICATOR_API_URL } from '../config';
import {
  openReconnectingStream,
  type ReconnectingStreamOptions,
  type SseMessage,
  type StreamStatus,
} from '../sseClient';

type EventType = components['schemas']['EventType'];

// The topics the inbox carries and the data each one makes stale.
const TOPIC_QUERIES = {
  'review.case.created': queryKeys.reviewCases.all,
  'review.approved': queryKeys.reviewCases.all,
  'alert.raised': queryKeys.alerts.all,
} satisfies Partial<Record<EventType, QueryKey>>;

type InboxTopic = keyof typeof TOPIC_QUERIES;

interface InboxNotification {
  /** event_id; absent only if the server sent neither `id:` nor an envelope */
  id?: string;
  topic: InboxTopic;
}

export function toNotification(message: SseMessage): InboxNotification | null {
  if (!(message.event in TOPIC_QUERIES)) return null;
  let eventId: unknown;
  try {
    eventId = (JSON.parse(message.data) as { event_id?: unknown })?.event_id;
  } catch {
    /* the topic alone is enough to refresh */
  }
  return {
    id: message.id || (typeof eventId === 'string' ? eventId : undefined),
    topic: message.event as InboxTopic,
  };
}

// Enough to drop a server's replay after a reconnect; bounded for long sessions.
const SEEN_LIMIT = 500;

interface NotificationsValue {
  status: StreamStatus;
  unread: number;
  markAllRead: () => void;
}

const NotificationsContext = createContext<NotificationsValue>({
  status: 'unavailable',
  unread: 0,
  markAllRead: () => {},
});

export const useNotifications = () => useContext(NotificationsContext);

const defaultUrl = () => (INDICATOR_API_URL ? `${INDICATOR_API_URL}/notifications/stream` : '');

export function NotificationProvider({
  children,
  url = defaultUrl(),
  stream,
}: {
  children: ReactNode;
  /** empty = no endpoint configured: the inbox stays off */
  url?: string;
  /** transport overrides (tests) */
  stream?: Pick<ReconnectingStreamOptions, 'fetchImpl' | 'sleep' | 'baseDelayMs'>;
}) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<StreamStatus>(url ? 'connecting' : 'unavailable');
  const [unread, setUnread] = useState(0);
  const seen = useRef<Set<string>>(new Set());
  const streamRef = useRef(stream);

  useEffect(() => {
    if (!url) return;
    const onMessage = (message: SseMessage) => {
      const n = toNotification(message);
      if (!n) return;
      if (n.id) {
        if (seen.current.has(n.id)) return;
        seen.current.add(n.id);
        if (seen.current.size > SEEN_LIMIT)
          seen.current.delete(seen.current.values().next().value as string);
      }
      void queryClient.invalidateQueries({ queryKey: TOPIC_QUERIES[n.topic] });
      setUnread((u) => u + 1);
    };
    return openReconnectingStream({
      url,
      headers: authHeaders,
      onMessage,
      onStatus: setStatus,
      // Whatever happened while disconnected: refresh the inbox data even if
      // the server cannot replay from Last-Event-ID.
      onReconnected: () => {
        for (const key of new Set(Object.values(TOPIC_QUERIES)))
          void queryClient.invalidateQueries({ queryKey: key });
      },
      ...streamRef.current,
    });
  }, [url, queryClient]);

  const markAllRead = useCallback(() => setUnread(0), []);
  const value = useMemo(() => ({ status, unread, markAllRead }), [status, unread, markAllRead]);
  return <NotificationsContext.Provider value={value}>{children}</NotificationsContext.Provider>;
}
