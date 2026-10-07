/**
 * "Someone else saved first" — shown after a 409 VERSION_CONFLICT, once the
 * screen already holds the latest version (useVersionedMutation). It says who
 * and when when the record carries it, and lists what changed so the user
 * decides again on the new content instead of overwriting it.
 */
import { AlertTriangle } from 'lucide-react';
import { formatTime } from '../../lib/formatVN';
import type { FieldChange } from '../../lib/api/useVersionedMutation';
import { Button } from '../ui';

const show = (value: unknown): string =>
  value === null || value === undefined
    ? '—'
    : typeof value === 'object'
      ? JSON.stringify(value)
      : String(value);

export function ConflictNotice({
  subject = 'Hồ sơ',
  actor,
  at,
  changes,
  onDismiss,
}: {
  /** what was changed, e.g. "Hồ sơ" */
  subject?: string;
  /** who saved first, when the record says (ReviewCaseRef v1 does not) */
  actor?: string | null;
  at?: string | null;
  changes: FieldChange[];
  onDismiss?: () => void;
}) {
  const who = actor && at ? `${actor} cập nhật lúc ${formatTime(at)}` : 'người khác cập nhật';
  return (
    <div
      role="alert"
      className="p-4 rounded-xl border border-[#B88719] bg-[#FFF9E8] flex items-start gap-3"
    >
      <AlertTriangle className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
      <div className="flex-1 min-w-0">
        <p className="text-sm font-bold text-[#111111]">
          {subject} đã được {who}
        </p>
        <p className="text-[12px] text-[#3F3F3F] mt-0.5">
          Màn hình đang hiện bản mới nhất; thay đổi của bạn chưa được lưu.
        </p>
        {changes.length > 0 && (
          <ul className="mt-2 space-y-0.5 text-[12px] text-[#3F3F3F] font-mono">
            {changes.map((c) => (
              <li key={c.field}>
                {c.field}: {show(c.before)} → {show(c.after)}
              </li>
            ))}
          </ul>
        )}
      </div>
      {onDismiss && (
        <Button size="sm" onClick={onDismiss}>
          Đã hiểu
        </Button>
      )}
    </div>
  );
}
