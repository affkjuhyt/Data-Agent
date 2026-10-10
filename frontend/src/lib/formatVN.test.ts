import { describe, expect, it } from 'vitest';
import {
  formatCell,
  formatCompact,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatDuration,
  formatElapsed,
  formatNumber,
  formatPercent,
  formatRelative,
  formatTime,
  isNumericColumn,
  toDate,
  toNumber,
  WEEK_STARTS_ON,
} from './formatVN';

describe('formatNumber', () => {
  it('groups thousands with a dot', () => {
    expect(formatNumber(1284560000)).toBe('1.284.560.000');
    expect(formatNumber(999)).toBe('999');
    expect(formatNumber(1000)).toBe('1.000');
    expect(formatNumber(0)).toBe('0');
  });

  it('uses a comma for decimals and strips trailing zeros', () => {
    expect(formatNumber(1.2845)).toBe('1,28');
    expect(formatNumber(2.5)).toBe('2,5');
    expect(formatNumber(2.5, { minFractionDigits: 2 })).toBe('2,50');
    expect(formatNumber(1234.5678, { maxFractionDigits: 3 })).toBe('1.234,568');
  });

  it('accepts numeric strings as Postgres returns them', () => {
    expect(formatNumber('1284560000.00')).toBe('1.284.560.000');
    expect(formatNumber('1200.50')).toBe('1.200,5');
    expect(formatNumber('  42 ')).toBe('42');
  });

  it('handles negatives and rounding to zero', () => {
    expect(formatNumber(-1500)).toBe('-1.500');
    expect(formatNumber(-0.001)).toBe('0');
    expect(formatNumber(-0.004, { maxFractionDigits: 2 })).toBe('0');
  });

  it('renders a dash for nothing', () => {
    expect(formatNumber(null)).toBe('—');
    expect(formatNumber(undefined)).toBe('—');
    expect(formatNumber('abc')).toBe('—');
    expect(formatNumber(NaN)).toBe('—');
    expect(formatNumber(Infinity)).toBe('—');
  });

  it('does not fall into exponent notation for very large values', () => {
    expect(formatNumber(1e21)).toBe('1.000.000.000.000.000.000.000');
    expect(formatNumber(BigInt(12345))).toBe('12.345');
  });
});

describe('formatCompact / formatCurrency', () => {
  it('abbreviates with US units', () => {
    expect(formatCompact(1284560000)).toBe('1,28B');
    expect(formatCompact(12500000)).toBe('12,5M');
    expect(formatCompact(3200)).toBe('3,2K');
    expect(formatCompact(950)).toBe('950');
    expect(formatCompact(-2_000_000)).toBe('-2M');
    expect(formatCompact(null)).toBe('—');
  });

  it('suffixes the currency sign', () => {
    expect(formatCurrency(1284560000)).toBe('1.284.560.000 ₫');
    expect(formatCurrency(1284560000, { compact: true })).toBe('1,28B ₫');
    expect(formatCurrency(1234.56)).toBe('1.235 ₫');
    expect(formatCurrency(10, { currency: 'USD' })).toBe('10 USD');
    expect(formatCurrency(undefined)).toBe('—');
  });

  it('formats ratios as percentages', () => {
    expect(formatPercent(0.125)).toBe('12,5%');
    expect(formatPercent(1)).toBe('100%');
    expect(formatPercent(0.33333, 2)).toBe('33,33%');
    expect(formatPercent(null)).toBe('—');
  });
});

describe('dates', () => {
  const d = new Date(2026, 5, 30, 14, 2, 9); // 06/30/2026 14:02:09 local

  it('uses dd/MM/yyyy and 24h time', () => {
    expect(formatDate(d)).toBe('30/06/2026');
    expect(formatTime(d)).toBe('14:02');
    expect(formatTime(d, true)).toBe('14:02:09');
    expect(formatDateTime(d)).toBe('30/06/2026 14:02');
  });

  it('accepts ISO strings and epoch numbers', () => {
    expect(formatDate('2026-01-05T00:00:00')).toBe('05/01/2026');
    expect(formatDate(d.getTime())).toBe('30/06/2026');
  });

  it('is a dash for invalid input', () => {
    expect(formatDate('not a date')).toBe('—');
    expect(formatDate(null)).toBe('—');
    expect(formatTime('')).toBe('—');
    expect(formatDateTime(undefined)).toBe('—');
    expect(toDate('x')).toBeNull();
  });

  it('starts the week on Monday', () => {
    expect(WEEK_STARTS_ON).toBe(1);
  });

  it('describes relative time in English', () => {
    const now = new Date(2026, 5, 30, 12, 0, 0);
    expect(formatRelative(new Date(2026, 5, 30, 11, 59, 40), now)).toBe('just now');
    expect(formatRelative(new Date(2026, 5, 30, 11, 45), now)).toBe('15m ago');
    expect(formatRelative(new Date(2026, 5, 30, 9, 0), now)).toBe('3h ago');
    expect(formatRelative(new Date(2026, 5, 28, 9, 0), now)).toBe('2d ago');
    expect(formatRelative(new Date(2026, 5, 1), now)).toBe('01/06/2026');
    expect(formatRelative(null, now)).toBe('—');
  });
});

describe('formatDuration', () => {
  it('scales units', () => {
    expect(formatDuration(850)).toBe('850 ms');
    expect(formatDuration(3200)).toBe('3,2s');
    expect(formatDuration(72000)).toBe('1m 12s');
    expect(formatDuration(120000)).toBe('2m');
    expect(formatDuration(-1)).toBe('—');
    expect(formatDuration(null)).toBe('—');
  });
});

describe('formatCell', () => {
  it('formats by runtime type', () => {
    expect(formatCell(null)).toBe('—');
    expect(formatCell(undefined)).toBe('—');
    expect(formatCell(true)).toBe('Yes');
    expect(formatCell(false)).toBe('No');
    expect(formatCell(1284560000)).toBe('1.284.560.000');
    expect(formatCell('1284560000.00')).toBe('1.284.560.000');
    expect(formatCell('2026-06-30')).toBe('30/06/2026');
    expect(formatCell('2026-06-30T14:02:00')).toBe('30/06/2026 14:02');
    expect(formatCell('Northern Region')).toBe('Northern Region');
    expect(formatCell(new Date(2026, 0, 2, 3, 4))).toBe('02/01/2026 03:04');
    expect(formatCell({ a: 1 })).toBe('{"a":1}');
  });

  it('does not treat order codes as numbers', () => {
    expect(formatCell('SO-001')).toBe('SO-001');
    expect(toNumber('12abc')).toBeNull();
  });
});

describe('isNumericColumn', () => {
  it('detects numeric columns including numeric strings and nulls', () => {
    expect(isNumericColumn([1, '2.5', null])).toBe(true);
    expect(isNumericColumn(['a', 1])).toBe(false);
    expect(isNumericColumn([null, undefined])).toBe(false);
    expect(isNumericColumn([])).toBe(false);
  });
});

describe('formatElapsed', () => {
  it('says how long something waited in Vietnamese', () => {
    expect(formatElapsed(30)).toBe('vừa xong');
    expect(formatElapsed(360)).toBe('6 phút');
    expect(formatElapsed(3600)).toBe('1 giờ');
    expect(formatElapsed(7500)).toBe('2 giờ 05 phút');
    expect(formatElapsed(270000)).toBe('3 ngày');
  });

  it('shows a dash for nothing to measure', () => {
    expect(formatElapsed(null)).toBe('—');
    expect(formatElapsed(-1)).toBe('—');
  });
});
