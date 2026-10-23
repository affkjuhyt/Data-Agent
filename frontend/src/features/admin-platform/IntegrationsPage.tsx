import { useState, type ReactNode } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
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
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listAdminUsers } from '../../lib/v1/adminAccess';
import {
  addClient,
  addWebhook,
  getIntegrationUsage,
  listIntegrations,
  MCP_TOOLS,
  revokeClient,
  rotateClientSecret,
  type Integration,
  type IntegrationKind,
  type IntegrationUsage,
} from '../../lib/v1/adminPlatform';
import { listDataSpaces } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';
import { formatExpiry, pct } from './format';

/*
 * "P12 · Tích hợp · Client & sử dụng" (2306:20082, UC-12-01, UC-12-03, UC-12-04): the REST and
 * MCP clients that call Data Agent with their tools, 7-day calls, errors, expiry and status; on
 * the right the usage of the selected client, with rotating its secret and revoking it. A
 * client has no rights of its own: it acts as its owner, who confirms one granted in their name
 * (Hồ sơ · Client tôi phụ trách). UC-12-02 (calling over MCP) has no screen. "Đăng ký client"
 * and "Webhook" open "Hộp thoại · addIntegration" (2095:8701); a secret or signing key is shown
 * once, in the answer that issues it.
 */
type DialogKind = IntegrationKind | 'webhook';

const KIND_LABEL: Record<IntegrationKind, string> = { mcp: 'MCP', api: 'API' };

const STATUS: Record<
  Integration['status'],
  { label: string; tone: 'success' | 'warning' | 'idle' }
> = {
  pending_owner: { label: 'Chờ người phụ trách', tone: 'warning' },
  active: { label: 'Hoạt động', tone: 'success' },
  expiring: { label: 'Sắp hết hạn', tone: 'warning' },
  paused: { label: 'Tạm dừng', tone: 'idle' },
  expired: { label: 'Hết hạn', tone: 'idle' },
  revoked: { label: 'Đã thu hồi', tone: 'idle' },
};

const DIALOG_KINDS: { value: DialogKind; label: string }[] = [
  { value: 'mcp', label: 'Client MCP' },
  { value: 'api', label: 'Client API (REST)' },
  { value: 'webhook', label: 'Webhook' },
];

interface Issued {
  title: string;
  label: string;
  value: string;
  note: string;
}

