/**
 * "Cách tính" (P01 · Hỏi đáp · Cách tính một con số, 2296:18832): the right-hand panel of the
 * Ask screen, the long form of "where did this number come from" for one answer — the metric,
 * its formula, the data and how fresh it is, the query, any risks, then the citations.
 * Label/value pairs in DA/Caption over DA/Body Small, formulas in DA/Mono, as in the frame.
 *
 * The frame opens per number ("2,32% · DH Bắc Bộ, T9"); the stream has no per-number lineage
 * yet, so the panel explains the whole answer, one block per query result.
 */
import { useState, type ReactNode } from 'react';
import { Icon } from '../../../design-system/Icon';
import { StatusDot } from '../../../design-system/StatusDot';
import { formatDateTime, formatDuration, formatNumber, formatTime } from '../../../lib/formatVN';
import { CITATION_TAGS, FRESHNESS_LABELS, GENERATED_BY_LABELS, TRUNCATION_LABELS } from '../labels';
import { resultSetToEvidenceRun } from '../resultSets';
import { runTrust, trustLabel } from '../trust';
import type { AskMessage, EvidenceRun } from '../types';
import { splitMetricRef } from './evidenceSummary';
import { CopyButton, SqlBlock } from './EvidenceStrip';

function KV({ k, children, mono }: { k: string; children: ReactNode; mono?: boolean }) {
  if (children === null || children === undefined || children === '') return null;
  return (
    <div className="flex flex-col gap-[2px] pb-da-sm">
      <span className="text-muted-foreground da-caption">{k}</span>
      <div className={mono ? 'whitespace-pre-wrap break-words text-foreground da-mono' : 'break-words text-foreground da-body-small'}>
        {children}
      </div>
    </div>
  );
}

const FRESH_TONE = { ok: 'success', warn: 'warning', bad: 'error' } as const;

