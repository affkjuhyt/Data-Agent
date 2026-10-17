import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { MatrixTable, SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  approveCrawlSchema,
  getCrawlSource,
  listCrawlSchemas,
  testCrawlSchema,
  type CrawlSchemaVersion,
} from '../../lib/v1/crawl';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P14 · Suy ngược schema · Chạy thử" (2389:15042, UC-14-02): one source's extraction schema.
 * Left, a raw sample as the agent read it (its hash, the repeating region, the row the selected
 * field comes from) and the samples it read; right, the proposed fields and the test on held-out
 * pages. A field the agent could not map with confidence waits for a person to pick its canonical
 * attribute; approval needs that choice and 95% of required fields right (BR-151).
 */
const fmt = (n: number | null) => (n === null ? '—' : n.toFixed(2).replace('.', ','));
const pct = (n: number) => `${n.toFixed(1).replace('.', ',')}%`;
const THRESHOLD = 95;

export default function CrawlSchemaPage() {
  const { routeId } = useAppState();
  const source = useV1(routeId ? () => getCrawlSource(routeId) : null, [routeId]);
  const schemas = useV1(routeId ? () => listCrawlSchemas(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={schemas} what="schema">
      {(page) =>
        page.items[0] ? (
          <SchemaBody
            key={page.items[0].id}
            sourceName={source.data?.name ?? ''}
            schema={page.items[0]}
            onChanged={schemas.reload}
          />
        ) : (
          <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
            Nguồn chưa có schema; Agent Thu thập đang đọc mẫu.
          </p>
        )
      }
    </AsyncRegion>
  );
}

function SchemaBody({
  sourceName,
  schema,
  onChanged,
}: {
  sourceName: string;
  schema: CrawlSchemaVersion;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string>();
  const [busy, setBusy] = useState<'test' | 'approve' | null>(null);
  const [error, setError] = useState<string>();
  const fields = useTableView(schema.fields, {
    search: (field) => `${field.name} ${field.type} ${field.locator} ${field.canonical ?? ''}`,
    filters: [{ id: 'type', label: 'Kiểu', value: (field) => field.type }],
    sorts: [
      { id: 'name', value: (field) => field.name },
      { id: 'type', value: (field) => field.type },
      { id: 'unit', value: (field) => field.unit ?? '' },
      { id: 'required', value: (field) => (field.required ? 'Có' : 'Không') },
      { id: 'key', value: (field) => (field.key ? 'Có' : '') },
      { id: 'locator', value: (field) => field.locator },
      { id: 'confidence', value: (field) => field.confidence },
    ],
    pageSize: 50,
  });
  const draft = schema.status === 'draft' || schema.status === 'testing';
  // Only a field the agent offers choices for waits on a person; the api offers none yet.
  const unsure = schema.fields.filter((f) => !f.canonical && f.canonical_options.length > 0);
  const missing = unsure.filter((f) => !choices[f.name]);
  const test = schema.test;
  const passes = test ? test.required_correct_pct >= THRESHOLD : false;
  const blocker = !test
    ? 'Chạy thử trước khi duyệt.'
    : !passes
      ? `Dưới ${THRESHOLD}% trường bắt buộc đúng (BR-151).`
      : missing.length
        ? `Chọn thuộc tính chuẩn cho ${missing.map((f) => f.name).join(', ')}.`
        : null;

  const run = async (verb: 'test' | 'approve') => {
    setBusy(verb);
    setError(undefined);
    try {
      if (verb === 'test') {
        await testCrawlSchema(schema.id);
        toast({ tone: 'info', message: 'Đang chạy thử trên mẫu giữ lại' });
      } else {
        await approveCrawlSchema(schema.id, choices);
        toast({ tone: 'success', message: `Đã duyệt v${schema.version}; lịch thu bắt đầu chạy` });
      }
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
        breadcrumb={['Dữ liệu', 'Thu thập ngoài', sourceName]}
        title={
          draft
            ? `Suy ngược schema · bản nháp v${schema.version}`
            : `Schema · v${schema.version} đang dùng`
        }
        description={schema.summary_text}
        actions={
          <>
            <Button loading={busy === 'test'} disabled={busy !== null} onClick={() => run('test')}>
              Chạy thử lại
            </Button>
            {draft &&
              (blocker ? (
                <Tooltip content={blocker}>
                  <Button kind="primary" disabled>
                    Duyệt schema
                  </Button>
                </Tooltip>
              ) : (
                <Button
                  kind="primary"
                  loading={busy === 'approve'}
                  disabled={busy !== null}
                  onClick={() => run('approve')}
                >
                  Duyệt schema
                </Button>
              ))}
          </>
        }
      />
      <div className="flex items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl max-[1279px]:flex-col">
        <div className="flex w-[430px] shrink-0 flex-col gap-da-xl max-[1279px]:w-full">
          {schema.sample && (
            <Card title={`Bản gốc · ${schema.sample.url}`}>
              <p className="text-muted-foreground da-mono">{schema.sample.fetched_text}</p>
              <MatrixTable
                columns={schema.sample.columns}
                rows={schema.sample.rows}
                highlightRow={schema.sample.highlighted_row}
                className="w-full border-collapse da-body-small"
                searchLabel="Tìm trong bản gốc"
              />
              <p className="text-muted-foreground da-caption">{schema.sample.footer_text}</p>
              <p className="text-foreground da-mono">{schema.sample.repeat_text}</p>
            </Card>
          )}
          {schema.samples_read.length > 0 && (
            <Card title="Mẫu đã đọc">
              <ul className="flex flex-col gap-da-lg text-foreground da-body-small">
                {schema.samples_read.map((line) => (
                  <li key={line}>· {line}</li>
                ))}
              </ul>
            </Card>
          )}
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-da-xl max-[1279px]:w-full">
          {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}
          <Card
            title={`Schema ${draft ? 'đề xuất' : 'đang dùng'} · ${schema.fields.length} trường`}
          >
            <TableToolbar view={fields} placeholder="Tìm trường…" searchLabel="Tìm trường" />
            <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
              <thead>
                <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
                  <SortableTh view={fields} column="name" className="w-[120px] px-da-lg py-da-md font-normal">
                    Trường
                  </SortableTh>
                  <SortableTh view={fields} column="type" className="w-[72px] px-da-lg py-da-md font-normal">
                    Kiểu
                  </SortableTh>
                  <SortableTh view={fields} column="unit" className="w-[64px] px-da-lg py-da-md font-normal">
                    Đơn vị
                  </SortableTh>
                  <SortableTh view={fields} column="required" className="w-[64px] px-da-lg py-da-md font-normal">
                    Bắt buộc
                  </SortableTh>
                  <SortableTh view={fields} column="key" className="w-[48px] px-da-lg py-da-md font-normal">
                    Khoá
                  </SortableTh>
                  <SortableTh view={fields} column="locator" className="px-da-lg py-da-md font-normal">
                    Vị trí trích
                  </SortableTh>
                  <th className="w-[170px] px-da-lg py-da-md font-normal">Thuộc tính chuẩn</th>
                  <SortableTh view={fields} column="confidence" className="w-[56px] px-da-lg py-da-md font-normal">
                    Độ tin
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {fields.rows.map((f) => (
                  <tr
                    key={f.name}
                    onClick={() => setSelected(f.name)}
                    className={cn(
                      'border-b border-border last:border-b-0 text-foreground',
                      (selected === f.name || (!selected && unsure.includes(f))) && 'bg-accent',
                    )}
                  >
                    <td className="truncate px-da-lg py-[10px] da-mono">{f.name}</td>
                    <td className="px-da-lg py-[10px]">{f.type}</td>
                    <td className="px-da-lg py-[10px]">{f.unit ?? '—'}</td>
                    <td className="px-da-lg py-[10px]">{f.required ? 'Có' : 'Không'}</td>
                    <td className="px-da-lg py-[10px]">{f.key ? 'Có' : '—'}</td>
                    <td className="px-da-lg py-[10px]">{f.locator}</td>
                    <td className="px-da-lg py-[6px]">
                      {f.canonical ??
                        (!draft || !unsure.includes(f) ? (
                          '—'
                        ) : (
                          <Select
                            label={`Thuộc tính chuẩn của ${f.name}`}
                            value={choices[f.name] ?? null}
                            placeholder="Chưa chắc · chọn"
                            onChange={(v) => setChoices({ ...choices, [f.name]: v })}
                            options={f.canonical_options.map((o) => ({ value: o, label: o }))}
                          />
                        ))}
                    </td>
                    <td
                      className={cn(
                        'px-da-lg py-[10px]',
                        f.confidence !== null && f.confidence < 0.8
                          ? 'text-warning'
                          : 'text-foreground',
                      )}
                    >
                      {fmt(f.confidence)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <TablePager view={fields} />
          </Card>
          <Card title="Chạy thử trên mẫu giữ lại">
            {test ? (
              <div className="flex gap-da-xl max-[1023px]:flex-wrap">
                <Stat
                  label="Trang giữ lại"
                  value={String(test.pages)}
                  note={test.records === null ? '' : `${test.records} bản ghi trích được`}
                />
                <Stat
                  label="Trường bắt buộc đúng"
                  value={pct(test.required_correct_pct)}
                  note={
                    passes
                      ? `đạt ngưỡng ${THRESHOLD}% (BR-151)`
                      : `dưới ngưỡng ${THRESHOLD}% (BR-151)`
                  }
                  tone={passes ? 'success' : 'error'}
                />
                <Stat
                  label="Cần người quyết"
                  value={`${missing.length} trường`}
                  note={
                    missing.length
                      ? `${missing.map((f) => f.name).join(', ')} chưa ánh xạ chắc`
                      : 'đã chọn đủ'
                  }
                  tone={missing.length ? 'warning' : 'success'}
                />
              </div>
            ) : (
              <p className="text-muted-foreground da-body-small">Chưa chạy thử.</p>
            )}
            <p className="text-muted-foreground da-caption">
              Duyệt xong, phiên bản v{schema.version} chuyển active và lịch thu bắt đầu chạy. Sửa vị
              trí trích trên bản gốc đang được làm; hiện agent sinh lại bộ chọn khi chạy thử lại.
            </p>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}

function Stat({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'success' | 'warning' | 'error';
}) {
  return (
    <div className="flex min-w-[160px] flex-1 flex-col gap-[6px] rounded-md border border-border bg-card px-da-xl py-[14px]">
      <p className="text-muted-foreground da-caption">{label}</p>
      <p className="text-foreground da-metric">{value}</p>
      <p
        className={cn(
          'da-caption',
          tone === 'success'
            ? 'text-success'
            : tone === 'warning'
              ? 'text-warning'
              : tone === 'error'
                ? 'text-error'
                : 'text-foreground',
        )}
      >
        {note}
      </p>
    </div>
  );
}
