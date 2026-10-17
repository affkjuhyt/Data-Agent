import type { Citation } from '../types';
import { CITATION_TAGS } from '../labels';

/** "Nguồn": the documents and tools an answer cites, numbered like the [n] markers in the text. */
export function CitationPanel({ citations, onOpen }: { citations: Citation[]; onOpen?: (citation: Citation) => void }) {
  if (!citations.length) return null;
  return (
    <section aria-label="Nguồn" className="flex flex-col gap-da-xs">
      <span className="text-muted-foreground da-caption">Nguồn</span>
      <ol className="m-0 flex list-none flex-col gap-da-sm p-0">
        {citations.map((c, i) => {
          const href = c.url || (c.type === 'kb' && c.doc_id ? `/api/knowledge/documents/${c.doc_id}/file` : null);
          return (
            <li key={i} className="flex items-start gap-da-md">
              <span className="mt-px inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-sm bg-accent px-da-xs text-primary da-label">
                {c.index}
              </span>
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="flex flex-wrap items-baseline gap-da-sm">
                  {onOpen && c.type === 'document' && c.doc_id ? (
                    <button type="button" onClick={() => onOpen(c)} className="cursor-pointer text-left text-primary hover:underline da-body-strong">{c.source}</button>
                  ) : href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary hover:underline da-body-strong">{c.source}</a>
                  ) : (
                    <span className="text-foreground da-body-strong">{c.source}</span>
                  )}
                  <span className="text-muted-foreground da-caption">{CITATION_TAGS[c.type] ?? c.type}</span>
                  {typeof c.score === 'number' && (
                    <span className="ml-auto text-muted-foreground da-mono">{c.score.toFixed(2)}</span>
                  )}
                </div>
                <p className="m-0 line-clamp-2 text-muted-foreground da-caption">{c.snippet}</p>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
