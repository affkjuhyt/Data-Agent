import { describe, expect, it } from 'vitest';
import { csvFileName, toCsv } from './exportCsv';

describe('toCsv', () => {
  it('writes a BOM, quoted fields and CRLF', () => {
    const csv = toCsv(['vùng', 'doanh_thu'], [
      { vùng: 'Miền Bắc', doanh_thu: 1200 },
      { vùng: 'Miền "Nam"', doanh_thu: null },
    ]);
    expect(csv.charCodeAt(0)).toBe(0xfeff);
    expect(csv.slice(1)).toBe('"vùng","doanh_thu"\r\n"Miền Bắc","1200"\r\n"Miền ""Nam""",""\r\n');
  });

  it('keeps raw values so numbers stay numbers in Excel', () => {
    const csv = toCsv(['n'], [{ n: '1284560000.00' }, { n: { a: 1 } }]);
    expect(csv).toContain('"1284560000.00"');
    expect(csv).toContain('"{""a"":1}"');
  });
});

describe('csvFileName', () => {
  it('slugs Vietnamese and stamps the date', () => {
    expect(csvFileName('Doanh thu thuần tháng 6 theo vùng', new Date(2026, 5, 30)))
      .toBe('doanh-thu-thuan-thang-6-theo-vung-20260630.csv');
    expect(csvFileName('   ', new Date(2026, 0, 1))).toBe('ket-qua-20260101.csv');
    expect(csvFileName('đơn hàng Đã huỷ', new Date(2026, 0, 1))).toBe('don-hang-da-huy-20260101.csv');
  });
});
