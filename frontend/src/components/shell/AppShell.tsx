import { useEffect, useState, type ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { AREA_RAILS, AREAS, firstPageOf } from '../../constants';
import { AppTopBar } from './AppTopBar';
import { CommandPalette } from './CommandPalette';
import { SideRail } from './SideRail';
import type { AreaId } from './areas';
import { useNeedsYouCount } from './useNeedsYouCount';

/*
 * The frame shared by every desktop screen ("Spec · Khung trang"): the 56px top bar and the
 * 208px rail stay fixed, only the main region scrolls. Areas without a rail give their screen
 * the full width. ⌘K / Ctrl+K opens the command palette from anywhere.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { activeModule, subTab, navigate, setSubTab, tenant, tenants, setTenant, user } =
    useAppState();
  const area = activeModule as AreaId;
  const rail = AREA_RAILS[area];
  const needsYouCount = useNeedsYouCount();
  const [paletteOpen, setPaletteOpen] = useState(false);

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
        needsYouCount={needsYouCount}
        workspaceName={tenant.tenantName ?? 'Toàn tổ chức'}
        workspaces={tenants.map((t) => ({ id: t.id, name: t.name }))}
        onWorkspace={(id) => {
          const chosen = tenants.find((t) => t.id === id);
          if (chosen)
            setTenant({ tenantId: chosen.id, tenantName: chosen.name, slug: chosen.slug });
        }}
        onOpenCommand={() => setPaletteOpen(true)}
        userName={user.name}
      />
      <div className="flex min-h-0 flex-1">
        {rail && (
          <SideRail
            label={AREAS.find((a) => a.id === area)?.label ?? ''}
            groups={rail}
            active={subTab[area] ?? firstPageOf(area) ?? ''}
            onSelect={(page) => setSubTab(area, page)}
          />
        )}
        <main className="min-w-0 flex-1 overflow-y-auto">{children}</main>
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
