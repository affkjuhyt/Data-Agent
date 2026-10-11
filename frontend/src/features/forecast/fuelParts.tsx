import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { useToast } from '../../design-system/Toast';
import { followForecastSchedule, type FuelPriceDistribution } from '../../lib/v1/forecast';

/* Pieces the two fuel-price screens (UC-15-07, UC-15-08) share. */

const parts = (iso: string) => {
  const [year, month, day] = iso.slice(0, 10).split('-');
  return { year, month: String(Number(month)), monthPadded: month, day };
};

/** "2026-07-09" → "09/7", as the Figma frames write a period. */
export const dayMonth = (iso: string) => {
  const p = parts(iso);
  return `${p.day}/${p.month}`;
};

/** "2026-07-09" → "09/7/2026" */
export const dayMonthYear = (iso: string) => `${dayMonth(iso)}/${parts(iso).year}`;

/** "2026-02-26" → "26/02", the ticks of the ledger chart. */
export const dayMonthPadded = (iso: string) => {
  const p = parts(iso);
  return `${p.day}/${p.monthPadded}`;
};

/** "2026-07-08T18:02:00+07:00" → "08/7 18:02", the time as published (Vietnam time). */
export const dayMonthTime = (iso: string) => `${dayMonth(iso)} ${iso.slice(11, 16)}`;

/**
 * A distribution is shown only with both of its intervals, nested around the median; anything
 * else is a forecast without its interval and is not drawn (docs/v3 forecast rules).
 */
export function hasInterval(
  d: Pick<FuelPriceDistribution, 'median' | 'lo80' | 'hi80' | 'lo95' | 'hi95'>,
) {
  const values = [d.lo95, d.lo80, d.median, d.hi80, d.hi95];
  return (
    values.every((v) => Number.isFinite(v)) && values.every((v, i) => i === 0 || values[i - 1] <= v)
  );
}

/**
 * "Theo dõi lịch": subscribe to the pre-period and post-period bulletins of one fuel. The state
 * comes with the page's resource; the button keeps its own copy once toggled.
 */
export function FollowScheduleButton({
  seriesId,
  following,
  primary = false,
}: {
  seriesId: string;
  following: boolean;
  primary?: boolean;
}) {
  const toast = useToast();
  const [on, setOn] = useState(following);
  const [busy, setBusy] = useState(false);
  useEffect(() => setOn(following), [following, seriesId]);

  const toggle = async () => {
    setBusy(true);
    try {
      const result = await followForecastSchedule(seriesId, !on);
      setOn(result.following);
      toast({
        tone: 'success',
        message: result.following
          ? 'Đã theo dõi lịch: bạn nhận bản tin trước và sau mỗi kỳ'
          : 'Đã bỏ theo dõi lịch',
      });
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      kind={primary && !on ? 'primary' : 'secondary'}
      loading={busy}
      aria-pressed={on}
      onClick={toggle}
    >
      {on ? 'Đang theo dõi lịch' : 'Theo dõi lịch'}
    </Button>
  );
}