function RunDetail({ run, index, total }: { run: EvidenceRun; index: number; total: number }) {
  const [sqlOpen, setSqlOpen] = useState(false);
  const metric = run.metric;
  const { key, version } = splitMetricRef(metric?.metric);
  const prov = run.provenance;
  const fresh = prov?.status ? FRESHNESS_LABELS[prov.status] ?? { label: prov.status, tone: 'warn' as const } : null;
  const findings = run.aggregation?.findings ?? [];
  const filters = Array.isArray(metric?.filters) ? metric!.filters!.map(f => (typeof f === 'string' ? f : JSON.stringify(f))) : [];
  const level = runTrust(run);
  const governed = run.generated_by === 'semantic_layer' && metric;

  const formula = governed
    ? [
        metric.definition,
        metric.aggregation ? `${metric.aggregation.toUpperCase()}${metric.unit ? ` · đơn vị ${metric.unit}` : ''}` : null,
        filters.length ? `lọc: ${filters.join(', ')}` : null,
      ].filter(Boolean).join('\n')
    : '';

  const dataLine = [
    prov?.tables?.length ? prov.tables.map(t => t.table).join(', ') : null,
    prov?.as_of ? `chốt ${formatDateTime(prov.as_of)}` : null,
    prov?.owners?.length ? `phụ trách: ${prov.owners.join(', ')}` : null,
  ].filter(Boolean).join(' · ');

  return (
    <div className={index ? 'flex flex-col border-t border-border pt-da-lg' : 'flex flex-col'}>
      {total > 1 && <span className="pb-da-sm text-foreground da-body-strong">Kết quả {index + 1} / {total}</span>}

      <KV k="Nhãn tin cậy">
        <StatusDot tone={level === 'EXPLORATORY' ? 'warning' : 'success'}>{trustLabel(level)}</StatusDot>
      </KV>

      {governed ? (
        <>
          <KV k="Chỉ số">
            {key}{version ? ` v${version}` : ''} · đã duyệt{metric.owner ? `, người phụ trách ${metric.owner}` : ''}
            {metric.matched_term ? <span className="text-muted-foreground"> · khớp qua “{metric.matched_term}”</span> : null}
          </KV>
          <KV k="Công thức" mono>{formula || null}</KV>
          <KV k="Chiều">{metric.dimensions?.length ? metric.dimensions.join(', ') : null}</KV>
          <KV k="Độ chi tiết">{metric.grain}</KV>
          <KV k="Cột ngày">{metric.business_date_column}</KV>
        </>
      ) : (
        <KV k="Chỉ số">
          {GENERATED_BY_LABELS[run.generated_by] ?? run.generated_by}
          <p className="m-0 mt-da-xs text-muted-foreground da-caption">
            Câu hỏi không khớp chỉ số đã duyệt nào nên mô hình tự viết SQL. Câu lệnh vẫn qua lớp chặn chỉ-đọc,
            giới hạn số dòng và nhật ký truy cập. Đăng ký một chỉ số cho câu hỏi này để lần sau ra cùng một con số.
          </p>
        </KV>
      )}

      <KV k="Dữ liệu">
        {dataLine || (prov ? null : 'Chưa có thông tin quản trị cho các bảng này (kho chưa được quét).')}
      </KV>
      {fresh && (
        <KV k="Độ mới">
          <StatusDot tone={FRESH_TONE[fresh.tone]} colorText>
            {fresh.label}{typeof prov?.age_seconds === 'number' ? ` · ${formatDuration(prov.age_seconds * 1000)}` : ''}
          </StatusDot>
        </KV>
      )}
      <KV k="Phân loại">{prov?.classification}</KV>
      {prov?.stale_tables?.length ? <KV k="Bảng quá hạn">{prov.stale_tables.join(', ')}</KV> : null}
      {prov?.unknown_tables?.length ? <KV k="Bảng chưa đo độ mới">{prov.unknown_tables.join(', ')}</KV> : null}

      <KV k="Truy vấn">
        {[
          run.db_name ? `${run.db_name}${run.db_type ? ` (${run.db_type})` : ''}` : null,
          run.row_count !== null && run.row_count !== undefined ? `${formatNumber(run.row_count)} dòng` : null,
          run.executed_at ? `chạy ${formatDateTime(run.executed_at)}` : null,
          run.watermark ? `mốc ${formatDateTime(run.watermark)}` : null,
        ].filter(Boolean).join(' · ') || null}
      </KV>
      <KV k="Mã câu lệnh" mono>{run.sql_hash}</KV>

      {(findings.length > 0 || run.truncation?.reason || run.rows_capped || run.result_warnings?.length) ? (
        <KV k="Cảnh báo">
          <ul className="m-0 flex list-disc flex-col gap-[2px] pl-da-xl">
            {findings.map((f, i) => (
              <li key={i} className="text-error">
                {f.detail}{f.expression ? <span className="text-muted-foreground da-mono"> — {f.expression}</span> : null}
              </li>
            ))}
            {run.truncation?.reason && (
              <li className="text-warning">
                Kết quả chưa đủ: {TRUNCATION_LABELS[run.truncation.reason] ?? run.truncation.reason}
                {run.truncation.rows_returned ? ` (${formatNumber(run.truncation.rows_returned)} dòng)` : ''}.
              </li>
            )}
            {run.rows_capped && <li className="text-muted-foreground">Bảng dưới câu trả lời chỉ xem trước {formatNumber(run.rows.length)} dòng đầu.</li>}
            {run.result_warnings?.map((warning, i) => (
              <li key={`result-${i}`} className="text-warning">{warning.detail || warning.code}</li>
            ))}
          </ul>
        </KV>
      ) : null}

      {run.sql && (
        <div className="flex flex-col gap-da-sm">
          <button
            type="button"
            aria-expanded={sqlOpen}
            onClick={() => setSqlOpen(v => !v)}
            className="w-fit text-primary hover:underline da-body-strong"
          >
            {sqlOpen ? 'Ẩn câu lệnh SQL' : 'Xem câu lệnh SQL'}
          </button>
          {sqlOpen && (
            <>
              <SqlBlock sql={run.sql} />
              <div className="flex items-center gap-da-sm">
                <CopyButton text={run.sql} label="Sao chép SQL" />
                <span className="text-muted-foreground da-caption">sandbox_executor · chỉ đọc · có ghi nhật ký</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

export function EvidencePanel({ message, onClose }: { message: AskMessage | null; onClose?: () => void }) {
  const ev = message?.evidence;
  const citations = message?.citations ?? [];
  const resultRuns = (message?.resultSets ?? []).map(resultSetToEvidenceRun);
  const runs = resultRuns.length ? resultRuns : ev?.runs ?? [];

  const header = (
    <div className="flex items-center gap-da-md">
      <h2 className="m-0 min-w-0 flex-1 text-foreground da-heading-2">Cách tính</h2>
      {onClose && (
        <button type="button" aria-label="Đóng" onClick={onClose} className="inline-flex size-7 items-center justify-center rounded-md text-foreground hover:bg-subtle">
          <Icon name="x" />
        </button>
      )}
    </div>
  );

  if (!message) {
    return (
      <div className="flex flex-col gap-da-lg">
        {header}
        <p className="m-0 text-muted-foreground da-body-small">
          Đặt một câu hỏi để xem con số được tính ra sao: chỉ số nào, câu lệnh nào, bao nhiêu dòng, dữ liệu mới tới đâu.
        </p>
      </div>
    );
  }

  if (message.status === 'streaming' && !ev && !resultRuns.length) {
    return (
      <div className="flex flex-col gap-da-lg">
        {header}
        <p className="m-0 text-muted-foreground da-body-small">
          Đang chạy… cách tính (chỉ số, câu lệnh, số dòng) hiện khi có câu trả lời.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-da-lg">
      {header}
      <p className="m-0 text-muted-foreground da-caption">
        Trả lời lúc {formatTime(message.timestamp)}{message.latencyMs ? ` · ${formatDuration(message.latencyMs)}` : ''}
      </p>

      {runs.length ? (
        runs.map((run, i) => <RunDetail key={run.id ?? i} run={run} index={i} total={runs.length} />)
      ) : (
        <p className="m-0 text-muted-foreground da-body-small">
          {ev?.clarifications?.length
            ? 'Chưa chạy truy vấn nào: hệ thống đang chờ bạn làm rõ câu hỏi.'
            : 'Câu trả lời này không đọc từ kho dữ liệu (nó dựa trên tài liệu hoặc hội thoại), nên không có câu lệnh để xem.'}
        </p>
      )}

      {ev?.warnings?.some(w => w.kind === 'assumption') ? (
        <KV k="Giả định">
          {ev.warnings.filter(w => w.kind === 'assumption').map((w, i) => <p key={i} className="m-0">{w.detail}</p>)}
        </KV>
      ) : null}

      {ev?.warnings?.some(w => w.kind !== 'assumption') ? (
        <KV k="Cảnh báo đơn vị">
          {ev.warnings.filter(w => w.kind !== 'assumption').map((w, i) => <p key={i} className="m-0 text-error">{w.detail}</p>)}
        </KV>
      ) : null}

      {citations.length > 0 && (
        <KV k="Nguồn">
          <ol className="m-0 flex list-none flex-col gap-da-sm p-0">
            {citations.map(c => (
              <li key={c.index} className="flex items-start gap-da-md">
                <span className="mt-px inline-flex h-4 min-w-4 shrink-0 items-center justify-center rounded-sm bg-accent px-da-xs text-primary da-label">{c.index}</span>
                <div className="min-w-0">
                  <div className="break-words text-foreground da-body-strong">
                    {c.source} <span className="text-muted-foreground da-caption">· {CITATION_TAGS[c.type] ?? c.type}</span>
                  </div>
                  <p className="m-0 line-clamp-2 text-muted-foreground da-caption">{c.snippet}</p>
                </div>
              </li>
            ))}
          </ol>
        </KV>
      )}
    </div>
  );
}
