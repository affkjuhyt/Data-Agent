import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { KIND_LABEL, listCatalog, type CatalogAsset } from '../../lib/v1/catalog';
import { useV1 } from '../../lib/v1/useV1';

/*
 * R1-04: one list of the tables, columns, documents, series and APIs a Data Space answers from.
 * Each row opens the asset. "Nguồn" opens the connection, the collection, or the forecast
 * book the asset was taken from.
 */
export default function CatalogPage() {
  const { navigate } = useAppState();
  const catalog = useV1(['listCatalog'], listCatalog);
  const items = catalog.data?.items ?? [];
  const view = useTableView(items, {
    search: (asset) => `${asset.name} ${asset.source} ${KIND_LABEL[asset.kind]}`,
    filters: [
      {
        id: 'kind',
        label: 'Loại',
        value: (asset) => asset.kind,
        text: (kind) => KIND_LABEL[kind as CatalogAsset['kind']] ?? kind,
      },
    ],
    sorts: [
      { id: 'name', value: (asset) => asset.name },
      { id: 'kind', value: (asset) => KIND_LABEL[asset.kind] },
      { id: 'source', value: (asset) => asset.source },
    ],
  });

  const open = (link: CatalogAsset['open']) => navigate(link.area, link.page, link.id);

  return (
    <div className="flex flex-col gap-[20px] px-da-3xl py-da-xl">
      <PageHeader
        title="Danh mục"
        description="Bảng, cột, tài liệu, chuỗi và API của Data Space này. Mỗi dòng mở đúng tài sản; nguồn nằm cạnh tên."
      />
      <AsyncRegion state={catalog} what="danh mục">
        {() => (
          <>
            <TableToolbar
              view={view}
              placeholder="Tìm bảng, cột, tài liệu, chuỗi, API…"
              searchLabel="Tìm trong danh mục"
            />
            <table className="w-full border-collapse" aria-label="Danh mục">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh
                    view={view}
                    column="kind"
                    className="w-[140px] px-da-lg py-da-md font-normal"
                  >
                    Loại
                  </SortableTh>
                  <SortableTh view={view} column="name" className="px-da-lg py-da-md font-normal">
                    Tên
                  </SortableTh>
                  <SortableTh
                    view={view}
                    column="source"
                    className="w-[280px] px-da-lg py-da-md font-normal"
                  >
                    Nguồn
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.length === 0
                  ? tableEmptyRow(3)
                  : view.rows.map((asset) => (
                      <tr key={`${asset.kind}:${asset.id}`} className="border-b border-border">
                        <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                          {KIND_LABEL[asset.kind]}
                        </td>
                        <td className="px-da-lg py-da-md">
                          <button
                            type="button"
                            className="text-left text-foreground da-body-strong"
                            onClick={() => open(asset.open)}
                          >
                            {asset.name}
                          </button>
                        </td>
                        <td className="px-da-lg py-da-md da-body-small">
                          {asset.sourceLink ? (
                            <button
                              type="button"
                              className="text-left text-foreground"
                              onClick={() => open(asset.sourceLink!)}
                            >
                              {asset.source}
                            </button>
                          ) : (
                            <span className="text-muted-foreground">{asset.source}</span>
                          )}
                        </td>
                      </tr>
                    ))}
              </tbody>
            </table>
            <TablePager view={view} />
          </>
        )}
      </AsyncRegion>
    </div>
  );
}
