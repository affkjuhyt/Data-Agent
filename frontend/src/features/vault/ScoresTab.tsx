import { useMemo } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { KpiCard } from '../../design-system/KpiCard';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { formatDate, formatNumber, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  listKeyAccess,
  listScores,
  type KeyAccessEntry,
  type ThesisScore,
} from '../../lib/v1/exec';
import { useV1 } from '../../lib/v1/useV1';
import {
  bands,
  BRIER_BASELINE,
  MIN_FOR_CONCLUSION,
  summary,
  type Band,
} from '../../lib/vault/calibration';
import { openSealedSafe, shortDateTime, type SealedScore } from './content';
import { useVault, type Frame } from './VaultPage';

/*
 * Tab Chấm điểm (2426:15422, UC-16-04, BR-191): the calibration of the leader's confidence. The
 * confidence is only readable here, so the Brier score, the bands and the chart are computed on
 * the device from the decrypted theses and their final scores; scores are append-only. Beside it,
 * the vault key's access log: every time a workflow asked OpenBao to decrypt a condition.
 */
const pct = (ratio: number) => `${Math.round(ratio * 100)}%`;

const scoreOutcome = (outcome: ThesisScore['outcome']) =>
  outcome === 'correct' ? 'Đúng' : outcome === 'incorrect' ? 'Sai' : 'Không chấm được';

