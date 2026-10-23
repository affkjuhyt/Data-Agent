import { useState } from 'react';
import { Checkbox } from '../../design-system/Checkbox';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import { listScopeColumns, listSpaceDatasets, type RowScope } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';

/*
 * The row scope of a membership or an invitation, from the Data Space's own data: a table, one
 * of its columns, and the values the latest profile found in it. The query engine applies the
 * scope as `column IN (values)` to every table of the Data Space that has the column, so the
 * table only says where the values come from.
 */
export function RowScopePicker({
  spaceId,
  value,
  onChange,
}: {
  spaceId: string;
  /** null: every row. A column with no value yet is a scope still being chosen. */
  value: RowScope | null;
  onChange: (scope: RowScope | null) => void;
}) {
  const [dataset, setDataset] = useState<string | null>(null);
  const limited = value !== null;
  const datasets = useV1(
    ['listSpaceDatasets', spaceId],
    limited ? () => listSpaceDatasets(spaceId) : null,
  );
  const columns = useV1(
    ['listScopeColumns', spaceId, dataset],
    limited && dataset ? () => listScopeColumns(spaceId, dataset) : null,
  );
  const column = value ? (Object.keys(value)[0] ?? null) : null;
  const chosen = column && value ? value[column] : [];
  const values = columns.data?.find((c) => c.name === column)?.values ?? [];

  return (
    <div className="flex flex-col gap-da-md">
      <Field label="Phạm vi dòng">
        <Select<'all' | 'limited'>
          label="Phạm vi dòng"
          options={[
            { value: 'all', label: 'Toàn bộ dòng' },
            { value: 'limited', label: 'Chỉ các dòng có giá trị đã chọn' },
          ]}
          value={limited ? 'limited' : 'all'}
          onChange={(mode) => onChange(mode === 'all' ? null : {})}
        />
      </Field>
      {limited && (
        <>
          <Field label="Lấy giá trị từ bảng">
            <Select
              label="Lấy giá trị từ bảng"
              placeholder={datasets.loading ? 'Đang tải bảng…' : 'Chọn bảng'}
              options={(datasets.data?.items ?? []).map((d) => ({
                value: d.id,
                label: d.display_name ?? d.table_name,
              }))}
              value={dataset}
              onChange={(id) => {
                setDataset(id);
                onChange({});
              }}
            />
          </Field>
          {dataset && (
            <Field label="Cột">
              <Select
                label="Cột"
                placeholder={
                  columns.loading
                    ? 'Đang tải cột…'
                    : columns.data?.length === 0
                      ? 'Bảng chưa có cột nào được quét giá trị'
                      : 'Chọn cột'
                }
                options={(columns.data ?? []).map((c) => ({ value: c.name, label: c.label }))}
                value={column}
                onChange={(name) => onChange({ [name]: [] })}
              />
            </Field>
          )}
          {column && (
            <fieldset className="flex flex-col gap-da-xs">
              <legend className="text-foreground da-label">
                Giá trị của {column} (theo lần quét gần nhất)
              </legend>
              {values.map((v) => (
                <Checkbox
                  key={v}
                  checked={chosen.includes(v)}
                  onChange={(on) =>
                    onChange({
                      [column]: on ? [...chosen, v] : chosen.filter((x) => x !== v),
                    })
                  }
                >
                  {v}
                </Checkbox>
              ))}
            </fieldset>
          )}
        </>
      )}
    </div>
  );
}

/** A scope ready to save: every row, or one column with at least one value. */
export const completeScope = (scope: RowScope | null): boolean =>
  scope === null || Object.values(scope).some((values) => values.length > 0);
