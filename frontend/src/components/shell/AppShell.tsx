import { useEffect, useRef, useState, type ReactNode } from 'react';
import { DATA_SPACE_STORAGE_KEY, ORGANISATION_SCOPE, useAppState } from '../../AppStateContext';
import { AREA_RAILS, AREAS, firstPageOf, isRailPage, railItemOf } from '../../constants';
import { AppTopBar } from './AppTopBar';
import { CommandPalette } from './CommandPalette';
import { SideRail } from './SideRail';
import type { AreaId, RailGroup } from './areas';
import { useRailCounts } from './useRailCounts';
import { useShellCounts } from './useShellCounts';
import { listDataSpaces } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';
import { MobileTabBar, mobileTabOf } from './MobileTabBar';
import { NotificationDrawer } from '../../features/notifications/NotificationDrawer';
import { useAuth } from '../../lib/AuthProvider';

/*
 * The frame shared by every desktop screen ("Spec · Khung trang"): the 56px top bar and the
 * 208px rail stay fixed, only the main region scrolls. The rail shows on an area's rail pages;
 * landings and off-rail pages get the full width. ⌘K / Ctrl+K opens the command palette.
 */
/**
 * The rail groups the viewer may see: items needing a capability they lack are left out.
 * Capabilities are A1.2's (`exec_vault`, `admin`, …): GET /v1/me's organisation-wide ones plus
 * what the chosen Data Space adds. The rail only hides links; the API still refuses on its own.
 */
export function railFor(groups: RailGroup[] | undefined, capabilities: string[]): RailGroup[] | undefined {
  if (!groups) return undefined;
  const held = new Set(capabilities);
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.requires || held.has(item.requires)),
    }))
    .filter((group) => group.items.length > 0);
}

export function AppShell({
  children,
  initialSpaceId = '',
}: {
  children: ReactNode;
  /** The space picked on "v3.1 · Chọn Data Space" before the shell opened; '' = Toàn tổ chức. */
  initialSpaceId?: string;
}) {
  const { activeModule, subTab, navigate, user, dataSpace, setDataSpace } = useAppState();
  const { logout, me } = useAuth();
  const area = activeModule as AreaId;
  const page = subTab[area] ?? firstPageOf(area);
  // The Data Space switcher ("v3.1 · Chọn Data Space"): the spaces the viewer belongs to
  // (GET /v1/data-spaces). "Toàn tổ chức" sends no X-Data-Space; choosing a space sets the
  // header for every v1 call and remounts the page so it reloads for that space.
  const spaces = useV1(['listDataSpaces'], listDataSpaces);
  const chosen = spaces.data?.items.find((ds) => ds.id === dataSpace?.id);
  const rail = isRailPage(area, page)
    ? railFor(AREA_RAILS[area], [...(me?.capabilities ?? []), ...(chosen?.my_capabilities ?? [])])
    : undefined;
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  const pickedBeforeShell = useRef(initialSpaceId);
  const storedSpace = typeof window !== 'undefined'
    ? window.localStorage.getItem(DATA_SPACE_STORAGE_KEY)
    : null;
  const organisationScope = storedSpace === ORGANISATION_SCOPE;
  const candidateId = pickedBeforeShell.current
    || me?.default_data_space_id
    || (storedSpace && storedSpace !== ORGANISATION_SCOPE ? storedSpace : '')
    || spaces.data?.items[0]?.id
    || '';

  useEffect(() => {
    const id = candidateId;
    if (!id) return;
    const ds = spaces.data?.items.find((s) => s.id === id);
    if (ds) {
      pickedBeforeShell.current = '';
      setDataSpace({ id: ds.id, name: ds.name });
    }
  }, [candidateId, spaces.data, setDataSpace]);
  const chooseSpace = (id: string) => {
    const ds = spaces.data?.items.find((s) => s.id === id);
    setDataSpace(ds ? { id: ds.id, name: ds.name } : null);
  };
  const spaceOptions = [
    { id: '', name: 'Toàn tổ chức' },
    ...(spaces.data?.items ?? []).map((ds) => ({ id: ds.id, name: ds.name })),
  ];
  const spaceName = dataSpace?.name ?? 'Toàn tổ chức';
  // Data screens require X-Data-Space. Do not mount them during the bootstrap render where
  // the list has arrived but the selected id has not yet reached the shared API client.
  const spaceReady =
    spaces.data !== undefined
    && (organisationScope || dataSpace !== null || spaces.data.items.length === 0);
  // Both the rail and the global badges call Data Space-scoped list APIs. They must follow the
  // same bootstrap gate as the page content, otherwise the shell itself creates 400s on reload.
  const dataSpaceReady = spaceReady && dataSpace !== null;
  // Only the area whose rail is on screen; a landing page draws no rail and loads no counts.
  const railCounts = useRailCounts(rail ? area : undefined, dataSpaceReady);
  const counts = useShellCounts(dataSpaceReady);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="flex h-screen flex-col bg-card text-foreground">
      <AppTopBar
        areas={AREAS}
        active={area}
        onNavigate={(id) => navigate(id, firstPageOf(id))}
        needsYouCount={counts.needsYou}
        runningAgents={counts.runningAgents}
        workspaceName={spaceName}
        workspaces={spaceOptions}
        onWorkspace={chooseSpace}
        onOpenCommand={() => setPaletteOpen(true)}
        onOpenNotifications={() => setNotificationsOpen(true)}
        onOpenAccount={(page) => navigate('account', page)}
        onLogout={logout}
        userName={user.name}
      />
      <div className="flex min-h-0 flex-1">
        {rail && (
          <SideRail
            label={AREAS.find((a) => a.id === area)?.label ?? ''}
            groups={rail}
            active={railItemOf(area, page) ?? ''}
            counts={area === 'agents' ? { ...railCounts, TEAM: counts.agents } : railCounts}
            area={area}
          />
        )}
        {/* Under 768px the bottom tab bar covers 57px plus the device inset; the page scrolls above it. */}
        <main
          key={dataSpace?.id ?? ''}
          className="min-w-0 flex-1 overflow-y-auto max-[767px]:pb-[calc(57px+env(safe-area-inset-bottom))]"
        >
          {spaceReady ? (
            children
          ) : (
            <div className="flex min-h-full items-center justify-center p-da-xl">
              <p className="text-muted-foreground da-body">Đang mở Data Space…</p>
            </div>
          )}
        </main>
      </div>
      <MobileTabBar
        active={mobileTabOf(area, page)}
        needsYouCount={counts.needsYou}
        onSelect={(tab) =>
          tab === 'ask' ? navigate('analysis', 'ASK') : navigate(tab, firstPageOf(tab))
        }
      />
      <NotificationDrawer
        open={notificationsOpen}
        onClose={() => setNotificationsOpen(false)}
        onOpenTarget={(route) =>
          navigate(route.area, route.page ?? firstPageOf(route.area), route.id)
        }
      />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={(target, page) => {
          setPaletteOpen(false);
          navigate(target, page ?? firstPageOf(target));
        }}
      />
    </div>
  );
}