export function ScoresTab({ frame }: { frame: Frame }) {
  const { session, theses } = useVault();
  const scores = useV1(async () => (await listScores()).items, []);

  const rows = useMemo(() => {
    // The latest score of each thesis wins: a restatement re-scores after the data was corrected.
    const latest = new Map<string, ThesisScore>();
    for (const s of scores.data ?? []) {
      const seen = latest.get(s.thesis_id);
      if (!seen || s.scored_at > seen.scored_at) latest.set(s.thesis_id, s);
    }
    return (theses.data ?? [])
      .filter((t) => latest.has(t.id) && t.content)
      .map((t) => {
        const score = latest.get(t.id)!;
        return { thesis: t, score, sealed: openSealedSafe<SealedScore>(session, score.sealed_ct) };
      })
      .sort((a, b) => b.thesis.due_at.localeCompare(a.thesis.due_at));
  }, [scores.data, theses.data, session]);

  const view = useTableView(rows, {
    search: (r) => `${r.thesis.content?.statement ?? ''} ${r.sealed?.observed ?? ''}`,
    filters: [{ id: 'outcome', label: 'Kết quả', value: (r) => scoreOutcome(r.score.outcome) }],
    sorts: [
      { id: 'statement', value: (r) => r.thesis.content?.statement },
      { id: 'scored', value: (r) => r.score.scored_at },
      { id: 'confidence', value: (r) => r.thesis.content?.confidence },
      { id: 'observed', value: (r) => r.sealed?.observed },
      { id: 'outcome', value: (r) => scoreOutcome(r.score.outcome) },
      { id: 'receipt', value: (r) => r.sealed?.receipt },
    ],
    pageSize: 50,
  });
  const judged = rows
    .filter((r) => r.score.outcome !== 'unresolvable')
    .map((r) => ({
      confidence: r.thesis.content!.confidence,
      correct: r.score.outcome === 'correct',
    }));
  const s = summary(judged);
  const band = bands(judged);
  const withdrawn = (theses.data ?? []).filter((t) => t.status === 'withdrawn').length;
  const unresolvable = (theses.data ?? []).filter((t) => t.status === 'unresolvable').length;
  const since = rows.length ? rows[rows.length - 1].thesis.due_at : null;

  return frame(
    {
      breadcrumb: ['Kho riêng', 'Chấm điểm'],
      title: 'Chấm điểm & hiệu chỉnh',
      description: `Ảnh chụp trạng thái ${formatDate(new Date())} · thiết bị tính từ mức tự tin đã giải mã và kết quả chấm · điểm chỉ ghi thêm, không sửa, không xoá (BR-191)`,
      // The PDF is the browser's print of this page: it is made on the device, never on a server.
      actions: <Button onClick={() => window.print()}>Xuất bản riêng PDF</Button>,
    },
    <>
      {scores.error && <Notice tone="error">Không tải được điểm. {scores.error.message}</Notice>}
      {!s.enough && (
        <Notice tone="info">
          Mới có {s.count} luận điểm đã chấm; cần ít nhất {MIN_FOR_CONCLUSION} để kết luận về độ
          hiệu chỉnh.
        </Notice>
      )}
      <div className="grid grid-cols-2 gap-da-lg lg:grid-cols-5">
        <KpiCard
          label="Brier"
          value={
            s.brier === null
              ? '—'
              : formatNumber(s.brier, { maxFractionDigits: 2, minFractionDigits: 2 })
          }
        >
          <span
            className={cn(
              'da-caption',
              s.brier !== null && s.brier > BRIER_BASELINE ? 'text-warning' : 'text-success',
            )}
          >
            mốc {formatNumber(BRIER_BASELINE)} · thấp hơn là tốt
          </span>
        </KpiCard>
        <KpiCard label="Đã chấm" value={String(s.count)}>
          {since && (
            <span className="text-foreground da-caption">từ {formatDate(since).slice(3)}</span>
          )}
        </KpiCard>
        <KpiCard label="Đúng" value={s.hitRate === null ? '—' : `${s.correct} · ${pct(s.hitRate)}`}>
          <span className="text-foreground da-caption">trên {s.count} luận điểm đã chấm</span>
        </KpiCard>
        <KpiCard
          label="Tự tin bình quân"
          value={s.meanConfidence === null ? '—' : pct(s.meanConfidence)}
        >
          {s.overconfidence !== null && (
            <span
              className={cn('da-caption', s.overconfidence > 0 ? 'text-warning' : 'text-success')}
            >
              {s.overconfidence > 0 ? 'cao' : 'thấp'} hơn tỷ lệ đúng{' '}
              {Math.abs(Math.round(s.overconfidence))} điểm
            </span>
          )}
        </KpiCard>
        <KpiCard label="Không vào điểm" value={`${withdrawn} · ${unresolvable}`}>
          <span className="text-foreground da-caption">
            {withdrawn} rút · {unresolvable} không chấm được
          </span>
        </KpiCard>
      </div>
      <div className="flex flex-col gap-da-lg xl:flex-row">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg">
          <section
            aria-label="Độ hiệu chỉnh"
            className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl"
          >
            <h2 className="text-foreground da-heading-3">Độ hiệu chỉnh theo 5 nhóm tự tin</h2>
            <div className="flex flex-col gap-da-xl md:flex-row">
              <CalibrationChart bands={band} />
              <div className="flex flex-col gap-da-xs">
                <span className="text-muted-foreground da-caption">Theo nhóm tự tin</span>
                <table className="da-caption">
                  <tbody>
                    {band.map((b) => (
                      <tr key={b.label} className="text-foreground">
                        <td className="pr-da-xl">{b.label}</td>
                        <td>
                          {b.count} luận điểm · đúng {b.correct} (
                          {b.hitRate === null ? '—' : pct(b.hitRate)})
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <span className="max-w-[300px] text-muted-foreground da-caption">
                  {reading(band)}
                </span>
              </div>
            </div>
          </section>
          <section
            aria-label="Luận điểm đã chấm"
            className="overflow-x-auto rounded-lg border border-border bg-card"
          >
            <TableToolbar view={view} placeholder="Tìm luận điểm…" searchLabel="Tìm luận điểm" />
            <table className="w-full text-left da-body-small">
              <thead className="bg-subtle text-muted-foreground da-caption">
                <tr>
                  <SortableTh view={view} column="statement" className="px-da-lg py-[10px] font-normal">
                    Luận điểm · điều kiện
                  </SortableTh>
                  <SortableTh view={view} column="scored" className="px-da-lg py-[10px] font-normal">
                    Ngày chấm
                  </SortableTh>
                  <SortableTh view={view} column="confidence" className="px-da-lg py-[10px] font-normal">
                    Tự tin
                  </SortableTh>
                  <SortableTh view={view} column="observed" className="px-da-lg py-[10px] font-normal">
                    Quan sát
                  </SortableTh>
                  <SortableTh view={view} column="outcome" className="px-da-lg py-[10px] font-normal">
                    Kết quả
                  </SortableTh>
                  <SortableTh view={view} column="receipt" className="px-da-lg py-[10px] font-normal">
                    Biên nhận
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map(({ thesis, score, sealed }) => (
                  <tr key={thesis.id} className="border-t border-border text-foreground">
                    <td className="px-da-lg py-[10px]">{thesis.content!.statement}</td>
                    <td className="whitespace-nowrap px-da-lg">{shortDateTime(score.scored_at)}</td>
                    <td className="px-da-lg">{pct(thesis.content!.confidence)}</td>
                    <td className="whitespace-nowrap px-da-lg">{sealed?.observed ?? '—'}</td>
                    <td className="whitespace-nowrap px-da-lg">
                      <span className="inline-flex items-center gap-[6px]">
                        <span
                          aria-hidden
                          className={cn(
                            'size-[6px] rounded-full',
                            score.outcome === 'correct'
                              ? 'bg-success'
                              : score.outcome === 'incorrect'
                                ? 'bg-error'
                                : 'bg-warning',
                          )}
                        />
                        {score.outcome === 'correct'
                          ? 'Đúng'
                          : score.outcome === 'incorrect'
                            ? 'Sai'
                            : 'Không chấm được'}
                      </span>
                    </td>
                    <td className="whitespace-nowrap px-da-lg text-info">
                      {sealed?.receipt ?? '—'}
                    </td>
                  </tr>
                ))}
                {view.total === 0 && (
                  <tr>
                    <td colSpan={6} className="px-da-lg py-da-lg text-muted-foreground">
                      Chưa có luận điểm nào được chấm.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <TablePager view={view} />
          </section>
        </div>
        <section
          aria-label="Sổ truy cập khoá"
          className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl xl:w-[392px] xl:shrink-0"
        >
          <h2 className="text-foreground da-heading-3">Sổ truy cập khoá · chỉ ghi thêm</h2>
          <KeyAccessLog />
        </section>
      </div>
    </>,
  );
}

/**
 * One sentence on where the curve leaves the diagonal: the band whose overconfidence weighs most,
 * gap times count, so one thesis alone at 55% does not outweigh four at 85%.
 */
function reading(band: Band[]) {
  const worst = band
    .filter((b) => b.count > 0 && b.hitRate !== null && b.meanConfidence !== null)
    .map((b) => ({ ...b, gap: b.meanConfidence! - b.hitRate! }))
    .sort((a, b) => b.gap * b.count - a.gap * a.count)[0];
  if (!worst || worst.gap <= 0.05) return 'Các điểm gần đường lý tưởng: tự tin khớp tỷ lệ đúng.';
  return `Điểm nằm dưới đường lý tưởng: tự tin cao hơn kết quả, rõ nhất ở nhóm ${worst.label.replace('%', '')}%.`;
}

const W = 380;
const H = 150;
const PAD = { left: 40, right: 30, top: 10, bottom: 22 };
const x = (conf: number) => PAD.left + ((conf - 0.5) / 0.5) * (W - PAD.left - PAD.right);
const y = (rate: number) => H - PAD.bottom - rate * (H - PAD.top - PAD.bottom);

function CalibrationChart({ bands: data }: { bands: Band[] }) {
  const points = data.filter((b) => b.count > 0 && b.meanConfidence !== null);
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-auto w-full max-w-[420px] shrink-0"
      role="img"
      aria-label="Biểu đồ tỷ lệ đúng theo mức tự tin; đường chéo là hiệu chỉnh lý tưởng"
    >
      {[0, 0.25, 0.5, 0.75, 1].map((r) => (
        <text
          key={r}
          x={PAD.left - 6}
          y={y(r) + 3}
          textAnchor="end"
          className="fill-muted-foreground text-[10px]"
        >
          {Math.round(r * 100)}%
        </text>
      ))}
      {[0.5, 0.6, 0.7, 0.8, 0.9, 1].map((c) => (
        <text
          key={c}
          x={x(c)}
          y={H - 6}
          textAnchor="middle"
          className="fill-muted-foreground text-[10px]"
        >
          {Math.round(c * 100)}%
        </text>
      ))}
      <text x={PAD.left + 4} y={PAD.top + 10} className="fill-muted-foreground text-[9px]">
        Tỷ lệ đúng
      </text>
      <text
        x={W - PAD.right}
        y={H - PAD.bottom - 4}
        textAnchor="end"
        className="fill-muted-foreground text-[9px]"
      >
        Tự tin
      </text>
      <line
        x1={x(0.5)}
        y1={y(0.5)}
        x2={x(1)}
        y2={y(1)}
        strokeDasharray="4 3"
        className="stroke-foreground"
        strokeWidth={1}
      />
      <text
        x={x(0.95)}
        y={y(1) + 2}
        textAnchor="middle"
        className="fill-muted-foreground text-[9px]"
      >
        lý tưởng
      </text>
      <polyline
        fill="none"
        strokeWidth={2}
        className="stroke-info"
        points={points.map((b) => `${x(b.meanConfidence!)},${y(b.hitRate!)}`).join(' ')}
      />
      {points.map((b) => (
        <circle
          key={b.label}
          cx={x(b.meanConfidence!)}
          cy={y(b.hitRate!)}
          r={3 + Math.min(b.count, 6) * 1.2}
          className="fill-info"
        >
          <title>
            {b.label}: {b.count} luận điểm, đúng {b.correct}
          </title>
        </circle>
      ))}
    </svg>
  );
}

const PURPOSE: Record<
  KeyAccessEntry['purpose'],
  { label: string; tone: 'accent' | 'info' | 'neutral' }
> = {
  scoring: { label: 'Chấm', tone: 'accent' },
  evidence_refresh: { label: 'Bằng chứng', tone: 'info' },
  validate: { label: 'Kiểm', tone: 'neutral' },
  unlock: { label: 'Mở kho', tone: 'neutral' },
  handover: { label: 'Bàn giao', tone: 'neutral' },
  rotation: { label: 'Xoay khoá', tone: 'neutral' },
  shred: { label: 'Huỷ khoá', tone: 'neutral' },
};

/** The key's access log (A2 GET /exec/key-access-log), optionally for one thesis. */
export function KeyAccessLog({ thesisId }: { thesisId?: string }) {
  const log = useV1(async () => (await listKeyAccess()).items, []);
  return (
    <AsyncRegion state={log} what="sổ truy cập khoá">
      {(items) => {
        const shown = thesisId
          ? items.filter((e) => !e.thesis_id || e.thesis_id === thesisId)
          : items;
        return (
          <div className="flex flex-col">
            <ul className="flex flex-col">
              {shown.map((e) => (
                <li
                  key={e.id}
                  className="flex flex-col gap-da-xs border-b border-border py-da-md last:border-b-0"
                >
                  <span className="flex items-center gap-da-md">
                    <span className="text-foreground da-mono">
                      {formatDate(e.at).slice(0, 5)} {formatTime(e.at, true)}
                    </span>
                    <Badge tone={PURPOSE[e.purpose].tone}>{PURPOSE[e.purpose].label}</Badge>
                  </span>
                  <span className="text-foreground da-body-small">{e.actor_text}</span>
                  <span className="text-muted-foreground da-mono text-[11px]">
                    {e.correlation_id ? `corr ${e.correlation_id}` : 'không gọi OpenBao'}
                  </span>
                </li>
              ))}
            </ul>
            <p className="pt-da-md text-muted-foreground da-caption">
              Mã đối chiếu khớp nhật ký audit OpenBao. Tối đa 3 lần giải mã mỗi luận điểm mỗi ngày
              (BR-192).
            </p>
          </div>
        );
      }}
    </AsyncRegion>
  );
}
