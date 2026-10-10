import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { EmptyState } from '../../design-system/EmptyState';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { requestAccess } from '../../lib/v1/adminAccess';
import {
  getMyDataSpaces,
  setOpenLastUsed,
  type DataSpaceChoice,
  type MyDataSpaces,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { BrandBar } from './BrandBar';

/*
 * "v3.1 · Chọn Data Space [UC-11-08]" (2230:14988): a full page after sign-in, before the app
 * shell — not the top-bar switcher (AppTopBar's WorkspaceSwitcher stays for changing space
 * later). One card per space: name, the viewer's role (blue = may ask and run, grey = viewer,
 * brown = no access), its size and last use, and "Mở"; the most recently used space has the
 * primary button. A space without access names its owner and offers "Yêu cầu quyền".
 * "Lần sau mở thẳng Data Space dùng gần nhất" is a server-side preference.
 */
const TONE = { analyst: 'info', viewer: 'idle' } as const;

/** "Đỗ Hải Yến" → "Hải Yến": Vietnamese greetings use the given names. */
const givenName = (full: string) => full.trim().split(/\s+/).slice(-2).join(' ');

export function DataSpacePicker({
  userName,
  onOpen,
  onLogout,
}: {
  userName: string;
  onOpen: (space: { id: string; name: string }) => void;
  onLogout: () => void;
}) {
  const spaces = useV1(getMyDataSpaces, []);
  const name = givenName(userName);

  return (
    <div className="flex min-h-screen flex-col items-center bg-card">
      <BrandBar>
        <span className="text-muted-foreground da-caption">
          {name} ·{' '}
          <button
            type="button"
            onClick={onLogout}
            className="hover:text-foreground hover:underline"
          >
            Đăng xuất
          </button>
        </span>
      </BrandBar>
      <main className="flex w-[880px] max-w-[calc(100vw-32px)] flex-col gap-da-lg pb-da-2xl pt-[56px]">
        <div className="flex flex-col gap-da-md">
          <h1 className="text-foreground da-heading-1">Chào {name}, chọn Data Space để bắt đầu</h1>
          <p className="max-w-[580px] text-muted-foreground da-body-small">
            Mỗi Data Space là một vùng dữ liệu riêng: nguồn, chỉ số, agent và quyền tách biệt. Bạn
            có thể đổi Data Space bất cứ lúc nào ở góc trên bên trái.
          </p>
        </div>
        <AsyncRegion
          state={spaces}
          what="Data Space của bạn"
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState
              icon="lock"
              title="Bạn chưa thuộc Data Space nào"
              description="Quản trị viên cần thêm bạn vào một Data Space trước khi bạn dùng được dữ liệu."
            />
          }
        >
          {(data) => <Choices data={data} onOpen={onOpen} />}
        </AsyncRegion>
      </main>
    </div>
  );
}

function Choices({
  data,
  onOpen,
}: {
  data: MyDataSpaces;
  onOpen: (space: { id: string; name: string }) => void;
}) {
  const toast = useToast();
  const [openLast, setOpenLast] = useState(data.open_last_used);
  const [requested, setRequested] = useState<Set<string>>(
    new Set(data.items.filter((s) => s.request_pending).map((s) => s.id)),
  );
  const recent = data.items.find((s) => s.access === 'member' && s.last_used_text)?.id;

  const toggle = async (value: boolean) => {
    setOpenLast(value);
    try {
      await setOpenLastUsed(value);
    } catch (e) {
      setOpenLast(!value);
      toast({ tone: 'error', message: `Không lưu được lựa chọn: ${(e as Error).message}` });
    }
  };

  const ask = async (space: DataSpaceChoice) => {
    const until = new Date(Date.now() + 90 * 86_400_000).toISOString().slice(0, 10);
    try {
      await requestAccess({
        data_space_id: space.id,
        capability: 'view_aggregate',
        reason: `Xin quyền vào Data Space ${space.name}.`,
        requested_until: until,
      });
      setRequested((prev) => new Set(prev).add(space.id));
      toast({
        tone: 'success',
        message: `Đã gửi yêu cầu tới ${space.owner_name ?? 'người phụ trách'}.`,
      });
    } catch (e) {
      toast({ tone: 'error', message: `Không gửi được yêu cầu: ${(e as Error).message}` });
    }
  };

  return (
    <>
      <ul className="flex flex-col gap-da-lg">
        {data.items.map((space) => (
          <li
            key={space.id}
            className="flex items-center gap-da-lg rounded-lg border border-border bg-card px-da-lg py-da-lg"
          >
            <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
              <div className="flex flex-wrap items-center gap-da-md">
                <h2 className="text-foreground da-heading-3">{space.name}</h2>
                {space.access === 'member' ? (
                  <StatusDot tone={TONE[space.role_kind ?? 'viewer']}>
                    {[space.role_label, space.scope_text].filter(Boolean).join(' · ')}
                  </StatusDot>
                ) : (
                  <StatusDot tone="warning">Chưa có quyền</StatusDot>
                )}
              </div>
              <p className="text-muted-foreground da-caption">
                {space.access === 'member'
                  ? `${space.source_count} nguồn · ${space.metric_count} chỉ số · ${space.agent_count} agent  ·  ${
                      space.last_used_text ? `Dùng gần nhất: ${space.last_used_text}` : 'Chưa dùng'
                    }`
                  : `Người phụ trách: ${space.owner_name ?? '—'}`}
              </p>
            </div>
            {space.access === 'member' ? (
              <Button
                kind={space.id === recent ? 'primary' : 'secondary'}
                aria-label={`Mở ${space.name}`}
                onClick={() => onOpen({ id: space.id, name: space.name })}
              >
                Mở
              </Button>
            ) : (
              <Button
                kind="ghost"
                disabled={requested.has(space.id)}
                onClick={() => void ask(space)}
                aria-label={`Yêu cầu quyền vào ${space.name}`}
              >
                {requested.has(space.id) ? 'Đã gửi yêu cầu' : 'Yêu cầu quyền'}
              </Button>
            )}
          </li>
        ))}
      </ul>
      <Checkbox checked={openLast} onChange={(v) => void toggle(v)}>
        Lần sau mở thẳng Data Space dùng gần nhất
      </Checkbox>
    </>
  );
}
