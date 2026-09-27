import { describe, it, expect } from 'vitest';
import { buildCron, cronToScheduleState, isCronRepresentable, cronToHuman } from './sourceShared';

describe('cronToScheduleState', () => {
  it('parses a plain wildcard-hour cron ("every hour") instead of falling back to none', () => {
    // Bulk Import's "hourly" word preset -> "0 * * * *" (sources.py's
    // _SCHEDULE_WORD_TO_CRON) — found live: opening the Edit picker on a
    // source with this cron silently showed "No schedule".
    const state = cronToScheduleState('0 * * * *');
    expect(state.mode).toBe('hourly');
    expect(state.hourEvery).toBe(1);
    expect(state.minuteEvery).toBe(0);
  });

  it('parses a multi-day monthly cron (no separate "twice a month" mode)', () => {
    // Bulk Import's old "semi-monthly" word preset -> "0 3 1,15 * *" is just
    // Monthly with 2 days ticked, not its own mode.
    const state = cronToScheduleState('0 3 1,15 * *');
    expect(state.mode).toBe('monthly');
    expect(state.monthDays).toEqual([1, 15]);
    expect(state.time).toBe('03:00');
  });

  it('parses a single-day monthly cron', () => {
    const state = cronToScheduleState('0 3 3 * *');
    expect(state.mode).toBe('monthly');
    expect(state.monthDays).toEqual([3]);
  });

  it('parses a multi-month yearly cron (no separate "twice a year" mode)', () => {
    // Bulk Import's old "semi-annual" word preset -> "0 3 1 1,7 *" is just
    // Yearly with 2 months ticked, not its own mode.
    const state = cronToScheduleState('0 3 1 1,7 *');
    expect(state.mode).toBe('yearly');
    expect(state.dayOfMonth).toBe(1);
    expect(state.months).toEqual([1, 7]);
  });

  it('parses a single-month yearly cron', () => {
    const state = cronToScheduleState('0 3 1 1 *');
    expect(state.mode).toBe('yearly');
    expect(state.months).toEqual([1]);
  });

  it('still recognizes quarterly (a fixed 4-month set) distinctly from a generic multi-month yearly cron', () => {
    const state = cronToScheduleState('0 3 1 1,4,7,10 *');
    expect(state.mode).toBe('quarterly');
    expect(state.dayOfMonth).toBe(1);
  });

  it('still falls back to none for a genuinely unrepresentable cron', () => {
    expect(cronToScheduleState('7 3,9 * * 1-5').mode).toBe('none');
  });
});

describe('buildCron / isCronRepresentable round-trip', () => {
  it.each([
    '0 3 1,15 * *',      // multi-day monthly
    '0 3 1 1,7 *',       // multi-month yearly
    '*/15 * * * *',      // minute-step hourly
    '0 3 * * *',         // daily
    '0 3 1 * *',         // single-day monthly
    '0 3 1 1,4,7,10 *',  // quarterly
    '0 3 1 1 *',         // single-month yearly
  ])('round-trips %s', (cron) => {
    expect(isCronRepresentable(cron)).toBe(true);
  });

  it('"every hour" round-trips to a cron-equivalent (though not byte-identical) form', () => {
    // "0 * * * *" and "0 */1 * * *" mean the same thing to every cron reader
    // in this codebase (croniter treats * and */1 identically) — this is
    // exactly why isCronRepresentable flags it, but re-saving it is safe.
    const state = cronToScheduleState('0 * * * *');
    const rebuilt = buildCron(
      state.mode, state.time, state.days, state.monthDays, state.dayOfMonth,
      state.months, state.hourEvery, state.minuteEvery,
    );
    expect(rebuilt).toBe('0 */1 * * *');
  });

  it('sorts multi-day/multi-month selections so the cron field order is stable', () => {
    expect(buildCron('monthly', '03:00', [], [15, 1], 1, [1], 0, 15)).toBe('0 3 1,15 * *');
    expect(buildCron('yearly', '03:00', [], [1], 1, [7, 1], 0, 15)).toBe('0 3 1 1,7 *');
  });
});

describe('cronToHuman', () => {
  it('reads a multi-day monthly cron in plain language', () => {
    expect(cronToHuman('0 3 1,15 * *')).toBe('Days 1, 15 of every month at 03:00');
  });

  it('reads a multi-month yearly cron in plain language', () => {
    expect(cronToHuman('0 3 1 1,7 *')).toBe('Day 1 of Jan, Jul every year at 03:00');
  });

  it('reads a plain wildcard-hour cron in plain language', () => {
    expect(cronToHuman('0 * * * *')).toBe('Every hour');
  });
});
