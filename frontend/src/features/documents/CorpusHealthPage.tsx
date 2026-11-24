import { useState } from 'react';
import { AppLink, LinkButton } from '../../components/shell/AppLink';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { EmptyState } from '../../design-system/EmptyState';
import { KpiCard } from '../../design-system/KpiCard';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { formatNumber, formatPercent } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { getDocumentHealth, ISSUE_KIND, type DocumentHealth } from '../../lib/v1/documents';
import { DOCUMENT_STATUS, listCollections } from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { HEAD_ROW, ROW } from './parts';

/*
 * RG-09 "Sức khoẻ kho tài liệu" (DI-21, DOCUMENT_INTELLIGENCE_SPEC §9): the Data Space's documents
 * by status, its open quality issues by kind and severity, the pages no answer may use, what
 * retrieval loses (passages without a vector or suspected of injection, documents out of force or
 * replaced) and the documents worst off, each opening in the viewer; the issues themselves are worked in Hàng chờ
 * chất lượng. All of it from GET /v1/document-health, which counts and never shows text.
 */
const ALL = 'all';
const CELL = 'px-da-xs py-da-md first:pl-da-lg last:pr-da-lg';
const HEAD = 'px-da-xs py-da-sm font-medium first:pl-da-lg last:pr-da-lg';

export default function CorpusHealthPage() {
  const [collection, setCollection] = useState<string>(ALL);
  const collections = useV1(['listCollections'], listCollections);
  const health = useV1(['getDocumentHealth', collection], () =>
    getDocumentHealth(collection === ALL ? undefined : collection),
  );
  const options = [
    { value: ALL, label: 'Mọi bộ sưu tập' },
    ...(collections.data?.items ?? []).map((c) => ({ value: c.id, label: c.name })),
  ];
  return (
    <div className="flex flex-col gap-da-xl px-da-3xl py-da-xl max-[767px]:px-da-lg">
      <PageHeader
        breadcrumb={['Dữ liệu', 'Tài liệu']}
        title="Sức khoẻ kho tài liệu"
        description="Tài liệu của Data Space theo trạng thái, vấn đề chất lượng đang mở và trang không dùng được để trả lời."
        actions={
          <>
            <Select
              size="sm"
              label="Bộ sưu tập"
              options={options}
              value={collection}
              onChange={setCollection}
            />
            <LinkButton area="data" page="DOC_QUALITY">
              Mở hàng chờ chất lượng
            </LinkButton>
          </>
        }
      />
      <AsyncRegion
        state={health}
        what="sức khoẻ kho tài liệu"
        skeletonRows={6}
        isEmpty={(h) => h.documents === 0}
        empty={
          <EmptyState
            className="m-da-3xl"
            icon="file"
            title="Chưa có tài liệu"
            description="Nạp tài liệu vào một bộ sưu tập để xem sức khoẻ của kho."
          />
        }
      >
        {(h) => <Health health={h} />}
      </AsyncRegion>
    </div>
  );
}

