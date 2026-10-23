import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { confirmMyClient, listMyClients, type MyClient } from '../../lib/v1/adminPlatform';
import { V1Error } from '../../lib/v1/client';
import { useV1 } from '../../lib/v1/useV1';
import { formatExpiry } from './format';
import { SectionTitle } from './parts';

/*
 * "Client tôi phụ trách" on Hồ sơ (UC-12-01, BR-48): a client acts with its owner's rights, so
 * one an administrator granted in my name waits in pending_owner until I agree here. The
 * agreement is to the client as listed (If-Match: its updated_at); if an administrator changed
 * it since, the api answers 412 and the list is read again for me to look before agreeing.
 */
const STATUS: Record<string, { label: string; tone: 'success' | 'warning' | 'idle' }> = {
  pending_owner: { label: 'Chờ bạn đồng ý', tone: 'warning' },
  active: { label: 'Hoạt động', tone: 'success' },
  paused: { label: 'Tạm dừng', tone: 'idle' },
  expired: { label: 'Hết hạn', tone: 'idle' },
  revoked: { label: 'Đã thu hồi', tone: 'idle' },
};

export function MyClients() {
  const toast = useToast();
  const clients = useV1(['listMyClients'], listMyClients);
  const [busy, setBusy] = useState<string | null>(null);

  const confirm = async (client: MyClient) => {
    setBusy(client.id);
    try {
      await confirmMyClient(client);
      toast({ tone: 'success', message: `${client.name} gọi được Data Agent bằng quyền của bạn.` });
    } catch (e) {
      toast({
        tone: 'error',
        message:
          e instanceof V1Error && e.status === 412
            ? `${client.name} vừa được quản trị viên sửa. Xem lại rồi đồng ý.`
            : `Không xác nhận được: ${(e as Error).message}`,
      });
    } finally {
      setBusy(null);
      clients.reload();
    }
  };

  return (
    <section aria-label="Client tôi phụ trách" className="flex flex-col gap-da-md px-da-3xl pt-da-2xl pb-da-xl">
      <SectionTitle>Client tôi phụ trách</SectionTitle>
      <p className="text-muted-foreground da-caption">
        Client gọi Data Agent bằng quyền của bạn; client quản trị viên cấp cho bạn chỉ chạy khi bạn
        đồng ý.
      </p>
      <AsyncRegion
        state={clients}
        what="client"
        skeletonRows={2}
        isEmpty={(p) => p.items.length === 0}
        empty={<p className="text-muted-foreground da-body-small">Bạn chưa phụ trách client nào.</p>}
      >
        {(p) => (
          <ul className="flex flex-col">
            {p.items.map((client) => {
              const status = STATUS[client.status] ?? { label: client.status, tone: 'idle' };
              const tools = Array.isArray(client.tools) ? client.tools : [];
              const spaces = Array.isArray(client.data_space_ids) ? client.data_space_ids.length : 0;
              return (
                <li
                  key={client.id}
                  className="flex items-center gap-da-lg border-b border-border py-[10px]"
                >
                  <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
                    <span className="truncate text-foreground da-body-strong">{client.name}</span>
                    <span className="truncate text-muted-foreground da-caption">
                      {client.kind === 'mcp' ? 'MCP' : 'API'} · {tools.join(', ') || 'chưa có công cụ'}{' '}
                      · {spaces} Data Space · hạn {formatExpiry(client.expires_at)}
                    </span>
                  </div>
                  <StatusDot tone={status.tone}>{status.label}</StatusDot>
                  {client.status === 'pending_owner' && (
                    <Button
                      size="sm"
                      kind="primary"
                      loading={busy === client.id}
                      onClick={() => confirm(client)}
                    >
                      Đồng ý
                    </Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </AsyncRegion>
    </section>
  );
}