export default function IntegrationsPage() {
  const toast = useToast();
  const clients = useV1(['listIntegrations'], listIntegrations);
  const [selected, setSelected] = useState<string>();
  const [dialog, setDialog] = useState<DialogKind | null>(null);
  const [issued, setIssued] = useState<Issued | null>(null);

  const items = clients.data?.items ?? [];
  const current = items.find((c) => c.id === selected) ?? items[0];
  const view = useTableView(items, {
    search: (client) =>
      `${client.name} ${KIND_LABEL[client.kind]} ${client.owner_name} ${client.scopes.join(' ')} ${client.calls_7d === null ? '' : formatNumber(client.calls_7d)} ${pct(client.error_rate_7d)} ${formatExpiry(client.expires_at)} ${STATUS[client.status].label}`,
    filters: [
      { id: 'status', label: 'Trạng thái', value: (client) => STATUS[client.status].label },
    ],
    sorts: [
      { id: 'name', value: (client) => client.name },
      { id: 'tools', value: (client) => client.scopes.join(', ') },
      { id: 'calls', value: (client) => client.calls_7d },
      { id: 'errors', value: (client) => client.error_rate_7d },
      { id: 'expiry', value: (client) => client.expires_at },
      { id: 'status', value: (client) => STATUS[client.status].label },
    ],
    pageSize: 50,
  });
  const active = items.filter((c) => c.status === 'active' || c.status === 'expiring').length;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Tích hợp']}
        title="Client API và MCP"
        description={`${clients.data ? `${active} client đang hoạt động · ` : ''}công cụ khác (Copilot, Excel, hệ thống nội bộ) gọi Data Agent theo đúng quyền của người dùng`}
        actions={
          <>
            <Button onClick={() => setDialog('webhook')}>Webhook</Button>
            <Button kind="primary" onClick={() => setDialog('mcp')}>
              Đăng ký client
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <div className="min-w-0 flex-1 pl-da-3xl pr-da-2xl pt-da-xl pb-da-3xl">
          <AsyncRegion
            state={clients}
            what="client"
            skeletonRows={5}
            isEmpty={(p) => p.items.length === 0}
            empty={
              <p className="text-muted-foreground da-body-small">
                Chưa có client nào. Đăng ký client để công cụ khác gọi Data Agent.
              </p>
            }
          >
            {() => (
              <div className="flex flex-col gap-da-lg">
                <TableToolbar view={view} placeholder="Tìm client…" searchLabel="Tìm client" />
                <table className="w-full table-fixed border-collapse">
                  <thead>
                    <tr className="border-b border-border text-left text-muted-foreground da-caption">
                      <SortableTh
                        view={view}
                        column="name"
                        className="px-da-lg py-da-md font-normal"
                      >
                        Client
                      </SortableTh>
                      <SortableTh
                        view={view}
                        column="tools"
                        className="w-[232px] py-da-md font-normal"
                      >
                        Công cụ
                      </SortableTh>
                      <SortableTh
                        view={view}
                        column="calls"
                        className="w-[132px] py-da-md font-normal"
                      >
                        Lượt gọi 7 ngày
                      </SortableTh>
                      <SortableTh
                        view={view}
                        column="errors"
                        className="w-[82px] py-da-md font-normal"
                      >
                        Lỗi
                      </SortableTh>
                      <SortableTh
                        view={view}
                        column="expiry"
                        className="w-[112px] py-da-md font-normal"
                      >
                        Hạn
                      </SortableTh>
                      <SortableTh
                        view={view}
                        column="status"
                        className="w-[162px] py-da-md pr-da-lg font-normal"
                      >
                        Trạng thái
                      </SortableTh>
                    </tr>
                  </thead>
                  <tbody>
                    {view.rows.map((client) => {
                      const status = STATUS[client.status];
                      const isCurrent = client.id === current?.id;
                      return (
                        <tr
                          key={client.id}
                          aria-selected={isCurrent}
                          className={cn('border-b border-border', isCurrent && 'bg-subtle')}
                        >
                          <td className="px-da-lg py-da-md">
                            <button
                              type="button"
                              onClick={() => setSelected(client.id)}
                              className="flex w-full min-w-0 flex-col gap-[2px] text-left"
                            >
                              <span className="truncate text-foreground da-body-strong">
                                {client.name}
                              </span>
                              <span className="truncate text-muted-foreground da-caption">
                                {[KIND_LABEL[client.kind], client.owner_name]
                                  .filter(Boolean)
                                  .join(' · ')}
                              </span>
                            </button>
                          </td>
                          <td className="py-da-md text-foreground da-mono">
                            {client.scopes.join(', ') || '—'}
                          </td>
                          <td className="py-da-md text-foreground da-mono">
                            {client.calls_7d === null ? '—' : formatNumber(client.calls_7d)}
                          </td>
                          <td
                            className={cn(
                              'py-da-md da-mono',
                              (client.error_rate_7d ?? 0) >= 0.02
                                ? 'text-warning'
                                : 'text-foreground',
                            )}
                          >
                            {pct(client.error_rate_7d)}
                          </td>
                          <td className="py-da-md text-muted-foreground da-mono">
                            {formatExpiry(client.expires_at)}
                          </td>
                          <td className="py-da-md pr-da-lg">
                            <StatusDot tone={status.tone}>{status.label}</StatusDot>
                          </td>
                        </tr>
                      );
                    })}
                    {view.total === 0 && tableEmptyRow(6)}
                  </tbody>
                </table>
                <TablePager view={view} />
              </div>
            )}
          </AsyncRegion>
        </div>

        <aside
          aria-label="Sử dụng của client"
          className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] pb-da-xl max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t"
        >
          {current && (
            <UsagePanel
              key={current.id}
              client={current}
              onRotated={(secret) =>
                setIssued({
                  title: `Bí mật mới của ${current.name}`,
                  label: 'Client secret',
                  value: secret,
                  note: 'Bí mật cũ hết hiệu lực. Chỉ hiện một lần: chép vào công cụ gọi Data Agent ngay.',
                })
              }
              onRevoked={() => {
                toast({ tone: 'success', message: `Đã thu hồi ${current.name}.` });
                clients.reload();
              }}
            />
          )}
          <p className="text-muted-foreground da-caption">
            Client không có quyền riêng; mọi lời gọi mang danh tính người dùng thật.
          </p>
        </aside>
      </div>

      {dialog && (
        <AddDialog
          initialKind={dialog}
          clients={items.filter((c) => c.status === 'active' || c.status === 'expiring')}
          onClose={() => setDialog(null)}
          onIssued={(result, clientId) => {
            setDialog(null);
            if (clientId) setSelected(clientId);
            setIssued(result);
            clients.reload();
          }}
        />
      )}
      {issued && <IssuedDialog issued={issued} onClose={() => setIssued(null)} />}
    </div>
  );
}

