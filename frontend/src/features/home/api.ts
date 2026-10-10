import { apiGet } from '../../lib/apiClient';

/*
 * Data of Trang chủ that a backend serves today. The other blocks of the design (your
 * metrics, what agents are doing, today's data) come with /v1/runs, /v1/sources and
 * /v1/metrics of the API spec; they are not drawn until those exist.
 */

/** One item of GET /api/reviews/pending (the HITL queue; /v1/inbox in the API spec). */
export interface PendingReview {
  conv_id: string;
  agent_id: string;
  query: string;
  waiting_secs: number;
  payload: { title: string; description: string } | null;
}

export const fetchPendingReviews = () => apiGet<PendingReview[]>('/api/reviews/pending');

/* Questions asked on Phân tích, kept per viewer by AskDataPage (same key). */
const HISTORY_KEY = 'dataagent.ask.history';

export function recentQuestions(limit: number): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .map((item) =>
        item && typeof item === 'object' && 'q' in item ? String((item as { q: unknown }).q) : '',
      )
      .filter(Boolean)
      .slice(0, limit);
  } catch {
    return [];
  }
}
