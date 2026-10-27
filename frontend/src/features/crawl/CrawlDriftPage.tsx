import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import { getSchemaDrift, resolveSchemaDrift, type SchemaDrift } from '../../lib/v1/crawl';
import { useV1 } from '../../lib/v1/useV1';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "P14 · Thay đổi cấu trúc · Hoàn tác" (2389:15400, UC-14-03): a heavy structure drift stopped a
 * source and quarantined its run (BR-152). The drift score and its signals, what it touches, the
 * version in use against the agent's proposal field by field, and what reverting would do. The
 * person approves the new version or reverts to the last good one (with a reason); editing the
 * schema by hand waits for its editor.
 */
const fmt = (n: number) => n.toFixed(2).replace('.', ',');
const fmt1 = (n: number) => String(n).replace('.', ',');

const CHANGE = {
  modify: { label: 'Sửa', tone: 'warning' },
  add: { label: 'Thêm', tone: 'success' },
  remove: { label: 'Bỏ', tone: 'error' },
} as const;

export default function CrawlDriftPage() {
  const { routeId } = useAppState();
  const drift = useV1(['getSchemaDrift', routeId], routeId ? () => getSchemaDrift(routeId) : null);
  return (
    <AsyncRegion state={drift} what="sự cố đổi cấu trúc">
      {(d) => <DriftBody drift={d} onChanged={drift.reload} />}
    </AsyncRegion>
  );
}

