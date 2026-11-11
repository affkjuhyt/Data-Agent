/**
 * Text as a search compares it: no case, no marks, "đ" read as "d" — so "chi nhanh" finds
 * "Chi nhánh" and "Da Nang" finds "Đà Nẵng", as libs/language.fold does on the server.
 * Intl.Collator's base sensitivity (React Aria's useFilter) ignores the marks but keeps "đ" a
 * letter of its own, so it cannot stand in here.
 */
export function fold(text: string): string {
  return text
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .replace(/đ/g, 'd')
    .replace(/Đ/g, 'D')
    .toLowerCase();
}

/** Whether `query` occurs in `text`, folded both. */
export function folds(text: string, query: string): boolean {
  return fold(text).includes(fold(query.trim()));
}
