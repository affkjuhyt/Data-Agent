import { listConversations } from '../../lib/v1/questions';

/** Recent Ask conversation titles for the home bar (server-backed, R1-13). */
export async function recentQuestions(limit: number): Promise<string[]> {
  try {
    const page = await listConversations(limit);
    return page.items
      .map((c) => c.title?.trim() || '')
      .filter(Boolean)
      .slice(0, limit);
  } catch {
    return [];
  }
}
