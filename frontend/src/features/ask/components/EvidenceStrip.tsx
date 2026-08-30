/**
 * "Vì sao tin được" — one line under the answer: which approved metric (or
 * model-written SQL), how many rows, how many tables, how fresh, any risk —
 * and a "Xem SQL" toggle. Everything comes from the `__evidence` frame.
 */
import React, { useState } from 'react';
import { Check, ChevronDown, ChevronUp, Code2, Copy, ShieldCheck } from 'lucide-react';
import type { Evidence } from '../types';
import { runChips, type Tone } from './evidenceSummary';

const TONE_STYLE: Record<Tone, React.CSSProperties> = {
  ok:      { background: 'var(--ab-ok-bg)',   color: 'var(--ab-ok)',   borderColor: 'transparent' },
  warn:    { background: 'var(--ab-warn-bg)', color: 'var(--ab-warn)', borderColor: 'transparent' },
  bad:     { background: 'var(--ab-bad-bg)',  color: 'var(--ab-bad)',  borderColor: 'transparent' },
  neutral: { background: 'var(--ab-bg-2)',    color: 'var(--ab-fg-2)', borderColor: 'var(--ab-border)' },
};

export function Chip({ label, tone, title }: { label: string; tone: Tone; title?: string }) {
  return (
    <span
      title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 8px', borderRadius: 9999,
        fontSize: 10.5, fontWeight: 600, border: '1px solid', whiteSpace: 'nowrap', ...TONE_STYLE[tone],
      }}
    >
      {label}
    </span>
  );
}

export function CopyButton({ text, label = 'Sao chép' }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(text);
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    } catch { /* clipboard unavailable */ }
  };
  return (
    <button type="button" className="ab-btn" onClick={copy} title={label}>
      {done ? <Check size={11} /> : <Copy size={11} />} {done ? 'Đã chép' : label}
    </button>
  );
}

export function SqlBlock({ sql }: { sql: string }) {
  return (
    <pre
      data-testid="sql-block"
      style={{
        margin: '6px 0 0', padding: '10px 12px', borderRadius: 8, fontSize: 11.5, lineHeight: 1.6,
        fontFamily: 'var(--font-mono)', background: 'var(--ab-code-bg)', color: 'var(--ab-fg-2)',
        border: '1px solid var(--ab-border)', overflowX: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}
    >
      {sql}
    </pre>
  );
}

export function EvidenceStrip({ evidence, onOpenPanel }: { evidence: Evidence; onOpenPanel?: () => void }) {
  const [openSql, setOpenSql] = useState<number | null>(null);
  if (!evidence.runs.length && !evidence.warnings.length) return null;

  return (
    <div data-testid="evidence-strip" style={{ marginTop: 10, padding: '8px 10px', borderRadius: 10, background: 'var(--ab-bg)', border: '1px solid var(--ab-border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <ShieldCheck size={12} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: 'var(--ab-muted)' }}>Vì sao tin được</span>
        {onOpenPanel && (
          <button type="button" className="ab-link" style={{ marginLeft: 'auto' }} onClick={onOpenPanel}>Chi tiết ›</button>
        )}
      </div>

      {evidence.runs.map((run, i) => (
        <div key={run.id ?? i} style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: i ? 6 : 0, borderTop: i ? '1px dashed var(--ab-border)' : 'none' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {runChips(run).map(({ key, ...c }) => <Chip key={key} {...c} />)}
            <button
              type="button"
              className="ab-btn"
              aria-expanded={openSql === i}
              onClick={() => setOpenSql(openSql === i ? null : i)}
              style={{ marginLeft: 'auto' }}
            >
              <Code2 size={11} /> {openSql === i ? 'Ẩn SQL' : 'Xem SQL'} {openSql === i ? <ChevronUp size={10} /> : <ChevronDown size={10} />}
            </button>
          </div>
          {openSql === i && (
            <div>
              <SqlBlock sql={run.sql} />
              <div style={{ display: 'flex', gap: 6, marginTop: 6, alignItems: 'center' }}>
                <CopyButton text={run.sql} label="Sao chép SQL" />
                <span style={{ fontSize: 10.5, color: 'var(--ab-muted)' }}>Đã qua guard chỉ-đọc · ghi nhật ký truy cập</span>
              </div>
            </div>
          )}
        </div>
      ))}

      {evidence.warnings.map((w, i) => (
        <div key={i} style={{ marginTop: 6, fontSize: 11, color: 'var(--ab-bad)' }}>⚠ {w.detail}</div>
      ))}
    </div>
  );
}
