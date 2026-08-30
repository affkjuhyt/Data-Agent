import type { Citation } from '../types';
import { CITATION_COLORS, useAnswerTheme } from './AnswerTheme';
import { CITATION_TAGS } from '../labels';

export function CitationPanel({ citations }: { citations: Citation[] }) {
  const mode = useAnswerTheme();
  const colors = CITATION_COLORS[mode];
  if (!citations.length) return null;
  return (
    <div style={{ marginTop: 8, padding: '10px 12px', background: 'var(--ab-bg)', border: '1px solid var(--ab-border)', borderRadius: 10 }}>
      <div style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.02em', textTransform: 'uppercase', color: 'var(--ab-muted)', marginBottom: 8 }}>
        📎 Nguồn tham khảo
      </div>
      {citations.map((c, i) => {
        const color = colors[c.type] ?? colors.kb;
        const href = c.url || (c.type === 'kb' && c.doc_id ? `/api/knowledge/documents/${c.doc_id}/file` : null);
        return (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', padding: '6px 0', borderTop: i === 0 ? 'none' : '1px solid var(--ab-border)' }}>
            <div style={{
              flexShrink: 0, width: 16, height: 16, borderRadius: 4, marginTop: 1,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 9.5, fontWeight: 700, fontFamily: 'var(--font-mono)',
              background: `${color}29`, color,
            }}>
              {c.index}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: 6, flexWrap: 'wrap' }}>
                {href ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" style={{ fontSize: 12, fontWeight: 600, color, textDecoration: 'none' }}>{c.source}</a>
                ) : (
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--ab-fg)' }}>{c.source}</span>
                )}
                <span style={{ fontSize: 9, padding: '1px 5px', borderRadius: 4, background: 'var(--ab-bg-2)', color: 'var(--ab-muted)', letterSpacing: '0.03em' }}>
                  {CITATION_TAGS[c.type] ?? c.type}
                </span>
                {typeof c.score === 'number' && (
                  <span style={{ fontSize: 9.5, color: 'var(--ab-muted)', fontFamily: 'var(--font-mono)', marginLeft: 'auto' }}>{c.score.toFixed(2)}</span>
                )}
              </div>
              <div style={{
                fontSize: 11, color: 'var(--ab-muted)', marginTop: 2, lineHeight: 1.5,
                overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
              }}>
                {c.snippet}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
