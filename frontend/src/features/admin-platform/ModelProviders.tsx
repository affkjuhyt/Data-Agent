import { useState, type ComponentProps, type ReactNode } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { Toggle } from '../../design-system/Toggle';
import { useToast } from '../../design-system/Toast';
import { formatDate, formatNumber } from '../../lib/formatVN';
import {
  addProvider,
  getRoutingLog,
  listProviders,
  saveAiPolicy,
  testProvider,
  type AiPolicy,
  type AiProvider,
  type RoutingDecision,
  type RoutingLog,
  type RoutingLogEntry,
} from '../../lib/v1/adminPlatform';
import { useV1, type V1State } from '../../lib/v1/useV1';
import { formatHour, formatLatency, pct } from './format';

type StatusTone = ComponentProps<typeof StatusDot>['tone'];

/*
 * The Provider and "Nhật ký định tuyến" tabs of P11 (2436:15566, UC-11-05). Calls run on the
 * local vLLM by default; a frontier provider is used only for context labelled Công khai, only
 * while the org switch is on and only once AC06 approved it (BR-180). Every routing decision is
 * audited with its reason, provider and asker (BR-181) — the log tab is that audit.
 */

const RETENTION: Record<AiProvider['zero_retention'], { tone: StatusTone; label: string } | null> =
  {
    not_applicable: null,
    committed: { tone: 'success', label: 'Có cam kết' },
    verifying: { tone: 'warning', label: 'Đang xác minh' },
    none: { tone: 'error', label: 'Không cam kết' },
  };

const AC06: Record<AiProvider['ac06_status'], { tone: StatusTone; label: string }> = {
  approved: { tone: 'success', label: 'Đã duyệt' },
  pending: { tone: 'warning', label: 'Chờ duyệt AC06' },
  rejected: { tone: 'error', label: 'AC06 từ chối' },
};

const DECISION: Record<RoutingDecision, { tone: StatusTone; label: string }> = {
  frontier: { tone: 'info', label: 'Frontier' },
  blocked: { tone: 'error', label: 'Chặn' },
  local: { tone: 'success', label: 'Tại chỗ' },
};

const TH = 'px-da-lg py-da-md text-left font-normal text-muted-foreground da-caption';
const TD = 'px-da-lg py-[10px] align-middle';

/** "2026-W41" → "tuần 41". */
const weekLabel = (week: string) => `tuần ${Number(week.split('-W')[1])}`;

function TwoLines({ main, sub }: { main: ReactNode; sub?: ReactNode }) {
  return (
    <div className="flex flex-col gap-[2px]">
      <span className="text-foreground da-body-strong">{main}</span>
      {sub && <span className="text-muted-foreground da-caption">{sub}</span>}
    </div>
  );
}

function costLines(provider: AiProvider): { main: string; sub: string } {
  const { month_cost_vnd: cost, month_budget_vnd: budget } = provider;
  if (budget === null || cost === null) return { main: 'Hạ tầng nội bộ', sub: 'tính theo giờ GPU' };
  const millions = (n: number) => formatNumber(n / 1e6, { maxFractionDigits: 1 });
  const month = Number(provider.budget_month.split('-')[1]);
  return {
    main: `${millions(cost)} / ${millions(budget)} triệu đ`,
    sub: cost === 0 ? 'chưa phát sinh' : `${pct(cost / budget, 0)} ngân sách tháng ${month}`,
  };
}

function ProviderCost({ provider }: { provider: AiProvider }) {
  const lines = costLines(provider);
  return <TwoLines main={lines.main} sub={lines.sub} />;
}

const kindLabel = (provider: AiProvider) => (provider.kind === 'local' ? 'Tại chỗ' : 'Frontier');
const retentionLabel = (provider: AiProvider) =>
  RETENTION[provider.zero_retention]?.label ?? 'Không áp dụng';

