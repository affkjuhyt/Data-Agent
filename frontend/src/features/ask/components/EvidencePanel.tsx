/**
 * Right-hand panel of the Ask screen: the long form of "where did this
 * number come from" for one answer — how it was DEFINED (metric contract),
 * which QUERY ran, which TABLES and how fresh, and any RISKS. Plus the
 * document citations. No data of its own: it renders the message's frames.
 */
import React from 'react';
import { AlertTriangle, Database, FileText, Gauge, ShieldCheck, Sigma } from 'lucide-react';
import { formatDateTime, formatDuration, formatNumber, formatTime } from '../../../lib/formatVN';
import { CITATION_TAGS, FRESHNESS_LABELS, GENERATED_BY_LABELS, TRUNCATION_LABELS } from '../labels';
import type { AskMessage, EvidenceRun } from '../types';
import { splitMetricRef } from './evidenceSummary';
import { Chip, CopyButton, SqlBlock } from './EvidenceStrip';

function Section({ icon: Icon, title, children }: { icon: React.ComponentType<{ size?: number; style?: React.CSSProperties }>; title: string; children: React.ReactNode }) {
  return (
    <section style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <Icon size={12} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ab-muted)' }}>{title}</span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--ab-fg-2)', lineHeight: 1.55 }}>{children}</div>
    </section>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  if (v === null || v === undefined || v === '') return null;
  return (
    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
      <span style={{ minWidth: 92, fontSize: 11, color: 'var(--ab-muted)' }}>{k}</span>
      <span style={{ flex: 1, minWidth: 0, wordBreak: 'break-word' }}>{v}</span>
    </div>
  );
}

function RunDetail({ run, index, total }: { run: EvidenceRun; index: number; total: number }) {
  const metric = run.metric;
  const { key, version } = splitMetricRef(metric?.metric);
  const prov = run.provenance;
  const fresh = prov?.status ? FRESHNESS_LABELS[prov.status] ?? { label: prov.status, tone: 'warn' as const } : null;
  const findings = run.aggregation?.findings ?? [];
  const filters = Array.isArray(metric?.filters) ? metric!.filters!.map(f => (typeof f === 'string' ? f : JSON.stringify(f))) : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14, paddingTop: index ? 12 : 0, borderTop: index ? '1px dashed var(--ab-border)' : 'none' }}>
      {total > 1 && <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--ab-accent)' }}>Query {index + 1} / {total}</div>}

      <Section icon={Sigma} title="How it's calculated">
        {run.generated_by === 'semantic_layer' && metric ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 600, color: 'var(--ab-fg)' }}>{key}</span>
              {version && <Chip label={`v${version} · approved`} tone="ok" />}
            </div>
            <Row k="Owner" v={metric.owner} />
            <Row k="Definition" v={metric.definition} />
            <Row k="Calculation" v={metric.aggregation ? `${metric.aggregation.toUpperCase()}${metric.unit ? ` · unit ${metric.unit}` : ''}` : null} />
            <Row k="Matched via" v={metric.matched_term ? <>synonym "{metric.matched_term}"</> : null} />
            <Row k="Dimensions" v={metric.dimensions?.length ? metric.dimensions.join(', ') : null} />
            <Row k="Filters" v={filters.length ? filters.join(' · ') : 'none'} />
            <Row k="Date column" v={metric.business_date_column} />
            <Row k="Grain" v={metric.grain} />
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            <Chip label={GENERATED_BY_LABELS[run.generated_by] ?? run.generated_by} tone="warn" />
            <span style={{ fontSize: 11, color: 'var(--ab-muted)' }}>
              The question didn't match any approved metric, so the model wrote its own SQL. The statement still goes through the read-only guard, a row limit, and an access log. Register a metric for this question so it returns the same number next time.
            </span>
          </div>
        )}
      </Section>

      <Section icon={Database} title="Query">
        <Row k="Database" v={run.db_name ? `${run.db_name}${run.db_type ? ` (${run.db_type})` : ''}` : null} />
        <Row k="Row count" v={run.row_count !== null && run.row_count !== undefined ? formatNumber(run.row_count) : null} />
        <Row k="Executed via" v="sandbox_executor · READ ONLY · logged" />
        <SqlBlock sql={run.sql} />
        <div style={{ marginTop: 6 }}><CopyButton text={run.sql} label="Copy SQL" /></div>
      </Section>

      <Section icon={Gauge} title="Data source">
        {fresh && (
          <div style={{ marginBottom: 4 }}>
            <Chip label={fresh.label} tone={fresh.tone} />
          </div>
        )}
        <Row k="Data as of" v={prov?.as_of ? formatDateTime(prov.as_of) : prov ? 'not measured' : null} />
        <Row k="Data age" v={typeof prov?.age_seconds === 'number' ? formatDuration(prov.age_seconds * 1000) : null} />
        <Row k="Classification" v={prov?.classification} />
        <Row k="Data owner" v={prov?.owners?.length ? prov.owners.join(', ') : null} />
        {prov?.tables?.length ? (
          <div style={{ marginTop: 4, display: 'flex', flexDirection: 'column', gap: 3 }}>
            {prov.tables.map(t => (
              <div key={t.table} style={{ display: 'flex', gap: 6, alignItems: 'baseline', fontSize: 11 }}>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ab-fg)' }}>{t.table}</span>
                {t.owner && <span style={{ color: 'var(--ab-muted)' }}>· {t.owner}</span>}
                {t.data_as_of && <span style={{ color: 'var(--ab-muted)', marginLeft: 'auto', whiteSpace: 'nowrap' }}>{formatDateTime(t.data_as_of)}</span>}
              </div>
            ))}
          </div>
        ) : !prov ? (
          <span style={{ fontSize: 11, color: 'var(--ab-muted)' }}>No governance info for these tables yet — the warehouse hasn't been crawled.</span>
        ) : null}
        {prov?.stale_tables?.length ? <Row k="Stale" v={prov.stale_tables.join(', ')} /> : null}
        {prov?.unknown_tables?.length ? <Row k="Unmeasured" v={prov.unknown_tables.join(', ')} /> : null}
      </Section>

      {(findings.length > 0 || run.truncation?.reason || run.rows_capped) && (
        <Section icon={AlertTriangle} title="Warnings">
          <ul style={{ margin: 0, paddingLeft: 16, display: 'flex', flexDirection: 'column', gap: 3 }}>
            {findings.map((f, i) => (
              <li key={i} style={{ color: 'var(--ab-bad)' }}>
                {f.detail}{f.expression ? <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--ab-muted)' }}> — {f.expression}</span> : null}
              </li>
            ))}
            {run.truncation?.reason && (
              <li style={{ color: 'var(--ab-warn)' }}>
                Incomplete result: {TRUNCATION_LABELS[run.truncation.reason] ?? run.truncation.reason}
                {run.truncation.rows_returned ? ` (${formatNumber(run.truncation.rows_returned)} rows)` : ''}.
              </li>
            )}
            {run.rows_capped && <li style={{ color: 'var(--ab-muted)' }}>The table below only previews the first {formatNumber(run.rows.length)} rows.</li>}
          </ul>
        </Section>
      )}
    </div>
  );
}

