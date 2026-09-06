import { describe, expect, it } from 'vitest';
import { csvFileName, toCsv } from './exportCsv';

describe('toCsv', () => {
  it('writes a BOM, quoted fields and CRLF', () => {
    const csv = toCsv(['region', 'revenue'], [
      { region: 'North — Zürich', revenue: 1200 },
      { region: 'South "Bay"', revenue: null },
    ]);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv.slice(1)).toBe('"region","revenue"\r\n"North — Zürich","1200"\r\n"South ""Bay""",""\r\n');
  });

  it('keeps raw values so numbers stay numbers in Excel', () => {
    const csv = toCsv(['n'], [{ n: '1284560000.00' }, { n: { a: 1 } }]);
    expect(csv).toContain('"1284560000.00"');
    expect(csv).toContain('"{""a"":1}"');
  });
});

describe('csvFileName', () => {
  it('slugs non-ASCII text and stamps the date', () => {
    expect(csvFileName('Net revenue for June by region', new Date(2026, 5, 30)))
      .toBe('net-revenue-for-june-by-region-20260630.csv');
    expect(csvFileName('   ', new Date(2026, 0, 1))).toBe('result-20260101.csv');
    expect(csvFileName('đơn hàng Đã huỷ', new Date(2026, 0, 1))).toBe('don-hang-da-huy-20260101.csv');
  });
});
