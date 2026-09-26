import { describe, expect, it } from 'vitest';
import { nonEmptyCellCount, pickHeaderRowIndex } from './inventory.data';

describe('pickHeaderRowIndex', () => {
  it('skips a single-cell title row and picks the real header row', () => {
    const rows: (string | number)[][] = [
      ['1. Sản xuất nông nghiệp đến ngày 20 tháng 8 năm 2026'],
      [],
      ['', '', '', '', 'Nghìn ha', ''],
      ['', '', 'Thực hiện cùng', 'Thực hiện', 'Thực hiện kỳ này'],
      ['Lúa', 100, 90, 95, 5.5],
    ];
    expect(pickHeaderRowIndex(rows)).toBe(3);
  });

  it('uses row 0 when it already has 2+ populated cells', () => {
    const rows = [['id', 'name', 'value'], [1, 'a', 10]];
    expect(pickHeaderRowIndex(rows)).toBe(0);
  });

  it('falls back to row 0 when no row ever has 2+ populated cells', () => {
    const rows = [['Title'], [''], ['Only one column'], ['x']];
    expect(pickHeaderRowIndex(rows)).toBe(0);
  });

  it('returns 0 for an empty sheet', () => {
    expect(pickHeaderRowIndex([])).toBe(0);
  });
});

describe('nonEmptyCellCount', () => {
  it('counts cells that are not blank, undefined, or null', () => {
    expect(nonEmptyCellCount(['a', '', undefined, null, 'b'])).toBe(2);
  });

  it('treats whitespace-only cells as empty', () => {
    expect(nonEmptyCellCount(['  ', 'x'])).toBe(1);
  });

  it('returns 0 for undefined', () => {
    expect(nonEmptyCellCount(undefined)).toBe(0);
  });
});
