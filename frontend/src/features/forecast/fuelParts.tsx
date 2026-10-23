import { useEffect, useState } from 'react';
import { Button } from '../../design-system/Button';
import { useToast } from '../../design-system/Toast';
import {
  followForecastSchedule,
  getScheduleCalendar,
  type FuelPriceDistribution,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';

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
 * "Theo dõi lịch": subscribe to the pre-period and post-period bulletins of one fuel, that is to
 * the external calendar whose events the series' model forecasts. Without such a calendar there
 * is nothing to follow and the button is not shown. The api does not say whether the viewer
 * already follows it, so the button starts off and keeps its own state once toggled.
 */
export function FollowScheduleButton({
  seriesId,
  primary = false,
}: {
  seriesId: string;
  primary?: boolean;
}) {
  const toast = useToast();
  const calendar = useV1(['getScheduleCalendar', seriesId], () => getScheduleCalendar(seriesId));
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  useEffect(() => setOn(calendar.data?.following ?? false), [calendar.data]);

  if (!calendar.data) return null;
  const calendarId = calendar.data.id;

  const toggle = async () => {
    setBusy(true);
    try {
      const result = await followForecastSchedule(calendarId, !on);
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
