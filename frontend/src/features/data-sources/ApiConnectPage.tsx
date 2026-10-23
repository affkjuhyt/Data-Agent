import { useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  backfillSource,
  createApiSource,
  getApiConfig,
  listSecretRefs,
  saveApiConfig,
  tryApiRequest,
  type ApiAuth,
  type ApiFieldMapping,
  type ApiRequest,
  type ApiSourceConfig,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Dữ liệu · Kết nối API" (2083:6509, UC-04-01 for the REST connector): four steps —
 * endpoint, authentication (a secret reference, never the secret), paging & schedule, and the
 * mapping of the response to columns, proposed by "Gọi thử". /data/connect_api opens a new
 * connection from the connector picker; /data/connect_api/<source id> edits an existing one and
 * shows its health on the right, with a backfill of the gap the source reported.
 */
type Step = 'endpoint' | 'auth' | 'paging' | 'mapping';

const STEPS: { value: Step; label: string }[] = [
  { value: 'endpoint', label: 'Endpoint' },
  { value: 'auth', label: 'Xác thực' },
  { value: 'paging', label: 'Phân trang & lịch' },
  { value: 'mapping', label: 'Ánh xạ phản hồi' },
];

const AUTH: { value: ApiAuth; label: string }[] = [
  { value: 'oauth2_client', label: 'OAuth2 client credentials' },
  { value: 'bearer', label: 'Bearer token' },
  { value: 'api_key', label: 'API key' },
];

const PAGING: { value: ApiRequest['pagination']; label: string }[] = [
  { value: 'page', label: 'Tham số page tăng dần' },
  { value: 'cursor', label: 'Con trỏ next trong phản hồi' },
  { value: 'none', label: 'Không phân trang' },
];

const SCHEDULE: { value: ApiRequest['schedule']; label: string }[] = [
  { value: 'hourly', label: 'Mỗi giờ' },
  { value: 'every_2h', label: 'Mỗi 2 giờ' },
  { value: 'daily', label: 'Hằng ngày' },
];

const TYPE_LABEL: Record<ApiFieldMapping['type'], string> = {
  text: 'text',
  number: 'số',
  date: 'ngày',
  boolean: 'đúng/sai',
};

const EMPTY: ApiRequest = {
  url: '',
  query: '',
  headers: [],
  auth: 'oauth2_client',
  secret_ref: '',
  pagination: 'page',
  max_pages: 200,
  schedule: 'every_2h',
  rate_limit_per_second: 10,
  retries: 3,
};

export default function ApiConnectPage() {
  const { routeId } = useAppState();
  const config = useV1(['getApiConfig', routeId], routeId ? () => getApiConfig(routeId) : null);
  if (!routeId) return <ApiForm key="new" config={null} />;
  return (
    <AsyncRegion state={config} what="cấu hình API" skeletonRows={8}>
      {(data) => <ApiForm key={data.source_id} config={data} />}
    </AsyncRegion>
  );
}

/** "page tăng tới khi rỗng · tối đa 200" */
function pagingText(r: ApiRequest): string {
  if (r.pagination === 'none') return 'Một lần gọi, không phân trang';
  const how = r.pagination === 'page' ? 'page tăng tới khi rỗng' : 'theo con trỏ next';
  return `${how} · tối đa ${r.max_pages}`;
}

/** "Mỗi 2 giờ · tăng dần theo updated_since" when the query carries {{last_run}}. */
function scheduleText(r: ApiRequest): string {
  const label = SCHEDULE.find((s) => s.value === r.schedule)?.label ?? r.schedule;
  const incremental = /([A-Za-z_]+)=\{\{last_run\}\}/.exec(r.query);
  return incremental ? `${label} · tăng dần theo ${incremental[1]}` : label;
}

const headersText = (r: ApiRequest) => r.headers.map((h) => `${h.name}: ${h.value}`).join('\n');

function parseHeaders(text: string): ApiRequest['headers'] {
  return text
    .split('\n')
    .map((line) => line.split(':'))
    .filter((parts) => parts.length >= 2 && parts[0].trim())
    .map(([name, ...rest]) => ({ name: name.trim(), value: rest.join(':').trim() }));
}

function ApiForm({ config }: { config: ApiSourceConfig | null }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const secrets = useV1(['listSecretRefs'], () => listSecretRefs());
  const [name, setName] = useState(config?.name ?? '');
  const [request, setRequest] = useState<ApiRequest>(config?.request ?? EMPTY);
  const [headers, setHeaders] = useState(headersText(config?.request ?? EMPTY));
  const [mapping, setMapping] = useState<ApiFieldMapping[]>(config?.mapping ?? []);
  const mappingView = useTableView(mapping, {
    search: (field) => `${field.path} ${field.column} ${TYPE_LABEL[field.type]} ${field.note ?? ''}`,
    filters: [{ id: 'type', label: 'Kiểu', value: (field) => TYPE_LABEL[field.type] }],
    sorts: [
      { id: 'path', value: (field) => field.path },
      { id: 'column', value: (field) => field.column },
      { id: 'type', value: (field) => TYPE_LABEL[field.type] },
      { id: 'note', value: (field) => field.note },
    ],
    pageSize: 50,
  });
  const [response, setResponse] = useState<{ status: number; ms: number; body: string } | null>(
    null,
  );
  const [step, setStep] = useState<Step>(config ? 'mapping' : 'endpoint');
  const [busy, setBusy] = useState<'try' | 'save' | 'backfill' | null>(null);

  const set = (patch: Partial<ApiRequest>) => setRequest((r) => ({ ...r, ...patch }));
  const full: ApiRequest = { ...request, headers: parseHeaders(headers) };

  const done: Record<Step, boolean> = {
    endpoint: Boolean(name.trim()) && /^https:\/\/\S+$/.test(request.url),
    auth: Boolean(request.secret_ref),
    paging: request.max_pages > 0 && request.rate_limit_per_second > 0,
    mapping: mapping.length > 0 && mapping.every((m) => m.column.trim()),
  };
  const canTry = done.endpoint && done.auth;
  const canSave = STEPS.every((s) => done[s.value]);

  const callTry = async () => {
    setBusy('try');
    try {
      const result = await tryApiRequest(full);
      setResponse({ status: result.status, ms: result.duration_ms, body: result.body });
      // Keep columns the user already renamed; add fields the response newly shows.
      setMapping((current) =>
        result.mapping.map((m) => current.find((c) => c.path === m.path) ?? m),
      );
      setStep('mapping');
    } catch (e) {
      toast({ tone: 'error', message: `Gọi thử không được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  const save = async () => {
    setBusy('save');
    try {
      if (config) {
        await saveApiConfig(config.source_id, { request: full, mapping });
        toast({ tone: 'success', message: `Đã lưu ${name}. Lịch chạy giữ nguyên.` });
        navigate('data', 'SOURCE', config.source_id);
      } else {
        const source = await createApiSource({ name: name.trim(), request: full, mapping });
        toast({ tone: 'success', message: `Đã kết nối ${source.name} và lên lịch chạy.` });
        navigate('data', 'SOURCE', source.id);
      }
    } catch (e) {
      toast({ tone: 'error', message: `Không lưu được. ${(e as Error).message}` });
      setBusy(null);
    }
  };

  const backfill = async (gap: NonNullable<NonNullable<ApiSourceConfig['health']>['gap']>) => {
    if (!config) return;
    setBusy('backfill');
    try {
      await backfillSource(config.source_id, { from: gap.from, to: gap.to });
      toast({ tone: 'success', message: `Đã xếp lịch chạy bù ${gap.label}.` });
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy bù được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  const health = config?.health ?? null;

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl py-da-lg">
        <PageHeader
          breadcrumb={['Nguồn', 'Kết nối API']}
          title={config ? `${config.name} · REST API` : 'Kết nối REST API'}
          actions={
            <>
              <Button
                icon="play"
                loading={busy === 'try'}
                disabled={!canTry || busy !== null}
                onClick={() => void callTry()}
              >
                Gọi thử
              </Button>
              <Button
                kind="primary"
                loading={busy === 'save'}
                disabled={!canSave || busy !== null}
                onClick={() => void save()}
              >
                Lưu &amp; lên lịch
              </Button>
            </>
          }
        />
        <ol aria-label="Các bước" className="grid grid-cols-4 gap-da-xs">
          {STEPS.map((s, i) => (
            <li key={s.value}>
              <button
                type="button"
                aria-current={step === s.value ? 'step' : undefined}
                onClick={() => setStep(s.value)}
                className={cn(
                  'flex w-full items-center gap-da-sm rounded-md border px-da-md py-da-sm text-left hover:bg-subtle',
                  step === s.value ? 'border-primary' : 'border-border',
                )}
              >
                <span className="text-primary da-mono">
                  {done[s.value] ? '✓' : i + 1}
                  {done[s.value] && <span className="sr-only"> xong</span>}
                </span>
                <span className="min-w-0 flex-1 truncate text-foreground da-label">{s.label}</span>
              </button>
            </li>
          ))}
        </ol>

        {step === 'endpoint' && (
          <Panel>
            <Field labelTone="muted" label="Tên nguồn">
              <Input className="w-full" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field labelTone="muted" label="URL" hint="Chỉ nhận https. Phương thức GET.">
              <Input
                className="w-full"
                value={request.url}
                placeholder="https://card-api.noibo/v2/statements"
                onChange={(e) => set({ url: e.target.value.trim() })}
              />
            </Field>
            <Field
              labelTone="muted"
              label="Tham số truy vấn"
              hint="{{last_run}} là thời điểm lần chạy trước, {{page}} là số trang."
            >
              <Input
                className="w-full"
                value={request.query}
                onChange={(e) => set({ query: e.target.value })}
              />
            </Field>
            <Field labelTone="muted" label="Header" hint="Mỗi dòng một header, dạng Tên: giá trị.">
              <Textarea
                rows={3}
                value={headers}
                onChange={(e) => setHeaders(e.target.value)}
                className="w-full"
                mono
              />
            </Field>
          </Panel>
        )}

        {step === 'auth' && (
          <Panel>
            <Field labelTone="muted" label="Kiểu xác thực">
              <Select
                label="Kiểu xác thực"
                value={request.auth}
                onChange={(auth) => set({ auth })}
                options={AUTH}
              />
            </Field>
            <Field
              labelTone="muted"
              label="Thông tin xác thực"
              hint="Không lưu khoá hay mật khẩu trong Data Agent."
            >
              <Select
                label="Thông tin xác thực"
                placeholder="Chọn khoá trong kho bí mật"
                value={request.secret_ref || null}
                onChange={(secret_ref) => set({ secret_ref })}
                options={(secrets.data?.items ?? []).map((s) => ({ value: s.ref, label: s.label }))}
                disabled={!secrets.data}
              />
            </Field>
          </Panel>
        )}

        {step === 'paging' && (
          <Panel>
            <div className="grid grid-cols-2 gap-da-lg">
              <Field labelTone="muted" label="Phân trang">
                <Select
                  label="Phân trang"
                  value={request.pagination}
                  onChange={(pagination) => set({ pagination })}
                  options={PAGING}
                />
              </Field>
              <Field labelTone="muted" label="Tối đa số trang mỗi lần chạy">
                <Input
                  className="w-full"
                  inputMode="numeric"
                  value={String(request.max_pages)}
                  onChange={(e) => set({ max_pages: Number(e.target.value) || 0 })}
                />
              </Field>
              <Field labelTone="muted" label="Lịch">
                <Select
                  label="Lịch"
                  value={request.schedule}
                  onChange={(schedule) => set({ schedule })}
                  options={SCHEDULE}
                />
              </Field>
              <Field labelTone="muted" label="Giới hạn yêu cầu/giây">
                <Input
                  className="w-full"
                  inputMode="numeric"
                  value={String(request.rate_limit_per_second)}
                  onChange={(e) => set({ rate_limit_per_second: Number(e.target.value) || 0 })}
                />
              </Field>
              <Field labelTone="muted" label="Số lần thử lại">
                <Input
                  className="w-full"
                  inputMode="numeric"
                  value={String(request.retries)}
                  onChange={(e) => set({ retries: Number(e.target.value) || 0 })}
                />
              </Field>
            </div>
          </Panel>
        )}

        {step === 'mapping' && (
          <>
            <div className="grid grid-cols-2 items-start gap-da-md max-[1279px]:grid-cols-1">
              <section
                aria-label="Yêu cầu"
                className="flex flex-col gap-da-sm rounded-md border border-border bg-card px-da-lg py-da-md"
              >
                <h2 className="text-muted-foreground da-label">Yêu cầu</h2>
                <pre className="whitespace-pre-wrap break-all text-foreground da-mono">
                  {[
                    `GET ${request.url || '—'}`,
                    request.query ? `?${request.query}` : null,
                    `Authorization: ${request.auth === 'api_key' ? 'X-API-Key' : 'Bearer'} ••••••  (${AUTH.find((a) => a.value === request.auth)?.label})`,
                    headers.trim() || null,
                  ]
                    .filter(Boolean)
                    .join('\n')}
                </pre>
                <KV label="Phân trang">{pagingText(request)}</KV>
                <KV label="Lịch">{scheduleText(full)}</KV>
                <KV label="Giới hạn">
                  {request.rate_limit_per_second} yêu cầu/giây · thử lại {request.retries} lần
                </KV>
              </section>
              <section
                aria-label="Phản hồi mẫu"
                className="flex flex-col gap-da-sm rounded-md border border-border px-da-lg py-da-md"
              >
                <div className="flex items-center gap-da-sm">
                  <h2 className="text-muted-foreground da-label">Phản hồi mẫu</h2>
                  {response && (
                    <StatusDot tone={response.status < 300 ? 'success' : 'error'}>
                      {response.status} · {formatNumber(response.ms)} ms
                    </StatusDot>
                  )}
                </div>
                {response ? (
                  <pre className="whitespace-pre-wrap break-all text-foreground da-mono">
                    {response.body}
                  </pre>
                ) : (
                  <p className="text-muted-foreground da-body-small">
                    Bấm Gọi thử để gọi một lần với cấu hình này; agent đọc phản hồi và đề xuất ánh
                    xạ. Không có gì được lưu.
                  </p>
                )}
              </section>
            </div>
            {mapping.length > 0 && (
              <>
              <TableToolbar view={mappingView} placeholder="Tìm trường…" searchLabel="Tìm trường" />
              <div className="overflow-hidden rounded-md border border-border bg-card">
                <table className="w-full table-fixed border-collapse">
                  <thead>
                    <tr className="bg-subtle text-left text-muted-foreground da-label">
                      <SortableTh
                        view={mappingView}
                        column="path"
                        className="w-[206px] px-da-md py-da-sm font-medium"
                      >
                        Trường JSON
                      </SortableTh>
                      <SortableTh
                        view={mappingView}
                        column="column"
                        className="w-[206px] px-da-md py-da-sm font-medium"
                      >
                        Cột đích
                      </SortableTh>
                      <SortableTh
                        view={mappingView}
                        column="type"
                        className="w-[108px] px-da-md py-da-sm font-medium"
                      >
                        Kiểu
                      </SortableTh>
                      <SortableTh view={mappingView} column="note" className="px-da-md py-da-sm font-medium">
                        Ghi chú
                      </SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {mappingView.rows.map((m) => (
                      <tr key={m.path} className="border-t border-border">
                        <td className="truncate px-da-md py-da-sm text-foreground da-mono">
                          {m.path}
                        </td>
                        <td className="px-da-md py-da-xs">
                          <Input
                            className="w-full"
                            aria-label={`Cột đích của ${m.path}`}
                            value={m.column}
                            onChange={(e) =>
                              setMapping((all) =>
                                all.map((x) =>
                                  x.path === m.path ? { ...x, column: e.target.value } : x,
                                ),
                              )
                            }
                          />
                        </td>
                        <td className="px-da-md py-da-sm text-foreground da-body-small">
                          {TYPE_LABEL[m.type]}
                        </td>
                        <td className="px-da-md py-da-sm text-foreground da-body-small">
                          {m.note ?? ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <TablePager view={mappingView} />
              </>
            )}
          </>
        )}
      </div>
      {health && (
        <aside
          aria-label="Sức khoẻ kết nối"
          className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-card px-da-xl py-da-lg max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t"
        >
          <h2 className="text-foreground da-heading-3">Sức khoẻ kết nối</h2>
          <KV label="Lần chạy cuối">{health.last_run_text}</KV>
          <KV label="Độ trễ TB">{formatNumber(health.latency_ms)} ms</KV>
          <KV label="Lỗi 7 ngày">{health.errors_7d_text}</KV>
          {health.gap && (
            <>
              <KV label="Thiếu dữ liệu">{health.gap.text}</KV>
              <Button
                className="self-start"
                loading={busy === 'backfill'}
                disabled={busy !== null}
                onClick={() => void backfill(health.gap!)}
              >
                Chạy bù {health.gap.label}
              </Button>
            </>
          )}
        </aside>
      )}
    </div>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <div className="flex max-w-[640px] flex-col gap-da-lg rounded-md border border-border bg-card px-da-lg py-da-md">
      {children}
    </div>
  );
}

function KV({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-da-md">
      <span className="w-[90px] shrink-0 text-muted-foreground da-caption">{label}</span>
      <span className="min-w-0 flex-1 text-foreground da-body-small">{children}</span>
    </div>
  );
}