export function ProvidersTab({
  policy,
  routing,
}: {
  policy: V1State<AiPolicy>;
  routing: V1State<RoutingLog>;
}) {
  const toast = useToast();
  const providers = useV1(listProviders, []);
  const [testing, setTesting] = useState<string>();

  const callOnce = async (provider: AiProvider) => {
    setTesting(provider.id);
    try {
      const result = await testProvider(provider.id, provider.model_name);
      toast(
        result.ok
          ? {
              tone: 'success',
              message: `${provider.name} trả lời sau ${formatLatency(result.latency_ms)}.`,
            }
          : {
              tone: 'error',
              message: `${provider.name} không trả lời: ${result.detail ?? 'lỗi không rõ'}`,
            },
      );
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setTesting(undefined);
    }
  };

  return (
    <div className="flex flex-col gap-da-xl px-da-3xl pt-da-xl pb-da-3xl">
      <Card
        size="sm"
        title={providers.data ? `Provider · ${providers.data.items.length}` : 'Provider'}
      >
        <AsyncRegion
          state={providers}
          what="provider"
          skeletonRows={3}
          isEmpty={(p) => p.items.length === 0}
          empty={<p className="text-muted-foreground da-body-small">Chưa có provider nào.</p>}
        >
          {(p) => <ProviderTable items={p.items} testing={testing} onTest={callOnce} />}
        </AsyncRegion>
      </Card>

      <div className="flex items-start gap-da-xl max-[1279px]:flex-col">
        <Card
          size="sm"
          title="Luật định tuyến theo độ nhạy"
          className="w-[520px] shrink-0 max-[1279px]:w-full"
        >
          <AsyncRegion state={policy} what="luật định tuyến" skeletonRows={4}>
            {(data) => (
              <RoutingRules
                policy={data}
                providers={providers.data?.items}
                onSaved={policy.reload}
              />
            )}
          </AsyncRegion>
        </Card>

        <Card
          size="sm"
          title={
            routing.data
              ? `Nhật ký định tuyến · ${weekLabel(routing.data.week)}`
              : 'Nhật ký định tuyến'
          }
          className="min-w-0 flex-1 max-[1279px]:w-full"
        >
          <AsyncRegion state={routing} what="nhật ký định tuyến" skeletonRows={3}>
            {(log) => (
              <>
                <RoutingKpis log={log} />
                <RoutingTable entries={log.items} />
              </>
            )}
          </AsyncRegion>
        </Card>
      </div>
    </div>
  );
}

