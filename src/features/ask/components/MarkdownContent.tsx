/**
 * Minimal markdown renderer for answer text: headings, lists, fenced code,
 * bold, inline code, links and `[n]` citation markers. Deliberately small —
 * the answer is prose plus numbers, not a document.
 */
import React from 'react';
import type { Citation } from '../types';
import { CITATION_COLORS, useAnswerTheme } from './AnswerTheme';

const INLINE_RE = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\)|\[\d+\])/g;

export function InlineText({ text, citations }: { text: string; citations?: Citation[] }) {
  const mode = useAnswerTheme();
  const colors = CITATION_COLORS[mode];
  return (
    <>
      {text.split(INLINE_RE).map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**'))
          return <strong key={i} style={{ fontWeight: 600, color: 'var(--ab-fg)' }}>{part.slice(2, -2)}</strong>;
        if (part.startsWith('`') && part.endsWith('`'))
          return (
            <code key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '0.85em', background: 'var(--ab-code-bg)', padding: '1px 5px', borderRadius: 4, color: 'var(--ab-accent)' }}>
              {part.slice(1, -1)}
            </code>
          );
        const cite = part.match(/^\[(\d+)\]$/);
        if (cite && citations) {
          const c = citations.find(x => x.index === Number(cite[1]));
          if (c) {
            const color = colors[c.type] ?? colors.kb;
            const scoreStr = typeof c.score === 'number' ? ` · score ${c.score.toFixed(2)}` : '';
            return (
              <sup
                key={i}
                title={`${c.source}${scoreStr}\n${c.snippet}`}
                style={{
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  minWidth: 15, height: 15, padding: '0 4px', marginLeft: 1,
                  borderRadius: 4, fontSize: 9.5, fontWeight: 700,
                  fontFamily: 'var(--font-mono)', verticalAlign: 2,
                  background: `${color}29`, color,
                }}
              >
                {cite[1]}
              </sup>
            );
          }
        }
        const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        if (link)
          return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--ab-link)', textDecoration: 'underline' }}>{link[1]}</a>;
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
        <pre key={`cb${i}`} style={{ background: 'var(--ab-code-bg)', border: '1px solid var(--ab-border)', borderRadius: 8, padding: '10px 14px', margin: '8px 0 4px', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--ab-fg-2)', overflowX: 'auto', lineHeight: 1.6, whiteSpace: 'pre' }}>
          {codeLines.join('\n')}
        </pre>,
      );
      continue;
    }
    const hm = line.match(/^(#{1,4})\s+(.+)/);
    if (hm) {
      const lvl = hm[1].length;
      const sz = ['18px', '16px', '14px', '13px'][lvl - 1] ?? '13px';
      nodes.push(<p key={`h${i}`} style={{ fontSize: sz, fontWeight: 700, color: 'var(--ab-fg)', margin: lvl <= 2 ? '12px 0 4px' : '8px 0 2px' }}><InlineText text={hm[2]} citations={citations} /></p>);
      i++; continue;
    }
    if (/^[-*]\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*]\s/.test(lines[i])) { items.push(lines[i].replace(/^[-*]\s/, '')); i++; }
      nodes.push(
        <ul key={`ul${i}`} style={{ margin: '4px 0', paddingLeft: 18 }}>
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'var(--ab-fg-2)' }}><InlineText text={item} citations={citations} /></li>)}
        </ul>,
      );
      continue;
    }
    if (/^\d+\.\s/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s/.test(lines[i])) { items.push(lines[i].replace(/^\d+\.\s/, '')); i++; }
      nodes.push(
        <ol key={`ol${i}`} style={{ margin: '4px 0', paddingLeft: 20 }}>
          {items.map((item, j) => <li key={j} style={{ margin: '2px 0', fontSize: 13, color: 'var(--ab-fg-2)' }}><InlineText text={item} citations={citations} /></li>)}
        </ol>,
      );
      continue;
    }
    if (!line.trim()) { i++; continue; }
    nodes.push(<p key={`p${i}`} style={{ margin: '3px 0', fontSize: 13, lineHeight: 1.55, color: 'var(--ab-fg-2)' }}><InlineText text={line} citations={citations} /></p>);
    i++;
  }
  return <>{nodes}</>;
}
