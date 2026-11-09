import { type Page, api, ok } from './client';
import { getMe } from './me';
import type { components } from './schema';

/*
 * "Cần bạn · lọc Đã xử lý tuần này" (2167:19220, UC-07-07): what was settled this week, by the
 * viewer or by one of their rules, and what followed. GET /v1/inbox filters one status at a time
 * and has no date filter: each settled status is asked for, newest change first, and the week is
 * cut here.
 */
export interface ResolvedRequest {
  id: string;
  title: string;
  /** "me": the viewer answered; "rule": no person did — one of their rules answered for them. */
  resolved_by: 'me' | 'rule';
  resolved_at: string;
  /** What the decision was, from the request's status: "Đã duyệt", "Đã trả lời"… */
  outcome_text: string;
}

type HitlWire = components['schemas']['HitlView'];

/*
 * The hitl_request statuses a decision leaves: open and reassigned still wait, expired and
 * cancelled were settled by nobody.
 */
const OUTCOME: Record<string, string> = {
  answered: 'Đã trả lời',
  approved: 'Đã duyệt',
  rejected: 'Đã từ chối',
  changes_requested: 'Đã yêu cầu sửa',
};

/** Monday 00:00 of the current week, local time. */
function startOfWeek(now: Date = new Date()): Date {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return d;
}

export const listResolvedThisWeek = async (): Promise<Page<ResolvedRequest>> => {
  const since = startOfWeek().getTime();
  const [me, ...pages] = await Promise.all([
    // Without the viewer's id every person's answer counts as theirs.
    getMe().catch(() => null),
    ...Object.keys(OUTCOME).map((status) =>
      ok(
        api.GET('/v1/inbox', {
          params: { query: { status, order_by: 'updated_at desc', page_size: 200 } },
        }),
      ),
    ),
  ]);
  const settledBy = (raw: HitlWire): ResolvedRequest['resolved_by'] | null =>
    raw.resolved_by === null ? 'rule' : !me?.id || raw.resolved_by === me.id ? 'me' : null;
  return {
    items: pages
      .flatMap((page) => page.items)
      .flatMap((raw) => {
        const at = raw.resolved_at ?? raw.updated_at;
        const by = settledBy(raw);
        // Someone else of the viewer's group settled it: not the viewer's week.
        if (by === null || new Date(at).getTime() < since) return [];
        return [
          {
            id: raw.id,
            title: raw.title,
            resolved_by: by,
            resolved_at: at,
            outcome_text: OUTCOME[raw.status] ?? raw.status,
          },
        ];
      })
      .sort((a, b) => b.resolved_at.localeCompare(a.resolved_at)),
    next_page_token: '',
  };
};
