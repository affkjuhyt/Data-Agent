import { useState, type ReactNode } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  addIntegration,
  getIntegrationUsage,
  listIntegrations,
  type Integration,
  type IntegrationKind,
  type IntegrationUsage,
  type NewIntegration,
} from '../../lib/v1/adminPlatform';
import { listDataSpaces } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';
import { formatExpiry, pct } from './format';
import { Dialog } from '../../design-system/Dialog';

/*
 * "P12 · Tích hợp · Client & sử dụng" (2306:20082, UC-12-01, UC-12-03, UC-12-04): the clients
 * that call Data Agent (MCP, API, webhook) with their scopes, 7-day calls, errors, expiry and
 * status; on the right the usage of the selected client. A client has no rights of its own:
 * every call carries the identity of the real user behind it. UC-12-02 (calling over MCP) has
 * no screen. "Đăng ký client" and "Webhook" open "Hộp thoại · addIntegration" (2095:8701).
 */
const KIND_LABEL: Record<IntegrationKind, string> = { mcp: 'MCP', api: 'API', webhook: 'Webhook' };

const STATUS: Record<
  Integration['status'],
  { label: string; tone: 'success' | 'warning' | 'idle' }
> = {
  active: { label: 'Hoạt động', tone: 'success' },
  expiring: { label: 'Sắp hết hạn', tone: 'warning' },
  expired: { label: 'Hết hạn', tone: 'idle' },
  revoked: { label: 'Đã thu hồi', tone: 'idle' },
};

const DIALOG_KINDS: { value: IntegrationKind; label: string }[] = [
  { value: 'mcp', label: 'Máy chủ MCP' },
  { value: 'api', label: 'Client API' },
  { value: 'webhook', label: 'Webhook' },
];

const AUTH: { value: NewIntegration['auth']; label: string }[] = [
  { value: 'oauth2_client_credentials', label: 'OAuth2 · client credentials' },
  { value: 'api_key', label: 'API key · lưu trong kho bí mật' },
  { value: 'mtls', label: 'mTLS' },
];

export default function IntegrationsPage() {
  const toast = useToast();
  const clients = useV1(listIntegrations, []);
  const [selected, setSelected] = useState<string>();
  const [dialog, setDialog] = useState<IntegrationKind | null>(null);

  const items = clients.data?.items ?? [];
  const current = items.find((c) => c.id === selected) ?? items[0];
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
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="px-da-lg py-da-md font-normal">Client</th>
                    <th className="w-[232px] py-da-md font-normal">Phạm vi</th>
                    <th className="w-[132px] py-da-md font-normal">Lượt gọi 7 ngày</th>
                    <th className="w-[82px] py-da-md font-normal">Lỗi</th>
                    <th className="w-[112px] py-da-md font-normal">Hạn</th>
                    <th className="w-[142px] py-da-md pr-da-lg font-normal">Trạng thái</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((client) => {
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
                              {KIND_LABEL[client.kind]} · {client.owner_name}
                            </span>
                          </button>
                        </td>
                        <td className="py-da-md text-foreground da-mono">
                          {client.scopes.join(', ')}
                        </td>
                        <td className="py-da-md text-foreground da-mono">
                          {formatNumber(client.calls_7d)}
                        </td>
                        <td
                          className={cn(
                            'py-da-md da-mono',
                            (client.error_rate_7d ?? 0) >= 0.02
                              ? 'text-warning'
                              : 'text-foreground',
                          )}
                        >
                          {client.error_rate_7d === null ? '—' : pct(client.error_rate_7d)}
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
                </tbody>
              </table>
            )}
          </AsyncRegion>
        </div>

        <aside
          aria-label="Sử dụng của client"
          className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] pb-da-xl max-[1279px]:w-full max-[1279px]:border-l-0 max-[1279px]:border-t"
        >
          {current && <UsagePanel client={current} />}
          <p className="text-muted-foreground da-caption">
            Client không có quyền riêng; mọi lời gọi mang danh tính người dùng thật.
          </p>
        </aside>
      </div>

      {dialog && (
        <AddIntegrationDialog
          initialKind={dialog}
          onClose={() => setDialog(null)}
          onAdded={(client) => {
            setDialog(null);
            setSelected(client.id);
            toast({ tone: 'success', message: `Đã kết nối ${client.name}.` });
            clients.reload();
          }}
        />
      )}
    </div>
  );
}

function UsagePanel({ client }: { client: Integration }) {
  const usage = useV1(() => getIntegrationUsage(client.id), [client.id]);
  return (
    <>
      <h2 className="text-foreground da-heading-3">{client.name} · 7 ngày</h2>
      <AsyncRegion state={usage} what="số lượt gọi" skeletonRows={3}>
        {(data) => <UsageBody data={data} />}
      </AsyncRegion>
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
        {formatNumber(data.quota_per_day)} lượt/ngày · đã dùng {pct(data.quota_used_ratio, 0)}
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

function AddIntegrationDialog({
  initialKind,
  onClose,
  onAdded,
}: {
  initialKind: IntegrationKind;
  onClose: () => void;
  onAdded: (client: Integration) => void;
}) {
  const spaces = useV1(listDataSpaces, []);
  const [kind, setKind] = useState<IntegrationKind>(initialKind);
  const [endpoint, setEndpoint] = useState('');
  const [auth, setAuth] = useState<NewIntegration['auth']>('oauth2_client_credentials');
  const [tools, setTools] = useState('');
  const [audience, setAudience] = useState<string>('org');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const audiences = [
    { value: 'org', label: 'Mọi agent của tổ chức' },
    ...(spaces.data?.items ?? []).map((s) => ({
      value: s.id,
      label: `Agent trong Data Space ${s.name}`,
    })),
  ];

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const client = await addIntegration({
        kind,
        endpoint: endpoint.trim(),
        auth,
        tools: tools
          .split(',')
          .map((t) => t.trim())
          .filter(Boolean),
        data_space_id: audience === 'org' ? null : audience,
      });
      onAdded(client);
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
      description="Nơi agent nhận việc hoặc gửi kết quả."
      submitLabel="Kết nối & kiểm tra"
      submitting={saving}
      canSubmit={Boolean(endpoint.trim())}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <Field label="Loại">
        <Select<IntegrationKind>
          label="Loại"
          options={DIALOG_KINDS}
          value={kind}
          onChange={setKind}
        />
      </Field>
      <Field label="Địa chỉ">
        <Input
          value={endpoint}
          onChange={(e) => setEndpoint(e.target.value)}
          placeholder="https://mcp-ticket.noibo/v1"
        />
      </Field>
      <Field label="Xác thực">
        <Select<NewIntegration['auth']>
          label="Xác thực"
          options={AUTH}
          value={auth}
          onChange={setAuth}
        />
      </Field>
      <Field label="Công cụ cung cấp">
        <Input
          value={tools}
          onChange={(e) => setTools(e.target.value)}
          placeholder="ticket.create, ticket.search"
        />
      </Field>
      <Field label="Ai dùng được">
        <Select label="Ai dùng được" options={audiences} value={audience} onChange={setAudience} />
      </Field>
    </Dialog>
  );
}
