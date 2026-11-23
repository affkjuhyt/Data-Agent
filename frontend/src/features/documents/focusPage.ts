/*
 * The page a citation points at, handed from the screen that cites it to the document viewer.
 * The route carries only the document id (/data/document/<id>), so "open at page 14" is kept
 * here until the viewer for that document takes it, once. A citation to a table cell (DI-12)
 * also hands the cell's box, which the viewer outlines on that page.
 */
import type { PageBox } from '../../lib/v1/documents';

export interface Focus {
  page: number;
  box?: PageBox;
}

const pending = new Map<string, Focus>();

export function requestPage(
  documentId: string,
  page: number | null | undefined,
  box?: PageBox | null,
): void {
  if (page && page > 0) pending.set(documentId, box ? { page, box } : { page });
  else pending.delete(documentId);
}

export function takeFocus(documentId: string): Focus | undefined {
  const focus = pending.get(documentId);
  pending.delete(documentId);
  return focus;
}

export function takePage(documentId: string): number | undefined {
  return takeFocus(documentId)?.page;
}
