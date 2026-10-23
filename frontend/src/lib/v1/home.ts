import { type Page, api, ok } from './client';
import type { components } from './schema';

/*
 * What Trang chủ shows about the viewer beyond their inbox and runs ("v3.1 · Trang chủ"
 * 2132:15338, "Mobile · Trang chủ" 2231:15114, UC-01-01, UC-08-05): the metrics they follow with
 * this period's value, how it moved and why in one line, a short series; and the morning summary
 * the agents wrote. There is no single Home read in A2: the summary is the newest digest
 * notification (GET /v1/me/notifications?kind=digest). The followed metrics are the ones of
 * PUT /v1/me/digest, which A2 cannot read back, so `metrics` stays empty and the block is hidden.
 */
export interface MyMetric {
  metric_id: string;
  label: string;
  /** Short label for the mobile 2×2 grid ("NPL bán lẻ"). */
  short_label: string;
  value: string;
  certified: boolean;
  delta: { text: string; direction: 'up' | 'down' | 'flat'; good: boolean; warning?: boolean };
  /** "DH Bắc Bộ đóng góp 74%" */
  note: string;
  /** The last periods, oldest first, for the small bars. */
  series: number[];
}

export interface MyHome {
  metrics: MyMetric[];
  briefing: { lines: string[]; title: string } | null;
  /** "Bản tin lúc 06:00"; empty when there is no digest. */
  data_note: string;
}

type NotificationView = components['schemas']['NotificationView'];

const hhmm = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

/** The newest digest: its title, and its body one line per point. */
export const getMyHome = async (): Promise<MyHome> => {
  const page = await ok<Page<NotificationView>>(
    api.GET('/v1/me/notifications', {
      params: {
        query: {
          kind: 'digest',
          page_size: 1,
        },
      },
    }),
  );
  const digest = page.items[0];
  const lines = (digest?.body ?? '')
    .split('\n')
    .map((l) => l.replace(/^[-·*]\s*/, '').trim())
    .filter(Boolean);
  return {
    metrics: [],
    briefing: digest ? { title: digest.title, lines } : null,
    data_note: digest ? `Bản tin lúc ${hhmm(digest.created_at)}` : '',
  };
};
