import { useEffect, useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { AREA_RAILS, AREAS, firstPageOf, isRailPage } from '../../constants';
import { AppTopBar } from './AppTopBar';
import { CommandPalette } from './CommandPalette';
import { SideRail } from './SideRail';
import type { AreaId } from './areas';
import { useShellCounts } from './useShellCounts';
import { setDataSpace } from '../../lib/v1/client';
import { listDataSpaces } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * The frame shared by every desktop screen ("Spec · Khung trang"): the 56px top bar and the
 * 208px rail stay fixed, only the main region scrolls. The rail shows on an area's rail pages;
 * landings and off-rail pages get the full width. ⌘K / Ctrl+K opens the command palette.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { activeModule, subTab, navigate, setSubTab, user } = useAppState();
  const area = activeModule as AreaId;
  const page = subTab[area] ?? firstPageOf(area);
  const rail = isRailPage(area, page) ? AREA_RAILS[area] : undefined;
  const counts = useShellCounts();
  const [paletteOpen, setPaletteOpen] = useState(false);

  // The Data Space switcher ("v3.1 · Chọn Data Space"): the spaces the viewer belongs to
  // (GET /v1/data-spaces). "Toàn tổ chức" sends no X-Data-Space; choosing a space sets the
  // header for every v1 call and remounts the page so it reloads for that space.
  const spaces = useV1(listDataSpaces, []);
  const [dataSpaceId, setDataSpaceId] = useState<string>('');
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
        userName={user.name}
      />
      <div className="flex min-h-0 flex-1">
        {rail && (
          <SideRail
            label={AREAS.find((a) => a.id === area)?.label ?? ''}
            groups={rail}
            active={page ?? ''}
            counts={area === 'agents' ? { TEAM: counts.agents } : undefined}
            onSelect={(page) => setSubTab(area, page)}
          />
        )}
        <main key={dataSpaceId} className="min-w-0 flex-1 overflow-y-auto">
          {children}
        </main>
      </div>
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
