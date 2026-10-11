import { useEffect, useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { AREA_RAILS, AREAS, firstPageOf, isRailPage, railItemOf } from '../../constants';
import { AppTopBar } from './AppTopBar';
import { CommandPalette } from './CommandPalette';
import { SideRail } from './SideRail';
import type { AreaId, RailGroup } from './areas';
import { useRailCounts } from './useRailCounts';
import { useShellCounts } from './useShellCounts';
import { setDataSpace } from '../../lib/v1/client';
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
// "exec-vault" and "exec_vault" are the same capability, whichever way Keycloak spells it.
const normalise = (role: string) => role.toLowerCase().replace(/-/g, '_');

/** The rail groups the viewer may see: items needing a capability they lack are left out. */
export function railFor(groups: RailGroup[] | undefined, roles: string[]): RailGroup[] | undefined {
  if (!groups) return undefined;
  const held = new Set(roles.map(normalise));
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.requires || held.has(normalise(item.requires))),
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
  const { activeModule, subTab, navigate, setSubTab, user } = useAppState();
  const { logout, user: authUser } = useAuth();
  const area = activeModule as AreaId;
  const page = subTab[area] ?? firstPageOf(area);
  const rail = isRailPage(area, page)
    ? railFor(AREA_RAILS[area], authUser?.roles ?? [])
    : undefined;
  // Only the area whose rail is on screen; a landing page draws no rail and loads no counts.
  const railCounts = useRailCounts(rail ? area : undefined);
  const counts = useShellCounts();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [notificationsOpen, setNotificationsOpen] = useState(false);

  // The Data Space switcher ("v3.1 · Chọn Data Space"): the spaces the viewer belongs to
  // (GET /v1/data-spaces). "Toàn tổ chức" sends no X-Data-Space; choosing a space sets the
  // header for every v1 call and remounts the page so it reloads for that space.
  const spaces = useV1(listDataSpaces, []);
  const [dataSpaceId, setDataSpaceId] = useState<string>(initialSpaceId);
  useEffect(() => {
    setDataSpace(initialSpaceId || null);
  }, [initialSpaceId]);
  const chooseSpace = (id: string) => {
    setDataSpace(id || null);
    setDataSpaceId(id);
  };
  const spaceOptions = [
    { id: '', name: 'Toàn tổ chức' },
    ...(spaces.data?.items ?? []).map((ds) => ({ id: ds.id, name: ds.name })),
  ];
  const spaceName = spaceOptions.find((o) => o.id === dataSpaceId)?.name ?? 'Toàn tổ chức';

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
            onSelect={(page) => setSubTab(area, page)}
          />
        )}
        {/* Under 768px the bottom tab bar covers 57px plus the device inset; the page scrolls above it. */}
        <main
          key={dataSpaceId}
          className="min-w-0 flex-1 overflow-y-auto max-[767px]:pb-[calc(57px+env(safe-area-inset-bottom))]"
        >
          {children}
        </main>
      </div>
      <MobileTabBar
        active={mobileTabOf(area, page)}
        needsYouCount={counts.needsYou}
        onSelect={(tab) => (tab === 'ask' ? navigate('analysis', 'ASK') : navigate(tab, firstPageOf(tab)))}
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
