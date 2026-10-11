import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Drawer } from '../../design-system/Drawer';
import { Notice } from '../../design-system/Notice';
import { useToast } from '../../design-system/Toast';
import { formatDateTime } from '../../lib/formatVN';
import {
  getEvidence,
  refreshEvidence,
  type EvidenceBatches,
  type EvidenceRow,
} from '../../lib/v1/exec';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import {
  conditionTitle,
  openSealedSafe,
  shortDateTime,
  type OpenThesis,
  type SealedEvidence,
} from './content';
import { KeyAccessLog } from './ScoresTab';
import { useVault, type Frame } from './VaultPage';

/*
 * Tab Bằng chứng (2425:15639, UC-16-03): what the evidence workflow found for and against one
 * thesis, sealed to the vault's box key and opened here. The workflow sees only the condition —
 * never the claim, the confidence or the leader's name — and draws only from certified metrics,
 * published data products, approved forecasts and approved knowledge pages; a source past its
 * freshness commitment is listed as insufficient, not as evidence (BR-190).
 */
const STRENGTH = { weak: 'Yếu', medium: 'Vừa', strong: 'Mạnh' } as const;
const STRENGTH_TONE = { weak: 'neutral', medium: 'info', strong: 'accent' } as const;
const SIDE = { support: 'ủng hộ', oppose: 'phản bác' } as const;

interface Opened extends EvidenceRow {
  item: SealedEvidence | null;
}

const subject = (t: OpenThesis | undefined) =>
  t?.content?.statement.split(/\s[≥≤<>]/)[0] ?? 'Luận điểm';

/** What changed between the latest batch and the one before, by source key per side. */
function diff(latest: Opened[], previous: Opened[]) {
  const key = (r: Opened) => `${r.side}|${r.item?.key ?? r.id}`;
  const before = new Set(previous.map(key));
  const after = new Set(latest.map(key));
  const added = latest.filter((r) => r.item_kind === 'evidence' && !before.has(key(r)));
  const removed = previous.filter((r) => r.item_kind === 'evidence' && !after.has(key(r)));
  const sides = (['support', 'oppose'] as const).filter(
    (s) => !added.some((r) => r.side === s) && !removed.some((r) => r.side === s),
  );
  return [
    ...added.map((r) => `+1 ${SIDE[r.side]}: ${r.item?.title ?? 'mục niêm phong'}`),
    ...removed.map((r) => `−1 ${SIDE[r.side]}: ${r.item?.title ?? 'mục niêm phong'}`),
    ...sides.map((s) => `mục ${SIDE[s]} giữ nguyên`),
  ];
}

export function EvidenceTab({ frame }: { frame: Frame }) {
  const { session, selected, goTo } = useVault();
  const toast = useToast();
  const [log, setLog] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const evidence = useV1(selected ? () => getEvidence(selected.id) : null, [selected?.id]);
  const batches = evidence.data?.batches ?? [];
  const latest = batches[0];

  const refresh = async () => {
    if (!selected) return;
    setRefreshing(true);
    try {
      await refreshEvidence(selected.id);
      toast({ tone: 'success', message: 'Đã yêu cầu lô bằng chứng mới; lô có sau vài phút' });
    } catch (e) {
      toast({ tone: 'error', message: e instanceof Error ? e.message : String(e) });
    } finally {
      setRefreshing(false);
    }
  };

  const latestItems = evidence.data?.items.filter((r) => r.batch_no === latest?.batch_no) ?? [];
  return frame(
    {
      breadcrumb: ['Kho riêng', 'Luận điểm', subject(selected)],
      title: 'Bằng chứng hai phía',
      description: latest
        ? `Lô ${formatDateTime(latest.ran_at)} · ${latestItems.length} mục · mỗi mục niêm phong cho bạn · lô chạy hàng ngày và khi chỉ số hoặc data product có phiên bản mới`
        : 'Lô chạy hàng ngày và khi chỉ số hoặc data product có phiên bản mới',
      actions: selected && (
        <>
          <Button onClick={() => setLog(true)}>Xem sổ truy cập</Button>
          {selected.status === 'open' && (
            <Button loading={refreshing} onClick={refresh}>
              Làm mới bằng chứng
            </Button>
          )}
          <Button kind="primary" onClick={() => goTo('publish')}>
            Công bố cho tổ chức
          </Button>
        </>
      ),
    },
    !selected ? (
      <Notice tone="info">Chưa có luận điểm nào; soạn ở tab Luận điểm.</Notice>
    ) : (
      <>
        <ThesisSummary thesis={selected} />
        <AsyncRegion state={evidence} what="bằng chứng">
          {(data) => (
            <Batches
              data={data}
              open={(sealed) => openSealedSafe<SealedEvidence>(session, sealed)}
            />
          )}
        </AsyncRegion>
        <p className="mt-auto text-muted-foreground da-caption">
          Bằng chứng chỉ lấy từ chỉ số chứng nhận, data product đã phát hành, dự báo đã duyệt và
          trang tri thức đã duyệt; không dùng bộ nhớ hay trang giả thuyết của chính kho. Mỗi lô có
          ít nhất một dòng cho mỗi phía.
        </p>
        <Drawer open={log} width="md" title="Sổ truy cập khoá" onClose={() => setLog(false)}>
          <KeyAccessLog thesisId={selected.id} />
        </Drawer>
      </>
    ),
  );
}

