/**
 * "Đi sâu" under a bar chart (UC-01-04, AC-01-04-02): a member of the chart's axis — picked by
 * clicking its bar, or chosen here from the keyboard — and one of the metric's other cuts. The
 * choice is asked as a follow-up ("Đi sâu Miền Bắc theo chi nhánh"), which worker-agent reads
 * against this answer: the metric and period carry over, the member becomes a filter and the cut
 * replaces the axis.
 */
import { SuggestionChip } from '../../../design-system/SuggestionChip';
import { drillQuestion, type Drill } from '../dataAnswer';

export function DrillPicker({
  drill,
  members,
  picked,
  onPick,
  onAsk,
  disabled,
}: {
  drill: Drill;
  members: string[];
  picked: string | null;
  onPick: (member: string | null) => void;
  onAsk: (question: string) => void;
  disabled?: boolean;
}) {
  if (!members.length) return null;
  return (
    <div role="group" aria-label="Đi sâu" className="flex flex-wrap items-center gap-da-sm">
      <label className="inline-flex items-center gap-da-sm text-muted-foreground da-caption">
        Đi sâu {drill.axis}
        <select
          aria-label={drill.axis}
          value={picked ?? ''}
          disabled={disabled}
          onChange={e => onPick(e.target.value || null)}
          className="rounded-sm border border-border bg-card px-da-sm py-[3px] text-foreground da-body-small"
        >
          <option value="">chọn…</option>
          {members.map(m => (
            <option key={m} value={m}>{m}</option>
          ))}
        </select>
      </label>
      {picked && (
        <>
          <span className="text-muted-foreground da-caption">theo</span>
          {drill.cuts.map(cut => (
            <SuggestionChip
              key={cut.code}
              label={cut.name}
              showIcon={false}
              onSelect={() => {
                if (disabled) return;
                onPick(null);
                onAsk(drillQuestion(picked, cut.name));
              }}
            />
          ))}
        </>
      )}
    </div>
  );
}
