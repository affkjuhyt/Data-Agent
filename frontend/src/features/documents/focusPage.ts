/*
 * The page a citation points at, handed from the screen that cites it to the document viewer.
 * The route carries only the document id (/data/document/<id>), so "open at page 14" is kept
 * here until the viewer for that document takes it, once.
 */
const pending = new Map<string, number>();

export function requestPage(documentId: string, page: number | null | undefined): void {
  if (page && page > 0) pending.set(documentId, page);
  else pending.delete(documentId);
}

export function takePage(documentId: string): number | undefined {
  const page = pending.get(documentId);
  pending.delete(documentId);
  return page;
}
