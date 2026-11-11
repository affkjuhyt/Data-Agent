import { cn } from '../../lib/utils';
import { Avatar } from '../../design-system/Avatar';
import { Icon } from '../../design-system/Icon';
import { Menu } from '../../design-system/Menu';
import type { AreaId, AreaDef } from './areas';

/*
 * Figma "AppTopBar/v3" (2041:758) with the v3.1 labels approved 08/10/2026:
 * Trang chủ · Cần bạn · Agents · Phân tích · Dữ liệu · Quản trị.
 * 56px, card background, bottom border, 16px side padding, 12px gaps.
 * Left: brand mark, "Data Agent / <Data Space>" switcher. Then the areas; the active one on
 * subtle. After a 10px spacer, in sequence as in Figma (not pushed to the edge): the ⌘K
 * command bar, the running-agents pill, the bell and the user's avatar.
 *
 * Responsive ("Spec · Responsive"): from 1280 to 1439 the command bar shrinks to icon + ⌘K;
 * under 1280 every area except Trang chủ and Cần bạn moves into a "☰" menu. Under 768 (the
 * "v3.1 · Mobile" frames) the areas move to the bottom tab bar (MobileTabBar) and the bar keeps
 * only the Data Space, the bell and the avatar.
 */
interface Workspace {
  id: string;
  name: string;
}

export function AppTopBar({
  areas,
  active,
  onNavigate,
  needsYouCount,
  runningAgents,
  workspaceName,
  workspaces,
  onWorkspace,
  onOpenCommand,
  onOpenNotifications,
  onOpenAccount,
  onLogout,
  userName,
}: {
  areas: AreaDef[];
  active: AreaId;
  onNavigate: (area: AreaId) => void;
  needsYouCount?: number;
  /** Hidden until there is a source for it; never show a made-up number. */
  runningAgents?: number;
  workspaceName: string;
  workspaces: Workspace[];
  onWorkspace: (id: string) => void;
  onOpenCommand: () => void;
  onOpenNotifications?: () => void;
  /** Avatar menu: a Cài đặt page (PROFILE, NOTIFICATIONS, SHORTCUTS) or sign-out. */
  onOpenAccount?: (page: string) => void;
  onLogout?: () => void;
  userName: string;
}) {
  const shown = areas.filter((a) => !a.hidden);
  const folded = shown.filter((a) => !a.pinnedOnNarrow);

  return (
    <header className="sticky top-0 z-[var(--z-topbar)] flex h-14 shrink-0 items-center gap-da-lg border-b border-border bg-card px-da-xl max-[767px]:h-12 max-[767px]:gap-da-md max-[767px]:px-da-lg">
      <div className="flex shrink-0 items-center gap-da-md max-[767px]:min-w-0 max-[767px]:flex-1">
        <span className="inline-flex size-6 items-center justify-center rounded-md bg-primary text-primary-foreground max-[767px]:hidden">
          <Icon name="agent" />
        </span>
        <span className="text-foreground da-heading-3 max-[1023px]:hidden">Data Agent</span>
        <span className="text-border-strong da-body max-[1023px]:hidden" aria-hidden>
          /
        </span>
        <WorkspaceSwitcher name={workspaceName} workspaces={workspaces} onSelect={onWorkspace} />
      </div>

      <nav aria-label="Khu vực" className="flex shrink-0 items-center gap-da-xs max-[767px]:hidden">
        {shown.map((area) => (
          <AreaLink
            key={area.id}
            area={area}
            active={area.id === active}
            count={area.id === 'needs-you' ? needsYouCount : undefined}
            onNavigate={onNavigate}
            className={area.pinnedOnNarrow ? undefined : 'max-[1279px]:hidden'}
          />
        ))}
        <NarrowAreaMenu areas={folded} active={active} onNavigate={onNavigate} />
      </nav>

      <span className="w-2.5 shrink-0 max-[767px]:hidden" aria-hidden />

      <button
        type="button"
        onClick={onOpenCommand}
        aria-label="Mở bảng lệnh (⌘K)"
        className="flex h-8 w-[260px] shrink-0 items-center gap-da-sm rounded-md bg-subtle px-da-md text-left max-[1439px]:w-auto max-[767px]:hidden"
      >
        <Icon name="search" className="text-muted-foreground" />
        <span className="flex-1 truncate text-muted-foreground da-body-small max-[1439px]:hidden">
          Hỏi agent hoặc tìm…
        </span>
        <kbd className="rounded-sm border border-border px-da-xs text-muted-foreground da-mono">
          ⌘K
        </kbd>
      </button>

      {runningAgents !== undefined && runningAgents > 0 && (
        <span className="inline-flex shrink-0 items-center gap-da-sm rounded-full bg-accent px-da-md py-da-xs text-primary da-label max-[1023px]:hidden">
          <span className="size-1.5 rounded-full bg-primary" aria-hidden />
          {runningAgents} agent đang chạy
        </span>
      )}

      <button
        type="button"
        onClick={onOpenNotifications}
        aria-label="Thông báo"
        className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-subtle"
      >
        <Icon name="bell" />
      </button>
      <Menu
        label="Tài khoản"
        trigger={<Avatar name={userName} />}
        items={[
          { label: 'Hồ sơ', icon: 'users', onSelect: () => onOpenAccount?.('PROFILE') },
          { label: 'Thông báo', icon: 'bell', onSelect: () => onOpenAccount?.('NOTIFICATIONS') },
          { label: 'Phím tắt', onSelect: () => onOpenAccount?.('SHORTCUTS') },
          { kind: 'separator' },
          { label: 'Đăng xuất', onSelect: () => onLogout?.() },
        ]}
      />
    </header>
  );
}

