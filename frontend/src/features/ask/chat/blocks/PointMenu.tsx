import { Icon } from '../../../../design-system/Icon';
import { formatNumber } from '../../../../lib/formatVN';

/*
 * E3: a point of an answer's chart, picked by a click on it or from the list here (the keyboard's
 * way to the same point), and what can be done with it. Filtering on it and asking why are
 * follow-up questions — the figures they need are read by the agent again, governed like any
 * answer (C7). Leaving it out only hides it from this chart: the rows are the server's either way.
 */

export interface Point {
  category: string;
  value: number | null;
  /** Every series at this point, when the chart draws more than one (2012 beside 2013). */
  series?: { name: string; value: number | null }[];
}

/** A member of a time axis said the way a question says a period ("2013-05" → "tháng 5/2013"),
 *  so the agent reads it as the period: "tháng 2013-05" was not read as one, and its words were
 *  refused as a quantity no metric measures. Null for a member that is not a period. */
export function periodOf(category: string): string | null {
  let m = /^(\d{4})$/.exec(category);
  if (m) return `năm ${m[1]}`;
  m = /^(\d{4})-(\d{1,2})$/.exec(category);
  if (m) return `tháng ${Number(m[2])}/${m[1]}`;
  m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(category);
  if (m) return `ngày ${Number(m[3])}/${Number(m[2])}/${m[1]}`;
  m = /^(\d{4})-?Q([1-4])$/i.exec(category);
  if (m) return `quý ${m[2]}/${m[1]}`;
  m = /^Q([1-4])[/ ](\d{4})$/i.exec(category);
  if (m) return `quý ${m[1]}/${m[2]}`;
  return null;
}

/** The point as a question names it: its period, or its member of the axis. */
const pointName = (axis: string, category: string) => periodOf(category) ?? `${axis.toLowerCase()} ${category}`;

/** "Doanh thu Internet tháng 5/2013": the measure at that period; a member of another axis
 *  narrows the question the chart answered ("…, chỉ xét nhóm sản phẩm Bikes"). */
export function filterQuestion(measure: string, asked: string, axis: string, category: string) {
  const period = periodOf(category);
  if (period) return `${measure} ${period}`;
  return `${asked.trim().replace(/[.?!]+$/, '')}, chỉ xét ${axis.toLowerCase()} ${category}`;
}

/** "Vì sao Doanh thu Internet tháng 12/2013 cao nhất?": what stands out about the point, read
 *  from the drawn values, so the question names the thing to explain. */
export function whyQuestion(measure: string, axis: string, points: Point[], category: string, timeAxis: boolean) {
  const i = points.findIndex((p) => p.category === category);
  const value = points[i]?.value ?? null;
  const values = points.map((p) => p.value).filter((v): v is number => v !== null);
  const at = `${measure} ${pointName(axis, category)}`;
  if (value === null || values.length < 2) return `Vì sao ${at} như vậy?`;
  if (value === Math.max(...values)) return `Vì sao ${at} cao nhất?`;
  if (value === Math.min(...values)) return `Vì sao ${at} thấp nhất?`;
  const before = timeAxis && i > 0 ? points[i - 1] : undefined;
  if (before?.value != null && before.value !== value)
    return `Vì sao ${at} ${value > before.value ? 'tăng' : 'giảm'} so với ${pointName(axis, before.category)}?`;
  return `Vì sao ${at} đạt ${formatNumber(value)}?`;
}

const action =
  'inline-flex items-center gap-[4px] rounded-md border border-border px-da-sm py-[3px] text-foreground da-body-small hover:bg-subtle disabled:opacity-50';

export function PointMenu({
  axis,
  measure,
  unit,
  points,
  picked,
  onPick,
  onFilter,
  onExclude,
  onWhy,
}: {
  axis: string;
  measure: string;
  unit?: string;
  points: Point[];
  picked: string | null;
  onPick: (category: string | null) => void;
  /** Absent while the agent is busy or the answer is not the reader's to follow up. */
  onFilter?: (category: string) => void;
  /** Absent when one point is left: a chart of none says nothing. */
  onExclude?: (category: string) => void;
  onWhy?: (category: string) => void;
}) {
  const point = points.find((p) => p.category === picked) ?? null;
  return (
    <div className="flex flex-col gap-da-xs">
      <label className="inline-flex items-center gap-da-sm self-end text-muted-foreground da-caption">
        Chọn điểm
        <select
          aria-label={`Chọn điểm theo ${axis.toLowerCase()}`}
          value={picked ?? ''}
          onChange={(e) => onPick(e.target.value || null)}
          className="max-w-[160px] rounded-sm border border-border bg-card px-da-sm py-[2px] text-foreground da-caption"
        >
          <option value="">—</option>
          {points.map((p) => (
            <option key={p.category} value={p.category}>
              {p.category}
            </option>
          ))}
        </select>
      </label>
      {point && (
        <div
          role="toolbar"
          aria-label={`Điểm ${point.category}`}
          className="flex flex-wrap items-center gap-da-sm rounded-md border border-border bg-subtle px-da-md py-da-sm"
        >
          <span className="text-foreground da-body-small">
            <span className="text-muted-foreground">{axis}</span> {point.category}
            {(point.series && point.series.length > 1 ? point.series : [{ name: measure, value: point.value }])
              .filter((s) => s.value !== null)
              .map((s) => (
                <span key={s.name}>
                  {' · '}
                  {s.name} {formatNumber(s.value!)}
                  {unit ? ` ${unit}` : ''}
                </span>
              ))}
          </span>
          <span className="flex-1" />
          {onFilter && (
            <button type="button" className={action} onClick={() => onFilter(point.category)}>
              <Icon name="filter" size={14} /> Lọc theo giá trị này
            </button>
          )}
          {onExclude && (
            <button type="button" className={action} onClick={() => onExclude(point.category)}>
              <Icon name="eye" size={14} /> Loại bỏ điểm này
            </button>
          )}
          {onWhy && (
            <button type="button" className={action} onClick={() => onWhy(point.category)}>
              <Icon name="sparkles" size={14} /> Phân tích vì sao
            </button>
          )}
          <button
            type="button"
            aria-label="Bỏ chọn điểm"
            onClick={() => onPick(null)}
            className="rounded p-[3px] text-muted-foreground hover:bg-card"
          >
            <Icon name="x" size={14} />
          </button>
        </div>
      )}
    </div>
  );
}