function RoutingRules({
  policy,
  providers,
  onSaved,
}: {
  policy: AiPolicy;
  providers: AiProvider[] | undefined;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [target, setTarget] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const approved = (providers ?? []).filter(
    (p) => p.kind === 'frontier' && p.ac06_status === 'approved',
  );
  // Turning frontier on needs an AC06-approved frontier provider; turning it off never waits.
  const locked = !policy.frontier_allowed && approved.length === 0;

  const save = async () => {
    if (target === null) return;
    setSaving(true);
    setError(undefined);
    try {
      await saveAiPolicy({ ...policy, frontier_allowed: target });
      toast({
        tone: 'success',
        message: target
          ? 'Đã cho phép frontier trong tổ chức.'
          : 'Đã tắt frontier; mọi lời gọi chạy tại chỗ.',
      });
      setTarget(null);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-[10px] rounded-md bg-subtle px-[10px] py-da-md">
        <Toggle
          checked={policy.frontier_allowed}
          disabled={locked}
          label="Cho phép frontier trong tổ chức"
          onChange={(next) => {
            setError(undefined);
            setTarget(next);
          }}
        />
        <span className="min-w-0 flex-1 text-foreground da-body-strong">
          Cho phép frontier trong tổ chức
        </span>
        <span className="text-muted-foreground da-caption">
          {policy.frontier_allowed ? 'Đang bật' : 'Đang tắt'}
        </span>
      </div>
      <ol className="flex flex-col gap-da-md">
        {policy.routing_rules.map((rule, i) => (
          <li key={rule} className="flex items-start gap-da-md">
            <span className="w-[14px] shrink-0 font-semibold text-muted-foreground da-caption">
              {i + 1}
            </span>
            <span className="min-w-0 flex-1 text-foreground da-body-small">{rule}</span>
          </li>
        ))}
      </ol>
      <p className="text-muted-foreground da-caption">
        Tắt công tắc thì mọi lời gọi chạy tại chỗ; provider frontier chỉ dùng được sau khi AC06
        duyệt.
      </p>

      <ConfirmDialog
        open={target !== null}
        // Either way the switch can be turned back, so neither is a danger action (ConfirmDialog spec).
        tone="primary"
        title={target ? 'Cho phép frontier trong tổ chức?' : 'Tắt frontier trong tổ chức?'}
        confirmLabel={target ? 'Bật frontier' : 'Tắt frontier'}
        loading={saving}
        error={error}
        onConfirm={save}
        onCancel={() => setTarget(null)}
      >
        {target
          ? `Câu hỏi có toàn bộ ngữ cảnh nhãn Công khai, hoặc ngoài phạm vi data product và không kèm dữ liệu nội bộ, sẽ được gửi tới ${approved
              .map((p) => p.name)
              .join(', ')}. Mỗi lời gọi ghi kiểm toán.`
          : 'Từ lời gọi tiếp theo, mọi câu hỏi chạy trên mô hình tại chỗ, kể cả câu hỏi ngoài phạm vi data product.'}
      </ConfirmDialog>
    </>
  );
}

function RoutingKpis({ log }: { log: RoutingLog }) {
  const kpis: { label: string; value: number; error?: boolean }[] = [
    { label: 'Lời gọi frontier', value: log.frontier_calls },
    { label: 'Bị chặn vì dữ liệu nội bộ', value: log.blocked_internal, error: true },
    { label: 'Chạy tại chỗ', value: log.local_calls },
  ];
  return (
    <dl className="flex gap-da-xl">
      {kpis.map((kpi) => (
        <div key={kpi.label} className="flex min-w-0 flex-1 flex-col gap-[2px]">
          <dt className="text-muted-foreground da-caption">{kpi.label}</dt>
          <dd className={`da-heading-1 ${kpi.error ? 'text-error' : 'text-foreground'}`}>
            {formatNumber(kpi.value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function ProviderTable({
  items,
  testing,
  onTest,
}: {
  items: AiProvider[];
  testing: string | undefined;
  onTest: (provider: AiProvider) => void;
}) {
  const view = useTableView(items, {
    search: (provider) => {
      const cost = costLines(provider);
      return `${provider.name} ${kindLabel(provider)} ${provider.model_name} ${provider.model_note ?? ''} ${provider.endpoint_label} ${provider.region_text} ${retentionLabel(provider)} ${AC06[provider.ac06_status].label} ${cost.main} ${cost.sub}`;
    },
    filters: [
      { id: 'ac06', label: 'Duyệt AC06', value: (provider) => AC06[provider.ac06_status].label },
    ],
    sorts: [
      { id: 'name', value: (provider) => provider.name },
      { id: 'model', value: (provider) => provider.model_name },
      { id: 'endpoint', value: (provider) => provider.endpoint_label },
      { id: 'retention', value: (provider) => retentionLabel(provider) },
      { id: 'ac06', value: (provider) => AC06[provider.ac06_status].label },
      { id: 'cost', value: (provider) => provider.month_cost_vnd },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm provider…" searchLabel="Tìm provider" />
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-subtle">
              <SortableTh view={view} column="name" className={TH}>
                Provider
              </SortableTh>
              <SortableTh view={view} column="model" className={TH}>
                Mô hình
              </SortableTh>
              <SortableTh view={view} column="endpoint" className={TH}>
                Endpoint · vùng dữ liệu
              </SortableTh>
              <SortableTh view={view} column="retention" className={TH}>
                Không lưu dữ liệu
              </SortableTh>
              <SortableTh view={view} column="ac06" className={TH}>
                Duyệt AC06
              </SortableTh>
              <SortableTh view={view} column="cost" className={TH}>
                Chi phí tháng / ngân sách
              </SortableTh>
              <SortableTh view={view} className={TH}>
                <span className="sr-only">Thao tác</span>
              </SortableTh>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((provider) => {
              const retention = RETENTION[provider.zero_retention];
              const ac06 = AC06[provider.ac06_status];
              return (
                <tr key={provider.id} className="border-b border-border last:border-b-0">
                  <td className={TD}>
                    <TwoLines
                      main={provider.name}
                      sub={provider.kind === 'local' ? 'Tại chỗ' : 'Frontier'}
                    />
                  </td>
                  <td className={TD}>
                    <TwoLines main={provider.model_name} sub={provider.model_note} />
                  </td>
                  <td className={TD}>
                    <TwoLines main={provider.endpoint_label} sub={provider.region_text} />
                  </td>
                  <td className={`${TD} text-foreground da-body-small`}>
                    {retention ? (
                      <StatusDot tone={retention.tone}>{retention.label}</StatusDot>
                    ) : (
                      'Không áp dụng'
                    )}
                  </td>
                  <td className={TD}>
                    <StatusDot tone={ac06.tone}>{ac06.label}</StatusDot>
                  </td>
                  <td className={TD}>
                    <ProviderCost provider={provider} />
                  </td>
                  <td className={`${TD} text-right`}>
                    <Button
                      onClick={() => onTest(provider)}
                      disabled={testing !== undefined}
                      aria-label={`Gọi thử ${provider.name}`}
                    >
                      Gọi thử
                    </Button>
                  </td>
                </tr>
              );
            })}
            {view.total === 0 && tableEmptyRow(7)}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}

/** The card shows the time only; the full log spans the week, so it adds the day and asker. */
function RoutingTable({ entries, full = false }: { entries: RoutingLogEntry[]; full?: boolean }) {
  const view = useTableView(entries, {
    search: (entry) =>
      [
        full ? formatDate(entry.at).slice(0, 5) : '',
        formatHour(entry.at),
        entry.question,
        DECISION[entry.decision].label,
        entry.reason,
        entry.provider_name,
        full ? entry.asked_by_name : '',
      ].join(' '),
    filters: [
      { id: 'decision', label: 'Quyết định', value: (entry) => DECISION[entry.decision].label },
    ],
    sorts: [
      { id: 'at', value: (entry) => entry.at },
      { id: 'question', value: (entry) => entry.question },
      { id: 'decision', value: (entry) => DECISION[entry.decision].label },
      { id: 'reason', value: (entry) => entry.reason },
      { id: 'provider', value: (entry) => entry.provider_name },
      ...(full ? [{ id: 'asker', value: (entry: RoutingLogEntry) => entry.asked_by_name }] : []),
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm câu hỏi…" searchLabel="Tìm câu hỏi" />
      <div className="overflow-x-auto rounded-md border border-border">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-border bg-subtle">
              <SortableTh view={view} column="at" className={TH}>
                Lúc
              </SortableTh>
              <SortableTh view={view} column="question" className={TH}>
                Câu hỏi
              </SortableTh>
              <SortableTh view={view} column="decision" className={TH}>
                Quyết định
              </SortableTh>
              <SortableTh view={view} column="reason" className={TH}>
                Lý do
              </SortableTh>
              <SortableTh view={view} column="provider" className={TH}>
                Provider
              </SortableTh>
              {full && (
                <SortableTh view={view} column="asker" className={TH}>
                  Người hỏi
                </SortableTh>
              )}
            </tr>
          </thead>
          <tbody>
            {view.rows.map((entry) => {
              const decision = DECISION[entry.decision];
              return (
                <tr
                  key={entry.id}
                  className="border-b border-border text-foreground da-body-small last:border-b-0"
                >
                  <td className={`${TD} whitespace-nowrap da-mono`}>
                    {full && `${formatDate(entry.at).slice(0, 5)} `}
                    {formatHour(entry.at)}
                  </td>
                  <td className={TD}>{entry.question}</td>
                  <td className={TD}>
                    <StatusDot tone={decision.tone}>{decision.label}</StatusDot>
                  </td>
                  <td className={TD}>{entry.reason}</td>
                  <td className={TD}>{entry.provider_name}</td>
                  {full && <td className={TD}>{entry.asked_by_name}</td>}
                </tr>
              );
            })}
            {view.total === 0 && tableEmptyRow(full ? 6 : 5)}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}

const LOG_PAGE = 50;

export function RoutingLogTab() {
  const first = useV1(() => getRoutingLog({ page_size: LOG_PAGE }), []);
  const [more, setMore] = useState<RoutingLogEntry[]>([]);
  const [token, setToken] = useState<string>();
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string>();

  const next = token ?? first.data?.next_page_token;

  const loadMore = async () => {
    if (!first.data || !next) return;
    setLoadingMore(true);
    setError(undefined);
    try {
      const page = await getRoutingLog({
        week: first.data.week,
        page_size: LOG_PAGE,
        page_token: next,
      });
      setMore((rows) => [...rows, ...page.items]);
      setToken(page.next_page_token);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoadingMore(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-xl px-da-3xl pt-da-xl pb-da-3xl">
      <AsyncRegion
        state={first}
        what="nhật ký định tuyến"
        skeletonRows={6}
        isEmpty={(log) => log.items.length === 0}
        empty={<p className="text-muted-foreground da-body-small">Tuần này chưa có lời gọi nào.</p>}
      >
        {(log) => (
          <>
            <h2 className="text-foreground da-heading-3">
              Nhật ký định tuyến · {weekLabel(log.week)}
            </h2>
            <RoutingKpis log={log} />
            <RoutingTable entries={[...log.items, ...more]} full />
            {error && <p className="text-error da-caption">{error}</p>}
            {next && (
              <div>
                <Button onClick={loadMore} disabled={loadingMore}>
                  Tải thêm
                </Button>
              </div>
            )}
          </>
        )}
      </AsyncRegion>
    </div>
  );
}

const KINDS = [
  { value: 'frontier', label: 'Frontier (API bên ngoài)' },
  { value: 'local', label: 'Tại chỗ' },
] as const;

export function AddProviderDialog({
  onClose,
  onAdded,
}: {
  onClose: () => void;
  onAdded: (provider: AiProvider) => void;
}) {
  const [name, setName] = useState('');
  const [kind, setKind] = useState<AiProvider['kind']>('frontier');
  const [model, setModel] = useState('');
  const [endpoint, setEndpoint] = useState('');
  const [region, setRegion] = useState('');
  const [budget, setBudget] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const budgetMillions = Number(budget.replace(',', '.'));
  const budgetOk = kind === 'local' || (budget.trim() !== '' && budgetMillions > 0);

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const provider = await addProvider({
        name: name.trim(),
        kind,
        model_name: model.trim(),
        endpoint: endpoint.trim(),
        region: region.trim(),
        month_budget_vnd: kind === 'local' ? null : Math.round(budgetMillions * 1e6),
      });
      onAdded(provider);
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      size="sm"
      open
      title="Thêm provider"
      description="Provider frontier chỉ được định tuyến sau khi AC06 duyệt và chỉ nhận ngữ cảnh nhãn Công khai."
      submitLabel="Thêm provider"
      submitting={saving}
      canSubmit={Boolean(name.trim() && model.trim() && endpoint.trim() && budgetOk)}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Tên">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Nhà cung cấp C (API)"
        />
      </Field>
      <Field label="Loại">
        <Select
          label="Loại"
          options={[...KINDS]}
          value={kind}
          onChange={(v) => setKind(v as AiProvider['kind'])}
        />
      </Field>
      <Field label="Mô hình">
        <Input
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="Mô hình frontier C"
        />
      </Field>
      <Field label="Địa chỉ">
        <Input
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          placeholder="https://api.nhacungcap.example/v1"
        />
      </Field>
      <Field label="Vùng dữ liệu">
        <Input value={region} onChange={(e) => setRegion(e.target.value)} placeholder="Singapore" />
      </Field>
      {kind === 'frontier' && (
        <Field label="Ngân sách tháng (triệu đ)">
          <Input
            value={budget}
            inputMode="decimal"
            onChange={(e) => setBudget(e.target.value)}
            placeholder="10"
          />
        </Field>
      )}
      <p className="flex items-start gap-da-md rounded-md bg-info-subtle px-da-md py-da-sm text-foreground da-caption">
        <Icon name="agent" className="shrink-0 text-info" />
        Sau khi thêm, hệ thống xác minh cam kết không lưu dữ liệu và gửi hồ sơ cho AC06 duyệt.
      </p>
    </Dialog>
  );
}
