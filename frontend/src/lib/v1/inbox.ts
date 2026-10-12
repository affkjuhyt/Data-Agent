import { v1Get, type Page } from './client';

/*
 * "Cần bạn · lọc Đã xử lý tuần này" (2167:19220, UC-07-07): what was settled this week, by the
 * viewer or by one of their rules, and what followed. A2's GET /v1/inbox returns pending requests
 * only; `status=resolved` with `since` and the display fields are a PROPOSAL (docs/v3/05-ui-v3.md §4).
 */
export interface ResolvedRequest {
  id: string;
  title: string;
  /** "me": the viewer answered; "rule": one of their rules answered for them. */
  resolved_by: 'me' | 'rule';
  resolved_at: string;
  /** "Agent đã chạy tiếp", "Theo quy tắc của bạn", "2 agent đã cập nhật" */
  outcome_text: string;
}

/** Monday 00:00 of the current week, local time. */
export function startOfWeek(now: Date = new Date()): Date {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}

export const listResolvedThisWeek = async () =>
  v1Get<Page<ResolvedRequest>>('/inbox', {
    status: 'resolved',
    since: startOfWeek().toISOString(),
    limit: 200,
  });