function UsagePanel({
  client,
  onRotated,
  onRevoked,
}: {
  client: Integration;
  onRotated: (secret: string) => void;
  onRevoked: () => void;
}) {
  const toast = useToast();
  const usage = useV1(['getIntegrationUsage', client, client.id], () =>
    getIntegrationUsage(client),
  );
  const [busy, setBusy] = useState(false);
  const [revoking, setRevoking] = useState(false);
  const live = client.status !== 'revoked' && client.status !== 'expired';

  const rotate = async () => {
    setBusy(true);
    try {
      onRotated(await rotateClientSecret(client.id));
    } catch (e) {
      toast({ tone: 'error', message: `Không xoay được bí mật: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h2 className="text-foreground da-heading-3">{client.name} · 7 ngày</h2>
      <AsyncRegion state={usage} what="số lượt gọi" skeletonRows={3}>
        {(data) => <UsageBody data={data} />}
      </AsyncRegion>
      {live && (
        <div className="flex flex-wrap gap-da-sm">
          <Button size="sm" loading={busy} onClick={rotate}>
            Xoay bí mật
          </Button>
          <Button size="sm" kind="danger" onClick={() => setRevoking(true)}>
            Thu hồi
          </Button>
        </div>
      )}
      {revoking && (
        <RevokeDialog
          client={client}
          onClose={() => setRevoking(false)}
          onRevoked={() => {
            setRevoking(false);
            onRevoked();
          }}
        />
      )}
    </>
  );
}

function UsageBody({ data }: { data: IntegrationUsage }) {
  const max = Math.max(1, ...data.daily_calls);
  return (
    <>
      <div
        role="img"
        aria-label={`Lượt gọi theo ngày: ${data.daily_calls.map((n) => formatNumber(n)).join(', ')}`}
        className="flex h-20 items-end gap-[10px]"
      >
        {data.daily_calls.map((n, i) => (
          <span
            key={i}
            className="w-[30px] rounded-[2px] bg-chart-6"
            style={{ height: `${Math.max(n > 0 ? 4 : 1, Math.round((n / max) * 67))}px` }}
          />
        ))}
      </div>
      <UsageLine label="Công cụ dùng nhiều nhất">
        {data.top_tools.length === 0
          ? 'Chưa có lượt gọi'
          : data.top_tools.map((t) => `${t.name} ${pct(t.share, 0)}`).join(' · ')}
      </UsageLine>
      <UsageLine label="Hạn mức">
        {formatNumber(data.quota_per_day)} lượt/ngày · hôm nay đã dùng{' '}
        {pct(data.quota_used_ratio, 0)}
      </UsageLine>
      <UsageLine label="Bị chặn do quyền">
        {formatNumber(data.blocked_by_permission)} lượt · người dùng không có quyền dữ liệu
      </UsageLine>
    </>
  );
}

function UsageLine({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-[2px] pb-da-sm">
      <span className="text-muted-foreground da-caption">{label}</span>
      <span className="text-foreground da-body-small">{children}</span>
    </div>
  );
}

function RevokeDialog({
  client,
  onClose,
  onRevoked,
}: {
  client: Integration;
  onClose: () => void;
  onRevoked: () => void;
}) {
  const [reason, setReason] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      await revokeClient(client.id, reason.trim());
      onRevoked();
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };
  return (
    <Dialog
      size="sm"
      open
      title={`Thu hồi ${client.name}?`}
      description="Client ngừng gọi được ngay, webhook của nó tạm dừng. Không hoàn tác được."
      submitLabel="Thu hồi"
      submitting={saving}
      canSubmit={Boolean(reason.trim())}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Lý do">
        <Textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} />
      </Field>
    </Dialog>
  );
}

/** YYYY-MM-DD in local time. */
const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;

/** Today plus `months`, as YYYY-MM-DD. */
const monthsAhead = (months: number) => {
  const d = new Date();
  d.setMonth(d.getMonth() + months);
  return isoDay(d);
};

function AddDialog({
  initialKind,
  clients,
  onClose,
  onIssued,
}: {
  initialKind: DialogKind;
  clients: Integration[];
  onClose: () => void;
  onIssued: (issued: Issued, clientId?: string) => void;
}) {
  const toast = useToast();
  const spaces = useV1(['listDataSpaces'], listDataSpaces);
  const users = useV1(['listAdminUsers'], listAdminUsers);
  const [kind, setKind] = useState<DialogKind>(initialKind);
  const [name, setName] = useState('');
  const [owner, setOwner] = useState<string | null>(null);
  const [space, setSpace] = useState<string | null>(null);
  const [tools, setTools] = useState<string[]>([]);
  const [expiresOn, setExpiresOn] = useState(monthsAhead(6));
  const [clientId, setClientId] = useState<string | null>(clients[0]?.id ?? null);
  const [target, setTarget] = useState('');
  const [events, setEvents] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const people = (users.data?.items ?? []).filter((u) => u.kind === 'person');
  const eventTypes = events
    .split(',')
    .map((t) => t.trim())
    .filter(Boolean);
  const canSubmit =
    kind === 'webhook'
      ? Boolean(clientId && target.trim() && eventTypes.length)
      : Boolean(name.trim() && owner && space && tools.length && expiresOn);

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      if (kind === 'webhook') {
        const hook = await addWebhook({
          api_client_id: clientId!,
          target_url: target.trim(),
          event_types: eventTypes,
        });
        onIssued({
          title: 'Đã đăng ký webhook',
          label: 'Khoá ký',
          value: hook.signing_key,
          note: 'Chỉ hiện một lần: dùng khoá này để kiểm chữ ký của mỗi lần gửi.',
        });
        return;
      }
      const { client, secret } = await addClient({
        name: name.trim(),
        kind,
        owner_user_id: owner!,
        tools,
        data_space_ids: [space!],
        expires_on: expiresOn,
      });
      if (client.status === 'pending_owner')
        toast({
          tone: 'info',
          message: `${client.name} chờ ${client.owner_name || 'người phụ trách'} xác nhận trước khi gọi được.`,
        });
      onIssued(
        {
          title: `Đã cấp ${client.name}`,
          label: 'Client secret',
          value: secret,
          note: 'Chỉ hiện một lần: chép vào công cụ gọi Data Agent ngay.',
        },
        client.id,
      );
    } catch (e) {
      setError((e as Error).message);
      setSaving(false);
    }
  };

  return (
    <Dialog
      size="sm"
      open
      title="Thêm tích hợp"
      description="Client gọi Data Agent bằng quyền của người phụ trách; webhook nhận sự kiện."
      submitLabel={kind === 'webhook' ? 'Đăng ký webhook' : 'Cấp client'}
      submitting={saving}
      canSubmit={canSubmit}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Loại">
        <Select<DialogKind> label="Loại" options={DIALOG_KINDS} value={kind} onChange={setKind} />
      </Field>
      {kind === 'webhook' ? (
        <>
          <Field label="Client">
            <Select
              label="Client"
              options={clients.map((c) => ({ value: c.id, label: c.name }))}
              value={clientId}
              onChange={setClientId}
            />
          </Field>
          <Field label="Địa chỉ nhận">
            <Input
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              placeholder="https://bot.noibo/hooks/data-agent"
            />
          </Field>
          <Field label="Sự kiện">
            <Input
              value={events}
              onChange={(e) => setEvents(e.target.value)}
              placeholder="alert.fired, report.published"
            />
          </Field>
        </>
      ) : (
        <>
          <Field label="Tên">
            <Input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Copilot nội bộ"
            />
          </Field>
          <Field label="Người phụ trách">
            <Select
              label="Người phụ trách"
              options={people.map((u) => ({ value: u.id, label: u.display_name }))}
              value={owner}
              onChange={setOwner}
            />
          </Field>
          <Field label="Data Space">
            <Select
              label="Data Space"
              options={(spaces.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }))}
              value={space}
              onChange={setSpace}
            />
          </Field>
          <fieldset className="flex flex-col gap-da-xs">
            <legend className="pb-da-xs text-foreground da-label">Công cụ</legend>
            {MCP_TOOLS.map((tool) => (
              <Checkbox
                key={tool}
                checked={tools.includes(tool)}
                onChange={(on) =>
                  setTools((all) => (on ? [...all, tool] : all.filter((t) => t !== tool)))
                }
              >
                {tool}
              </Checkbox>
            ))}
          </fieldset>
          <Field label="Hạn dùng" hint="Tối đa 12 tháng (BR-144).">
            <Input
              type="date"
              value={expiresOn}
              min={isoDay(new Date())}
              max={monthsAhead(12)}
              onChange={(e) => setExpiresOn(e.target.value)}
            />
          </Field>
        </>
      )}
    </Dialog>
  );
}

function IssuedDialog({ issued, onClose }: { issued: Issued; onClose: () => void }) {
  return (
    <Dialog
      size="sm"
      open
      title={issued.title}
      description={issued.note}
      onClose={onClose}
      footer={
        <Button kind="primary" onClick={onClose}>
          Đã chép, đóng
        </Button>
      }
    >
      <Field label={issued.label}>
        <Input readOnly value={issued.value} className="da-mono" />
      </Field>
    </Dialog>
  );
}