function AreaLink({
  area,
  active,
  count,
  onNavigate,
  className,
}: {
  area: AreaDef;
  active: boolean;
  count?: number;
  onNavigate: (area: AreaId) => void;
  className?: string;
}) {
  return (
    <a
      href={`/${area.id}`}
      aria-current={active ? 'page' : undefined}
      onClick={(event) => {
        if (event.metaKey || event.ctrlKey || event.shiftKey) return;
        event.preventDefault();
        onNavigate(area.id);
      }}
      className={cn(
        'flex items-center gap-da-xs whitespace-nowrap rounded-md px-da-md py-da-sm da-body-strong transition-colors duration-[120ms] ease-out',
        active ? 'bg-subtle text-foreground' : 'text-muted-foreground hover:text-foreground',
        className,
      )}
    >
      {area.label}
      {count !== undefined && count > 0 && (
        <span
          className="rounded-full bg-warning px-da-xs text-primary-foreground da-label"
          aria-label={`${count} việc chờ bạn`}
        >
          {count}
        </span>
      )}
    </a>
  );
}

/*
 * The folded areas and the Data Space switcher are the design system's Menu (React Aria): it
 * closes on Esc or a click outside, moves with the arrow keys and returns focus to its button,
 * which the hand-written popovers with their own document listeners did not.
 */
function NarrowAreaMenu({
  areas,
  active,
  onNavigate,
}: {
  areas: AreaDef[];
  active: AreaId;
  onNavigate: (area: AreaId) => void;
}) {
  return (
    <div className="hidden max-[1279px]:block">
      <Menu
        label="Khu vực khác"
        triggerKind="custom"
        trigger="☰"
        triggerClassName="rounded-md px-da-md py-da-sm text-muted-foreground da-body-strong hover:text-foreground"
        placement="bottom start"
        items={areas.map((area) => ({
          label: area.label,
          selected: area.id === active,
          onSelect: () => onNavigate(area.id),
        }))}
      />
    </div>
  );
}

function WorkspaceSwitcher({
  name,
  workspaces,
  onSelect,
}: {
  name: string;
  workspaces: Workspace[];
  onSelect: (id: string) => void;
}) {
  if (workspaces.length <= 1)
    return <span className="flex items-center text-foreground da-body-strong">{name}</span>;
  return (
    <Menu
      // The button names the current Data Space, as its text shows; the menu is named after it.
      label={`Data Space: ${name}`}
      triggerKind="custom"
      trigger={
        <>
          {name}
          <Icon name="chevron-down" className="text-muted-foreground" />
        </>
      }
      triggerClassName="flex items-center gap-da-xs text-foreground da-body-strong"
      placement="bottom start"
      items={workspaces.map((ws) => ({
        label: ws.name,
        selected: ws.name === name,
        onSelect: () => onSelect(ws.id),
      }))}
    />
  );
}
