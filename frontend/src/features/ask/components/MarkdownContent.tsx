/**
 * Minimal markdown renderer for answer text: headings, lists, fenced code,
 * bold, inline code, links and `[n]` citation markers. Deliberately small —
 * the answer is prose plus numbers, not a document. Prose is DA/Reading, as in
 * "P01 · Hỏi đáp" (2296:18565).
 */
import React from 'react';
import type { Citation } from '../types';

const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[\d+\])/g;

function InlineText({ text, citations }: { text: string; citations?: Citation[] }) {
  return (
    <>
      {text.split(INLINE_RE).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**'))
          return <strong key={i} className="font-semibold">{part.slice(2, -2)}</strong>;
        if (part.startsWith('`') && part.endsWith('`'))
          return <code key={i} className="rounded-sm bg-subtle px-da-xs da-mono">{part.slice(1, -1)}</code>;
        const cite = part.match(/^\[(\d+)\]$/);
        if (cite && citations) {
          const c = citations.find(x => x.index === Number(cite[1]));
          if (c) {
            const scoreStr = typeof c.score === 'number' ? ` · điểm ${c.score.toFixed(2)}` : '';
            return (
              <sup
                key={i}
                title={`${c.source}${scoreStr}\n${c.snippet}`}
                className="ml-px inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent px-da-xs align-[2px] text-primary da-label"
              >
                {cite[1]}
              </sup>
            );
          }
        }
        const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        if (link)
          return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" className="text-primary underline">{link[1]}</a>;
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

export function MarkdownContent({ text, citations }: { text: string; citations?: Citation[] }) {
  const lines = text.split('\n');
  const nodes: React.ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith('```')) {
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```')) { codeLines.push(lines[i]); i++; }
      i++;
      nodes.push(
        <pre key={`cb${i}`} className="m-0 overflow-x-auto whitespace-pre rounded-md border border-border bg-subtle px-da-lg py-da-md text-foreground da-mono">
          {codeLines.join('\n')}
        </pre>,
      );
      continue;
    }
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const lvl = hm[1].length;
      nodes.push(
        <p key={`h${i}`} className={lvl <= 2 ? 'mt-da-md da-heading-2' : 'da-heading-3'}>
          <InlineText text={hm[2]} citations={citations} />
        </p>,
      );
      i++; continue;
    }
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s/, '')); i++; }
      nodes.push(
        <ul key={`ul${i}`} className="m-0 list-disc pl-da-2xl da-reading">
          {items.map((item, j) => <li key={j}><InlineText text={item} citations={citations} /></li>)}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s/, '')); i++; }
      nodes.push(
        <ol key={`ol${i}`} className="m-0 list-decimal pl-da-2xl da-reading">
          {items.map((item, j) => <li key={j}><InlineText text={item} citations={citations} /></li>)}
        </ol>,
      );
      continue;
    }
    if (!line.trim()) { i++; continue; }
    nodes.push(<p key={`p${i}`} className="m-0 da-reading"><InlineText text={line} citations={citations} /></p>);
    i++;
  }
  return <div className="flex flex-col gap-da-sm text-foreground">{nodes}</div>;
}