function ThesisSummary({ thesis }: { thesis: OpenThesis }) {
  const c = thesis.content;
  const chip = 'rounded-full bg-subtle px-da-md py-da-xs text-muted-foreground da-label';
  return (
    <section className="flex flex-wrap items-center gap-da-lg rounded-lg border border-border bg-card px-da-xl py-da-lg">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md">
        <h2 className="text-foreground da-heading-3">
          {c ? conditionTitle(c.condition) : 'Không giải mã được'}
        </h2>
        <div className="flex flex-wrap gap-[6px]">
          {c && (
            <span className={chip}>
              {c.condition.ref_kind === 'data_product' ? 'Data product' : 'Chỉ số'}:{' '}
              {c.condition.metric_label}
            </span>
          )}
          <span className={chip}>Chấm {formatDateTime(thesis.due_at)}</span>
          {c && <Badge tone="accent">Tự tin {Math.round(c.confidence * 100)}%</Badge>}
        </div>
      </div>
      <p className="flex max-w-[320px] items-start gap-da-md rounded-md bg-info-subtle px-da-lg py-da-md text-foreground da-caption">
        <span aria-hidden className="mt-[5px] size-[6px] shrink-0 rounded-full bg-info" />
        Agent tìm bằng chứng không thấy nhận định, mức tự tin và tên bạn
      </p>
    </section>
  );
}

function Batches({
  data,
  open,
}: {
  data: EvidenceBatches;
  open: (sealed: string) => SealedEvidence | null;
}) {
  const [latest, previous] = data.batches;
  if (!latest)
    return (
      <Notice tone="info">Chưa có lô bằng chứng nào; lô đầu chạy trong đêm sau khi lưu.</Notice>
    );
  const opened = data.items.map<Opened>((r) => ({ ...r, item: open(r.sealed_ct) }));
  const now = opened.filter((r) => r.batch_no === latest.batch_no);
  const before = previous ? opened.filter((r) => r.batch_no === previous.batch_no) : [];
  return (
    <>
      {previous && (
        <p className="rounded-md bg-subtle px-da-lg py-da-md da-caption">
          <span className="text-foreground da-body-strong">
            Thay đổi so với lô trước ({shortDateTime(previous.ran_at)})
          </span>{' '}
          <span className="text-muted-foreground">
            {[
              ...diff(now, before),
              `${latest.dropped_untraceable} mục bị bỏ vì không truy được số`,
            ].join(' · ')}
          </span>
        </p>
      )}
      <div className="grid gap-da-xl md:grid-cols-2">
        {(['support', 'oppose'] as const).map((side) => {
          const rows = now.filter((r) => r.side === side);
          return (
            <section
              key={side}
              aria-label={side === 'support' ? 'Ủng hộ' : 'Phản bác'}
              className="flex flex-col gap-[10px]"
            >
              <h3 className="flex items-center gap-[6px] text-foreground da-body-strong">
                <span
                  aria-hidden
                  className={cn(
                    'size-[6px] rounded-full',
                    side === 'support' ? 'bg-success' : 'bg-error',
                  )}
                />
                {side === 'support' ? 'Ủng hộ' : 'Phản bác'} ·{' '}
                {rows.filter((r) => r.item_kind === 'evidence').length}
              </h3>
              {rows.map((r) => (
                <EvidenceCard key={r.id} row={r} />
              ))}
            </section>
          );
        })}
      </div>
    </>
  );
}

function EvidenceCard({ row }: { row: Opened }) {
  const item = row.item;
  if (row.item_kind !== 'evidence')
    return (
      <div className="flex flex-col gap-da-xs rounded-lg border border-warning bg-subtle px-da-lg py-[10px]">
        <span className="flex items-center gap-[6px] text-foreground da-body-small">
          <span aria-hidden className="size-[6px] shrink-0 rounded-full bg-warning" />
          {item?.title ??
            (row.item_kind === 'none_found'
              ? 'Không tìm thấy bằng chứng phía này'
              : 'Bằng chứng không đủ')}
        </span>
        {item?.text && <span className="text-muted-foreground da-caption">{item.text}</span>}
      </div>
    );
  return (
    <article className="flex flex-col gap-da-xs rounded-lg border border-border bg-card px-da-lg py-da-md">
      <span className="flex items-center gap-da-md">
        {row.strength && <Badge tone={STRENGTH_TONE[row.strength]}>{STRENGTH[row.strength]}</Badge>}
        <span className="text-muted-foreground da-caption">{item?.source_kind}</span>
      </span>
      <h4 className="text-foreground da-body-small">
        {item?.title ?? 'Không mở được mục niêm phong'}
      </h4>
      {item && (
        <p className="text-foreground da-body-small">
          {item.text}
          {item.query_run && (
            <>
              {' · '}
              <span className="text-info">{item.query_run}</span>
            </>
          )}
        </p>
      )}
      {item?.source && (
        <span className="text-muted-foreground da-caption">Nguồn: {item.source}</span>
      )}
      {!row.freshness_ok && <span className="text-warning da-caption">Nguồn quá hạn độ mới</span>}
    </article>
  );
}