function DriftBody({ drift, onChanged }: { drift: SchemaDrift; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [confirmRevert, setConfirmRevert] = useState(false);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<'approve' | 'revert' | null>(null);
  const [error, setError] = useState<string>();
  const open = drift.resolution === null;
  const proposed = drift.proposed_version;
  const signalText = (s: SchemaDrift['signals'][number]) =>
    typeof s.value === 'number' ? fmt(s.value) : s.value;
  const signals = useTableView(drift.signals, {
    search: (s) => `${s.name} ${s.measure} ${signalText(s)}`,
    filters: [{ id: 'name', label: 'Tín hiệu', value: (s) => s.name }],
    sorts: [
      { id: 'name', value: (s) => s.name },
      { id: 'measure', value: (s) => s.measure },
      { id: 'value', value: (s) => (typeof s.value === 'number' ? s.value : s.value) },
      { id: 'weight', value: (s) => s.weight },
    ],
    pageSize: 50,
  });
  const changes = useTableView(drift.changes, {
    search: (c) => `${CHANGE[c.change].label} ${c.field} ${c.before} ${c.after} ${c.test_text}`,
    filters: [{ id: 'change', label: 'Thay đổi', value: (c) => CHANGE[c.change].label }],
    sorts: [
      { id: 'change', value: (c) => CHANGE[c.change].label },
      { id: 'field', value: (c) => c.field },
      { id: 'before', value: (c) => c.before },
      { id: 'after', value: (c) => c.after },
      { id: 'test', value: (c) => c.test_text },
    ],
    pageSize: 50,
  });

  const resolve = async (resolution: 'approved_new' | 'reverted') => {
    if (resolution === 'reverted' && !reason.trim()) {
      setError('Ghi lý do hoàn tác.');
      return;
    }
    setBusy(resolution === 'reverted' ? 'revert' : 'approve');
    setError(undefined);
    try {
      await resolveSchemaDrift(drift.id, {
        resolution,
        proposed_version_id: resolution === 'approved_new' ? proposed?.id : undefined,
        note: resolution === 'reverted' ? reason.trim() : undefined,
      });
      toast({
        tone: 'success',
        message:
          resolution === 'reverted'
            ? `Đang hoàn tác về v${drift.active_version.version}; trích lại từ bản gốc`
            : `Đã duyệt v${proposed?.version}; nguồn chạy tiếp`,
      });
      setConfirmRevert(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Thu thập ngoài', drift.source_name]}
        title={drift.title}
        description={drift.summary_text}
        actions={
          open ? (
            <>
              <Tooltip content="Trình sửa schema bằng tay đang được làm.">
                <Button disabled>Sửa tay</Button>
              </Tooltip>
              <Button disabled={busy !== null} onClick={() => setConfirmRevert(true)}>
                Hoàn tác về v{drift.active_version.version}
              </Button>
              {proposed && (
                <Button
                  kind="primary"
                  loading={busy === 'approve'}
                  disabled={busy !== null}
                  onClick={() => resolve('approved_new')}
                >
                  Duyệt phiên bản v{proposed.version}
                </Button>
              )}
            </>
          ) : (
            <LinkButton area="data" page="CRAWL_SOURCE" id={drift.source_id}>
              Mở nguồn
            </LinkButton>
          )
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pb-da-2xl pt-da-xl">
        {!open && (
          <Notice tone="success">
            Đã xử lý:{' '}
            {drift.resolution === 'reverted'
              ? `hoàn tác về v${drift.active_version.version}`
              : `duyệt v${proposed?.version}`}
            .
          </Notice>
        )}
        {error && !confirmRevert && <Notice tone="error">Không thực hiện được: {error}</Notice>}
        <div className="flex items-start gap-da-xl max-[1279px]:flex-col">
          <Card
            title={`Điểm lệch so với v${drift.active_version.version}`}
            className="min-w-0 flex-1"
          >
            <TableToolbar view={signals} placeholder="Tìm tín hiệu…" searchLabel="Tìm tín hiệu" />
            <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
              <thead>
                <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                  <SortableTh
                    view={signals}
                    column="name"
                    className="w-[130px] px-da-lg py-da-md font-normal"
                  >
                    Tín hiệu
                  </SortableTh>
                  <SortableTh
                    view={signals}
                    column="measure"
                    className="px-da-lg py-da-md font-normal"
                  >
                    Đo
                  </SortableTh>
                  <SortableTh
                    view={signals}
                    column="value"
                    className="w-[80px] px-da-lg py-da-md font-normal"
                  >
                    Giá trị
                  </SortableTh>
                  <SortableTh
                    view={signals}
                    column="weight"
                    className="w-[80px] px-da-lg py-da-md font-normal"
                  >
                    Trọng số
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {signals.rows.map((s) => (
                  <tr key={s.name} className="border-b border-border text-foreground">
                    <td className="px-da-lg py-[10px]">{s.name}</td>
                    <td className="px-da-lg py-[10px]">{s.measure}</td>
                    <td className="px-da-lg py-[10px]">
                      {typeof s.value === 'number' ? fmt(s.value) : s.value}
                    </td>
                    <td className="px-da-lg py-[10px]">
                      {s.weight === null ? '—' : fmt1(s.weight)}
                    </td>
                  </tr>
                ))}
                {drift.drift_score !== null && drift.thresholds ? (
                  <tr>
                    <td className="px-da-lg py-[10px] text-foreground da-heading-3">Tổng</td>
                    <td className="px-da-lg py-[10px] text-muted-foreground">
                      Ngưỡng nhẹ {fmt(drift.thresholds.light)} · nặng {fmt1(drift.thresholds.heavy)}
                    </td>
                    <td
                      className={cn(
                        'px-da-lg py-[10px] da-heading-3',
                        drift.drift_score >= drift.thresholds.heavy
                          ? 'text-error'
                          : 'text-foreground',
                      )}
                    >
                      {fmt(drift.drift_score)}
                    </td>
                    <td />
                  </tr>
                ) : (
                  <tr>
                    <td className="px-da-lg py-[10px] text-foreground da-heading-3">Mức</td>
                    <td
                      colSpan={3}
                      className={cn(
                        'px-da-lg py-[10px] da-heading-3',
                        drift.severity === 'major' ? 'text-error' : 'text-foreground',
                      )}
                    >
                      {drift.severity === 'major' ? 'Nặng' : 'Nhẹ'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <TablePager view={signals} />
          </Card>
          {drift.impact.length > 0 && (
            <Card title="Ảnh hưởng" className="w-[520px] shrink-0 max-[1279px]:w-full">
              <ul className="flex flex-col gap-da-lg">
                {drift.impact.map((i) => (
                  <li key={i.text}>
                    <StatusDot tone={i.tone}>{i.text}</StatusDot>
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
        {proposed && (
          <Card
            title={`So sánh schema v${drift.active_version.version} (đang dùng) và v${proposed.version} (agent đề xuất)`}
          >
            <TableToolbar
              view={changes}
              placeholder="Tìm trường…"
              searchLabel="Tìm thay đổi schema"
            />
            <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
              <thead>
                <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                  <SortableTh
                    view={changes}
                    column="change"
                    className="w-[100px] px-da-lg py-da-md font-normal"
                  >
                    Thay đổi
                  </SortableTh>
                  <SortableTh
                    view={changes}
                    column="field"
                    className="w-[160px] px-da-lg py-da-md font-normal"
                  >
                    Trường
                  </SortableTh>
                  <SortableTh
                    view={changes}
                    column="before"
                    className="px-da-lg py-da-md font-normal"
                  >
                    v{drift.active_version.version}
                  </SortableTh>
                  <SortableTh
                    view={changes}
                    column="after"
                    className="px-da-lg py-da-md font-normal"
                  >
                    v{proposed.version} đề xuất
                  </SortableTh>
                  <SortableTh
                    view={changes}
                    column="test"
                    className="w-[160px] px-da-lg py-da-md font-normal"
                  >
                    Chạy thử v{proposed.version}
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {changes.rows.map((c) => (
                  <tr
                    key={c.field}
                    className="border-b border-border last:border-b-0 text-foreground"
                  >
                    <td className="px-da-lg py-[10px]">
                      <Badge tone={CHANGE[c.change].tone}>{CHANGE[c.change].label}</Badge>
                    </td>
                    <td className="px-da-lg py-[10px] da-mono">{c.field}</td>
                    <td className="px-da-lg py-[10px]">{c.before}</td>
                    <td className="px-da-lg py-[10px]">{c.after}</td>
                    <td className="px-da-lg py-[10px]">{c.test_text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TablePager view={changes} />
          </Card>
        )}
        {drift.revert_steps.length > 0 && (
          <Card title={`Hoàn tác về v${drift.active_version.version} sẽ làm gì`}>
            <ol className="flex flex-col gap-[6px] text-foreground da-body-small">
              {drift.revert_steps.map((step, i) => (
                <li key={step}>
                  {i + 1}. {step}
                </li>
              ))}
            </ol>
          </Card>
        )}
      </div>
      <ConfirmDialog
        open={confirmRevert}
        title={`Hoàn tác về v${drift.active_version.version}?`}
        confirmLabel="Hoàn tác"
        cancelLabel="Quay lại"
        tone="danger"
        loading={busy === 'revert'}
        error={confirmRevert ? error : undefined}
        onCancel={() => {
          setConfirmRevert(false);
          setError(undefined);
        }}
        onConfirm={() => resolve('reverted')}
      >
        <div className="flex flex-col gap-da-sm">
          <p>
            Bản ghi trích bằng phiên bản lỗi chuyển superseded và được trích lại từ bản gốc; dữ liệu
            đã phát hành không bị sửa.
          </p>
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Lý do (bắt buộc)</span>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
            />
          </label>
        </div>
      </ConfirmDialog>
    </div>
  );
}

function Card({
  title,
  className,
  children,
}: {
  title: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        'flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl',
        className,
      )}
    >
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}
