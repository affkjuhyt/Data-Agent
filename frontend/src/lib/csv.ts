/*
 * One CSV field, quoted. Text that starts with = + - @ tab or CR is run as a formula when the
 * file is opened in Excel or Sheets (CSV injection), and exported cells come from the server or
 * the model, so such text gets a leading apostrophe. Numbers stay numbers: a negative value is
 * data, not a formula.
 */
const FORMULA_START = /^[=+\-@\t\r]/;

export function csvCell(value: unknown): string {
  if (value === null || value === undefined) return '""';
  if (typeof value === 'number' || typeof value === 'bigint') return `"${String(value)}"`;
  let text: string;
  if (typeof value === 'object') {
    try {
      text = JSON.stringify(value);
    } catch {
      text = String(value);
    }
  } else {
    text = String(value);
  }
  const safe = FORMULA_START.test(text) ? `'${text}` : text;
  return `"${safe.replace(/"/g, '""')}"`;
}
