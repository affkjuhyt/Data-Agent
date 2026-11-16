import { listConversations } from '../../lib/v1/questions';

/** Recent questions, for the home ask bar's chips: each question once (E5). The same question
 *  asked in several conversations filled all four chips with it; more are read so that `limit`
 *  different ones remain. */
export async function recentQuestions(limit: number): Promise<string[]> {
  try {
    const page = await listConversations(limit * 5);
    const seen = new Set<string>();
    const out: string[] = [];
    for (const c of page.items) {
      const title = c.title?.trim() || '';
      const key = title.toLocaleLowerCase('vi').replace(/[.?!\s]+$/, '');
      if (!title || seen.has(key)) continue;
      seen.add(key);
      out.push(title);
      if (out.length === limit) break;
    }
    return out;
  } catch {
    return [];
  }
}