function Health({ health: h }: { health: DocumentHealth }) {
  const ready = h.by_status.find((s) => s.status === 'ready')?.count ?? 0;
  return (
    <>
      <div className="grid grid-cols-4 gap-da-lg max-[1023px]:grid-cols-2 max-[519px]:grid-cols-1">
        <KpiCard label="Tài liệu" value={formatNumber(h.documents)}>
          <span className="text-muted-foreground da-body-small">
            {formatPercent(ready / h.documents)} sẵn sàng để trả lời
          </span>
        </KpiCard>
        <KpiCard label="Vấn đề đang mở" value={formatNumber(h.open_issues)}>
          <span
            className={cn(
              'da-body-small',
              h.blocking_issues > 0 ? 'text-error' : 'text-muted-foreground',
            )}
          >
            {formatNumber(h.blocking_issues)} chặn tài liệu
          </span>
        </KpiCard>
        <KpiCard label="Trang không dùng được" value={formatNumber(h.pages_unusable)}>
          <span className="text-muted-foreground da-body-small">
            {h.pages_read > 0
              ? `${formatPercent(h.pages_unusable / h.pages_read)} của ${formatNumber(h.pages_read)} trang đã đọc`
              : 'Chưa đọc trang nào'}
          </span>
        </KpiCard>
        <KpiCard label="Bị giữ chờ duyệt" value={formatNumber(h.gated_documents)}>
          <span className="text-muted-foreground da-body-small">Quá 30% trang không dùng được</span>
        </KpiCard>
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,2fr)] gap-da-xl max-[1023px]:grid-cols-1">
        <section aria-labelledby="health-status" className="flex flex-col gap-da-md">
          <h2 id="health-status" className="text-foreground da-heading-3">
            Tài liệu theo trạng thái
          </h2>
          <ul className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-lg">
            {h.by_status.map((s) => {
              const status = DOCUMENT_STATUS[s.status] ?? { tone: 'idle', text: s.status };
              return (
                <li key={s.status} className="flex flex-col gap-da-xs">
                  <div className="flex items-center justify-between gap-da-md da-body-small">
                    <StatusDot tone={status.tone}>{status.text}</StatusDot>
                    <span className="text-foreground da-mono">{formatNumber(s.count)}</span>
                  </div>
                  <div aria-hidden="true" className="h-1 rounded-full bg-subtle">
                    <div
                      className="h-1 rounded-full bg-border-strong"
                      style={{ width: `${(100 * s.count) / h.documents}%` }}
                    />
                  </div>
                </li>
              );
            })}
          </ul>
          <h2 id="health-retrieval" className="text-foreground da-heading-3">
            Ảnh hưởng tới truy hồi
          </h2>
          <dl
            aria-labelledby="health-retrieval"
            className="flex flex-col gap-da-sm rounded-lg border border-border bg-card p-da-lg da-body-small"
          >
            {[
              [
                'Đoạn chưa có vector',
                `${formatNumber(h.chunks_unindexed)} / ${formatNumber(h.chunks)}`,
              ],
              ['Đoạn nghi chèn lệnh', formatNumber(h.chunks_injection_suspected)],
              ['Tài liệu hết hiệu lực', formatNumber(h.expired_documents)],
              ['Tài liệu đã bị thay thế', formatNumber(h.superseded_documents)],
            ].map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-da-md">
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-foreground da-mono">{value}</dd>
              </div>
            ))}
          </dl>
        </section>

        <section aria-labelledby="health-issues" className="flex flex-col gap-da-md">
          <h2 id="health-issues" className="text-foreground da-heading-3">
            Vấn đề đang mở theo loại
          </h2>
          {h.issues.length === 0 ? (
            <p className="rounded-lg border border-border bg-card p-da-lg text-muted-foreground da-body-small">
              Không có vấn đề chất lượng nào đang mở.
            </p>
          ) : (
            <div className="overflow-hidden rounded-lg border border-border bg-card">
              <table className="w-full border-collapse">
                <thead>
                  <tr className={HEAD_ROW}>
                    <th className={HEAD}>Loại</th>
                    <th className={HEAD}>Mức</th>
                    <th className={cn(HEAD, 'text-right')}>Vấn đề</th>
                    <th className={cn(HEAD, 'text-right')}>Tài liệu</th>
                  </tr>
                </thead>
                <tbody>
                  {h.issues.map((i) => (
                    <tr key={`${i.kind}-${i.severity}`} className={ROW}>
                      <td className={cn(CELL, 'text-foreground da-body-small')}>
                        {ISSUE_KIND[i.kind] ?? i.kind}
                      </td>
                      <td className={CELL}>
                        <Severity severity={i.severity} />
                      </td>
                      <td className={cn(CELL, 'text-right text-foreground da-mono')}>
                        {formatNumber(i.count)}
                      </td>
                      <td className={cn(CELL, 'text-right text-foreground da-mono')}>
                        {formatNumber(i.documents)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      <section aria-labelledby="health-affected" className="flex flex-col gap-da-md">
        <h2 id="health-affected" className="text-foreground da-heading-3">
          Tài liệu bị ảnh hưởng nhiều nhất
        </h2>
        {h.most_affected.length === 0 ? (
          <p className="rounded-lg border border-border bg-card p-da-lg text-muted-foreground da-body-small">
            Không tài liệu nào có vấn đề mở hay trang không dùng được.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-border bg-card">
            <table className="w-full border-collapse">
              <thead>
                <tr className={HEAD_ROW}>
                  <th className={HEAD}>Tài liệu</th>
                  <th className={HEAD}>Trạng thái</th>
                  <th className={cn(HEAD, 'text-right')}>Vấn đề mở</th>
                  <th className={cn(HEAD, 'text-right')}>Trang không dùng được</th>
                  <th className={HEAD}>Loại vấn đề</th>
                </tr>
              </thead>
              <tbody>
                {h.most_affected.map((d) => {
                  const status = DOCUMENT_STATUS[d.status] ?? { tone: 'idle', text: d.status };
                  const name = d.title ?? d.file_name;
                  return (
                    <tr key={d.id} className={ROW}>
                      <td className={cn(CELL, 'max-w-[320px] truncate da-body-strong')}>
                        <AppLink
                          area="data"
                          page="DOCUMENT"
                          id={d.id}
                          className="text-primary hover:underline"
                          aria-label={`Mở ${name} trong trình xem tài liệu`}
                        >
                          {name}
                        </AppLink>
                      </td>
                      <td className={cn(CELL, 'da-body-small')}>
                        <StatusDot tone={status.tone}>{status.text}</StatusDot>
                      </td>
                      <td className={cn(CELL, 'text-right text-foreground da-mono')}>
                        {formatNumber(d.open_issues)}
                        {d.blocking_issues > 0 && (
                          <span className="text-error">
                            {' '}
                            · {formatNumber(d.blocking_issues)} chặn
                          </span>
                        )}
                      </td>
                      <td className={cn(CELL, 'text-right text-foreground da-mono')}>
                        {d.page_count
                          ? `${formatNumber(d.pages_unusable)} / ${formatNumber(d.page_count)}`
                          : formatNumber(d.pages_unusable)}
                      </td>
                      <td className={cn(CELL, 'text-muted-foreground da-body-small')}>
                        {d.kinds.map((k) => ISSUE_KIND[k] ?? k).join(', ') || '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function Severity({ severity }: { severity: string }) {
  return severity === 'blocking' ? (
    <Badge tone="error">Chặn</Badge>
  ) : (
    <Badge tone="warning">Cảnh báo</Badge>
  );
}