export function EvidencePanel({ message }: { message: AskMessage | null }) {
  const ev = message?.evidence;
  const citations = message?.citations ?? [];

  if (!message) {
    return (
      <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--ab-muted)', fontSize: 12, lineHeight: 1.6 }}>
        <ShieldCheck size={22} style={{ margin: '0 auto 8px', opacity: 0.4 }} />
        Ask a question to see where the number came from: which metric, which SQL, how many rows, how fresh the data is.
      </div>
    );
  }

  const hasRuns = !!ev?.runs?.length;

  if (message.status === 'streaming' && !ev) {
    return (
      <div style={{ padding: '28px 16px', textAlign: 'center', color: 'var(--ab-muted)', fontSize: 12, lineHeight: 1.6 }}>
        Running… evidence (metric, SQL, row count) will appear once the answer is complete.
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ fontSize: 11, color: 'var(--ab-muted)' }}>
        Answered at {formatTime(message.timestamp)}{message.latencyMs ? ` · ${formatDuration(message.latencyMs)}` : ''}
      </div>

      {hasRuns ? (
        ev!.runs.map((run, i) => <RunDetail key={run.id ?? i} run={run} index={i} total={ev!.runs.length} />)
      ) : (
        <div style={{ fontSize: 12, color: 'var(--ab-muted)', lineHeight: 1.6 }}>
          {ev?.clarifications?.length
            ? 'No query has run yet — the system is waiting for you to clarify the question.'
            : "This answer wasn't read from the warehouse (it answers from documents or conversation), so there's no SQL to show."}
        </div>
      )}

      {ev?.warnings?.some(w => w.kind === 'assumption') ? (
        <Section icon={AlertTriangle} title="Assumptions">
          {ev.warnings.filter(w => w.kind === 'assumption').map((w, i) => <div key={i} style={{ color: 'var(--ab-muted)' }}>{w.detail}</div>)}
        </Section>
      ) : null}

      {ev?.warnings?.some(w => w.kind !== 'assumption') ? (
        <Section icon={AlertTriangle} title="Unit warnings">
          {ev.warnings.filter(w => w.kind !== 'assumption').map((w, i) => <div key={i} style={{ color: 'var(--ab-bad)' }}>{w.detail}</div>)}
        </Section>
      ) : null}

      {citations.length > 0 && (
        <Section icon={FileText} title="Sources">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {citations.map(c => (
              <div key={c.index} style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                <span style={{ flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 700, color: 'var(--ab-accent)' }}>[{c.index}]</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--ab-fg)', wordBreak: 'break-word' }}>
                    {c.source} <span style={{ fontSize: 9.5, fontWeight: 500, color: 'var(--ab-muted)' }}>· {CITATION_TAGS[c.type] ?? c.type}</span>
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--ab-muted)', overflow: 'hidden', display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}>{c.snippet}</div>
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}
