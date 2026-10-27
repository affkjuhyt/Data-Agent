import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listDataProducts, type DataProduct } from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';
import { versionLabel } from './format';
import { ImportCatalogDialog } from './ImportCatalogDialog';
import { ProductDrawer } from './ProductDrawer';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "v3.1 · Dữ liệu · Sản phẩm dữ liệu" (2062:2601, UC-06-06): the products this Data Space offers
 * to the others, as cards (contract version, status, Data Spaces using it, quality, last refresh),
 * filtered by Của tôi / Đang được dùng / Chờ xuất bản, with the selected one in a drawer.
 * "Nhập từ catalog" requests read access to products of other Data Spaces (ImportCatalogDialog).
 */
type Filter = 'mine' | 'in_use' | 'pending';

const inFilter = (p: DataProduct, filter: Filter) =>
  filter === 'mine' ||
  (filter === 'in_use' ? (p.used_by_spaces ?? 0) > 0 : p.status !== 'published');

export default function DataProductsPage() {
  const { navigate, routeId } = useAppState();
  const products = useV1(['listDataProducts'], listDataProducts);
  const [filter, setFilter] = useState<Filter>('mine');
  const [query, setQuery] = useState('');
  const [importing, setImporting] = useState(false);
  const toast = useToast();
  // undefined = nothing picked yet: the drawer shows the first product, as drawn; null = closed.
  const [picked, setPicked] = useState<string | null | undefined>(routeId);

  const items = products.data?.items ?? [];
  const selected = picked === undefined ? (items[0]?.id ?? null) : picked;

  const count = (f: Filter) => items.filter((p) => inFilter(p, f)).length;
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter(
      (p) =>
        inFilter(p, filter) &&
        (!q || [p.name, p.code, p.description].some((s) => s.toLowerCase().includes(q))),
    );
  }, [items, filter, query]);

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-xl px-da-3xl py-da-2xl">
        <PageHeader
          title="Sản phẩm dữ liệu"
          description="Dữ liệu Tài chính cung cấp cho các Data Space khác: contract, phiên bản, quyền và chất lượng."
          actions={
            <>
              <Button onClick={() => setImporting(true)}>Nhập từ catalog</Button>
              <LinkButton area="data" page="DATA_MARTS" kind="primary" icon="agent">
                Nhờ agent tạo sản phẩm
              </LinkButton>
            </>
          }
        />
        <div className="flex items-center gap-da-md border-b border-border">
          <Tabs<Filter>
            label="Lọc sản phẩm dữ liệu"
            className="border-b-0"
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'mine', label: `Của tôi (${count('mine')})` },
              { value: 'in_use', label: `Đang được dùng (${count('in_use')})` },
              { value: 'pending', label: `Chờ xuất bản (${count('pending')})` },
            ]}
          />
          <Input
            icon="search"
            aria-label="Tìm sản phẩm dữ liệu"
            placeholder="Tìm sản phẩm, cột, contract…"
            className="w-[240px]"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <AsyncRegion
          state={products}
          what="sản phẩm dữ liệu"
          skeletonRows={6}
          isEmpty={() => shown.length === 0}
          empty={
            <EmptyState
              icon="layers"
              title="Không có sản phẩm nào"
              description={
                query ? 'Không có sản phẩm khớp với từ tìm.' : 'Chưa có sản phẩm ở mục này.'
              }
            />
          }
        >
          {() => (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(300px,1fr))] gap-x-da-lg gap-y-da-xl">
              {shown.map((p) => (
                <ProductCard
                  key={p.id}
                  product={p}
                  selected={p.id === selected}
                  onSelect={() => setPicked(p.id)}
                />
              ))}
            </div>
          )}
        </AsyncRegion>
      </div>
      {selected && <ProductDrawer id={selected} onClose={() => setPicked(null)} />}
      <ImportCatalogDialog
        open={importing}
        onClose={() => setImporting(false)}
        onRequested={(count) => {
          setImporting(false);
          // copy: provisional, the frame has no success state.
          toast({ tone: 'success', message: `Đã gửi ${count} yêu cầu dùng cho chủ sở hữu` });
        }}
      />
    </div>
  );
}

const BADGE: Record<string, { tone: 'success' | 'warning' | 'neutral' | 'info'; label: string }> = {
  breaking: { tone: 'warning', label: 'Thay đổi phá vỡ' },
  published: { tone: 'success', label: 'Đã xuất bản' },
  draft: { tone: 'neutral', label: 'Bản nháp' },
  // copy: provisional, Figma DataProductCard (2028:470) has no state for a publish awaiting approval.
  pending_approval: { tone: 'info', label: 'Chờ duyệt' },
};

/* Figma "DataProductCard" (2028:470): Published / Draft / BreakingChange. */
function ProductCard({
  product,
  selected,
  onSelect,
}: {
  product: DataProduct;
  selected: boolean;
  onSelect: () => void;
}) {
  const badge = BADGE[product.breaking_change ? 'breaking' : product.status];
  const never = product.used_by_spaces === null;
  return (
    <button
      type="button"
      aria-pressed={selected}
      onClick={onSelect}
      className={cn(
        'flex flex-col items-start gap-da-md rounded-lg border bg-card p-da-xl text-left hover:bg-subtle',
        product.breaking_change ? 'border-warning' : selected ? 'border-primary' : 'border-border',
      )}
    >
      <span className="flex w-full items-center gap-da-md">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-primary">
          <Icon name="layers" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="text-foreground da-heading-3">{product.name}</span>
          <span className="truncate text-muted-foreground da-mono">{versionLabel(product)}</span>
        </span>
      </span>
      <span className="text-muted-foreground da-body-small">{product.description}</span>
      <Badge tone={badge.tone}>{badge.label}</Badge>
      <span className="flex gap-da-lg py-da-sm">
        <Stat label="Đang dùng" value={never ? '—' : `${product.used_by_spaces} space`} />
        <Stat
          label="Chất lượng"
          value={product.quality_pct === null ? '—' : `${product.quality_pct}%`}
        />
        <Stat label="Cập nhật" value={formatTime(product.refreshed_at)} />
      </span>
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <span className="flex flex-col">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-body-strong">{value}</span>
    </span>
  );
}
