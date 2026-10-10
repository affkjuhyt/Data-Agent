import { describe, expect, it } from 'vitest';
import { csvCell } from './csv';

describe('csvCell', () => {
  it('quotes and escapes plain text', () => {
    expect(csvCell('a "b"')).toBe('"a ""b"""');
  });

  it('neutralises text that a spreadsheet would run as a formula', () => {
    for (const text of ['=HYPERLINK("x")', '+1', '-2+3', '@SUM(A1)', '\tx', '\rx']) {
      expect(csvCell(text).startsWith(`"'`)).toBe(true);
    }
  });

  it('keeps numbers, including negative ones, as numbers', () => {
    expect(csvCell(-5)).toBe('"-5"');
    expect(csvCell(1200.5)).toBe('"1200.5"');
  });

  it('writes null as an empty field and objects as JSON', () => {
    expect(csvCell(null)).toBe('""');
    expect(csvCell({ a: 1 })).toBe('"{""a"":1}"');
  });
});
