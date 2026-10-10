import type { IconName } from '../../design-system/Icon';
import { formatElapsed } from '../../lib/formatVN';
import type { HitlKind, HitlRequest } from '../../lib/v1/types';

/*
 * How a HITL request is drawn as a decision card ("Spec · v3.1 · Thẻ việc Cần bạn":
 * kind = choose | approve | compare | review), from its kind in inbox.hitl_request.
 */
type DecisionKind = 'choose' | 'approve' | 'compare' | 'review';

const BY_HITL_KIND: Partial<Record<HitlKind, DecisionKind>> = {
  clarification: 'choose',
  entity_match: 'choose',
  forecast_assumption: 'choose',
  conflict: 'compare',
  report_publish: 'review',
  data_review: 'review',
  knowledge_finding: 'review',
};

export function decisionKindOf(request: HitlRequest): DecisionKind {
  const kind = BY_HITL_KIND[request.kind] ?? 'approve';
  // A clarification without choices is answered in words, like an approval.
  return kind === 'choose' && request.options.length === 0 ? 'approve' : kind;
}

export const DECISION: Record<
  DecisionKind,
  {
    icon: IconName;
    dot: 'info' | 'warning' | 'error' | 'primary';
    label: (r: HitlRequest) => string;
  }
> = {
  choose: { icon: 'chat', dot: 'info', label: (r) => `Chọn 1 trong ${r.options.length}` },
  approve: { icon: 'approve', dot: 'warning', label: () => 'Duyệt thay đổi' },
  compare: { icon: 'compare', dot: 'error', label: () => 'So sánh hai nguồn' },
  review: {
    icon: 'file',
    dot: 'primary',
    label: (r) => (r.kind === 'report_publish' ? 'Xem lại báo cáo' : 'Xem lại'),
  },
};

/** Under four hours left the deadline is shown in warning ("hạn màu warning khi < 4 giờ"). */
const URGENT_MS = 4 * 60 * 60 * 1000;

export function dueOf(
  request: HitlRequest,
  now = new Date(),
): { text: string; urgent: boolean } | null {
  if (!request.due_at) return null;
  const due = new Date(request.due_at);
  const left = due.getTime() - now.getTime();
  if (left <= 0) return { text: 'đã quá hạn', urgent: true };
  const hhmm = `${String(due.getHours()).padStart(2, '0')}:${String(due.getMinutes()).padStart(2, '0')}`;
  if (left < URGENT_MS) return { text: `còn ${formatElapsed(left / 1000)}`, urgent: true };
  if (due.toDateString() === now.toDateString())
    return { text: `hạn ${hhmm} hôm nay`, urgent: false };
  const ddmm = `${String(due.getDate()).padStart(2, '0')}/${String(due.getMonth() + 1).padStart(2, '0')}`;
  return { text: `hạn ${ddmm} ${hhmm}`, urgent: false };
}

export const isExpiringSoon = (request: HitlRequest, now = new Date()) =>
  dueOf(request, now)?.urgent ?? false;
