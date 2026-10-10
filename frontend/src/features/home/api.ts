/* Questions asked on Phân tích, kept per viewer by AskDataPage (same key). */
const HISTORY_KEY = 'dataagent.ask.history';

export function recentQuestions(limit: number): string[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(list)) return [];
    return list
      .map((item) =>
        item && typeof item === 'object' && 'q' in item ? String((item as { q: unknown }).q) : '',
      )
      .filter(Boolean)
      .slice(0, limit);
  } catch {
    return [];
  }
}
