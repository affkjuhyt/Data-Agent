import { useState, type CSSProperties } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  createDataProduct,
  getMart,
  listMarts,
  publishDataProduct,
  type DataMart,
  type MartTable,
} from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';
import { SuggestMartDialog } from './SuggestMartDialog';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P06 · Data mart & sản phẩm dữ liệu" (2290:18136, UC-06-06 step 1): a mart as its star model
 * (fact in the middle, dimensions around it, the agent's suggested dimension dashed) and, on the
 * right, what publishing it as a data product commits to. Opened from the rail without an id it
 * shows the first mart. The dashed dimension opens "Hộp thoại · suggestMart"; "Kiểm tra" opens the
 * quality rules, which are what a mart is checked against on every refresh.
 */
export default function DataMartPage() {
  const { routeId } = useAppState();
  const marts = useV1(['listMarts', routeId], routeId ? null : listMarts);
  const id = routeId ?? marts.data?.items[0]?.id;

  if (!id) {
    return (
      <div className="px-da-3xl py-da-2xl">
        <AsyncRegion
          state={marts}
          what="data mart"
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState
              icon="database"
              title="Chưa có data mart"
              description="Data mart được tạo từ các bảng đã chuẩn bị bằng pipeline."
            />
          }
        >
          {() => null}
        </AsyncRegion>
      </div>
    );
  }
  return <MartView id={id} />;
}

function MartView({ id }: { id: string }) {
  const { navigate } = useAppState();
  const mart = useV1(['getMart', id], () => getMart(id));
  const toast = useToast();
  const [suggesting, setSuggesting] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const m = mart.data;

  const publish = async () => {
    if (!m) return;
    setPublishing(true);
    try {
      const created = await createDataProduct({
        mart_id: m.id,
        name: m.product_draft.name,
        version: m.product_draft.version,
      });
      const done = await publishDataProduct(created.id);
      // Figma Toast · published (2094:7831) and · certSent (2094:7844); "Xem" opens the product.
      const view = { label: 'Xem', onClick: () => navigate('data', 'DATA_PRODUCTS', done.id) };
      toast(
        done.status === 'published'
          ? { tone: 'success', message: 'Đã xuất bản · người dùng nhận thông báo', action: view }
          : {
              tone: 'success',
              message: `Đã gửi chứng nhận ${done.name} v${done.version} · chờ chủ sở hữu`,
              action: view,
            },
      );
    } catch (e) {
      toast({ tone: 'error', message: `Không phát hành được: ${(e as Error).message}` });
    } finally {
      setPublishing(false);
    }
  };

  // A mart in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(mart.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Data mart', m?.name ?? '…']}
        title={m?.name ?? 'Data mart'}
        description={m?.summary_text}
        actions={
          <>
            <Button onClick={() => navigate('data', 'QUALITY')}>Kiểm tra</Button>
            <Button kind="primary" loading={publishing} disabled={!m} onClick={publish}>
              Phát hành sản phẩm dữ liệu
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto bg-background">
          <AsyncRegion state={mart} what="data mart" skeletonRows={6}>
            {(d) => <StarModel mart={d} onSuggestion={() => setSuggesting(true)} />}
          </AsyncRegion>
        </div>
        {m && (
          <aside className="flex w-[380px] shrink-0 flex-col gap-[10px] border-l border-border px-da-2xl pt-[20px]">
            <h2 className="text-foreground da-heading-3">Phát hành thành sản phẩm dữ liệu</h2>
            <Fact label="Tên" value={m.product_draft.name} />
            <Fact label="Phiên bản" value={m.product_draft.version_text} />
            <Fact label="Cam kết cột" value={m.product_draft.contract_text} />
            <Fact label="Độ mới" value={m.product_draft.freshness_text} />
            <Fact label="Chất lượng" value={m.product_draft.quality_text} />
            <Fact label="Bên dùng" value={m.product_draft.consumers_text} />
            {m.product_draft.breaking_note && (
              <p className="text-warning da-body-small">{m.product_draft.breaking_note}</p>
            )}
            <p className="text-muted-foreground da-caption">UC-06-06</p>
          </aside>
        )}
      </div>
      {m?.suggestion && (
        <SuggestMartDialog
          open={suggesting}
          mart={m}
          onClose={() => setSuggesting(false)}
          onApplied={() => {
            setSuggesting(false);
            mart.reload();
          }}
        />
      )}
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-[2px] pb-da-sm">
      <p className="text-muted-foreground da-caption">{label}</p>
      <p className="text-foreground da-body-small">{value}</p>
    </div>
  );
}

/*
 * The star as drawn: the fact table in the middle, up to six dimensions in fixed slots around
 * it, a line from each to the fact. The slots are the Figma positions (canvas 780×560).
 */
const FACT = { left: 300, top: 220, width: 220 };
const SLOTS = [
  { left: 40, top: 40 },
  { left: 560, top: 40 },
  { left: 40, top: 430 },
  { left: 560, top: 430 },
  { left: 580, top: 240 },
  { left: 20, top: 240 },
];
const DIM_WIDTH = 180;

function StarModel({ mart, onSuggestion }: { mart: DataMart; onSuggestion: () => void }) {
  const dims = mart.dimensions.slice(0, SLOTS.length);
  const factCentre = { x: FACT.left + FACT.width / 2, y: FACT.top + 60 };
  return (
    <div className="relative h-[560px] w-[780px]">
      <svg className="absolute inset-0" width={780} height={560} aria-hidden>
        {dims.map((d, i) => (
          <line
            key={d.name}
            x1={SLOTS[i].left + DIM_WIDTH / 2}
            y1={SLOTS[i].top + 30}
            x2={factCentre.x}
            y2={factCentre.y}
            stroke="var(--border-strong)"
            strokeDasharray={d.suggested ? '4 3' : undefined}
          />
        ))}
      </svg>
      <TableBox table={mart.fact} strong style={{ ...FACT }} />
      {dims.map((d, i) =>
        d.suggested && mart.suggestion ? (
          <button
            key={d.name}
            type="button"
            onClick={onSuggestion}
            aria-label={`${d.name}: chiều agent gợi ý, xem đề xuất`}
            className="absolute text-left"
            style={{ ...SLOTS[i], width: DIM_WIDTH }}
          >
            <TableBox table={d} dashed />
          </button>
        ) : (
          <TableBox key={d.name} table={d} style={{ ...SLOTS[i], width: DIM_WIDTH }} />
        ),
      )}
    </div>
  );
}

function TableBox({
  table,
  strong = false,
  dashed = false,
  style,
}: {
  table: MartTable;
  strong?: boolean;
  dashed?: boolean;
  style?: CSSProperties;
}) {
  return (
    <div
      style={style}
      className={cn(
        'flex flex-col overflow-hidden rounded-sm border bg-card',
        style && 'absolute',
        strong ? 'border-foreground' : 'border-border',
        dashed && 'border-dashed border-border-strong hover:bg-subtle',
      )}
    >
      <p className="border-b border-border px-[10px] py-da-sm text-foreground da-mono">
        {table.name}
      </p>
      {table.lines.map((line) => (
        <p
          key={line}
          className="whitespace-nowrap px-[10px] py-[3px] text-muted-foreground da-caption"
        >
          {line}
        </p>
      ))}
    </div>
  );
}
