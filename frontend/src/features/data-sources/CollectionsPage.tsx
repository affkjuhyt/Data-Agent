import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber, formatPercent } from '../../lib/formatVN';
import {
  getCollectionsSummary,
  listCollections,
  type CollectionsSummary,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Phi cấu trúc · Bộ sưu tập" (2068:3067, UC-05-06): files, mail, recordings and images
 * grouped into collections that run the same pipeline. A strip of pipeline health on top, then
 * one 385px card per collection; a card opens "P05 · Bộ sưu tập · Tài liệu & tiền xử lý".
 * The header follows the P04/P05 frames (breadcrumb "Dữ liệu / Tài liệu"). Creating a collection,
 * or asking the agent to propose one, has no flow yet.
 */
export default function CollectionsPage() {
  const { navigate } = useAppState();
  const collections = useV1(listCollections, []);
  const summary = useV1(getCollectionsSummary, []);

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Tài liệu']}
        title="Bộ sưu tập"
        description="Tệp, văn bản, email, ghi âm và hình ảnh được nạp theo pipeline: nhận dạng, trích trường, chia đoạn, lập chỉ mục, liên kết với bản ghi có cấu trúc và che dữ liệu cá nhân."
        actions={
          <>
            <Tooltip content="Agent đề xuất bộ sưu tập đang được làm.">
              <Button icon="agent" disabled>
                Nhờ agent đề xuất bộ sưu tập
              </Button>
            </Tooltip>
            <Tooltip content="Tạo bộ sưu tập đang được làm.">
              <Button kind="primary" disabled>
                Tạo bộ sưu tập
              </Button>
            </Tooltip>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-xl">
        {summary.data && <PipelineStrip summary={summary.data} />}
        <AsyncRegion
          state={collections}
          what="bộ sưu tập"
          skeletonRows={6}
          isEmpty={(page) => page.items.length === 0}
          empty={
            <EmptyState
              icon="folder"
              title="Chưa có bộ sưu tập nào"
              description="Kết nối SharePoint, hộp thư hoặc thư mục quét để tài liệu được nạp vào một bộ sưu tập."
              action={<Button onClick={() => navigate('data', 'CONNECT')}>Kết nối nguồn</Button>}
            />
          }
        >
          {(page) => (
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-x-da-md gap-y-da-xl">
              {page.items.map((collection) => (
                <li key={collection.id}>
                  <button
                    type="button"
                    onClick={() => navigate('data', 'COLLECTION', collection.id)}
                    className="flex h-full w-full flex-col items-start gap-da-sm rounded-lg border border-border bg-card p-da-lg text-left hover:border-border-strong hover:bg-subtle"
                  >
                    <span className="flex w-full items-center gap-da-sm">
                      <span className="inline-flex size-7 items-center justify-center rounded-md text-foreground">
                        <Icon name={collection.icon} />
                      </span>
                      <span className="min-w-0 flex-1 text-foreground da-heading-3">
                        {collection.name}
                      </span>
                    </span>
                    <span className="text-muted-foreground da-caption">
                      {collection.content_text}
                    </span>
                    <span className="text-foreground da-body-small">
                      {collection.extraction_text}
                    </span>
                    {collection.status ? (
                      <StatusDot tone={collection.status.tone}>{collection.status.text}</StatusDot>
                    ) : (
                      <Badge>Lưu trữ</Badge>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}

function PipelineStrip({ summary }: { summary: CollectionsSummary }) {
  const cells = [
    {
      tone: 'info' as const,
      label: 'Đang nạp',
      value: `${formatNumber(summary.ingesting_files)} tệp`,
    },
    {
      tone: 'error' as const,
      label: 'Lỗi nhận dạng',
      value: `${formatNumber(summary.recognition_errors)} tệp`,
    },
    {
      tone: 'warning' as const,
      label: 'Chờ duyệt dữ liệu cá nhân',
      value: `${formatNumber(summary.pii_batches_pending)} lô`,
    },
    {
      tone: 'success' as const,
      label: 'Độ phủ chỉ mục',
      value: formatPercent(summary.index_coverage),
    },
  ];
  return (
    <ul
      aria-label="Pipeline tài liệu"
      className="grid grid-cols-4 gap-da-md max-[1023px]:grid-cols-2"
    >
      {cells.map((cell) => (
        <li
          key={cell.label}
          className="flex items-center gap-da-sm rounded-lg border border-border bg-card px-da-lg py-da-md"
        >
          <StatusDot tone={cell.tone} className="min-w-0 flex-1 text-muted-foreground">
            <span className="text-muted-foreground">{cell.label}</span>
          </StatusDot>
          <span className="text-foreground da-body-strong">{cell.value}</span>
        </li>
      ))}
    </ul>
  );
}
