import React from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import { Placeholder } from './components/shared/Placeholder';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { ForbiddenToast } from './components/shared/ForbiddenToast';
import { ROUTE_MAP, SINGLE_SCREEN } from './routes';
import { MODULE_SUB_ITEMS } from './constants';

const RouteLoading = () => (
  <div className="ui-empty min-h-[320px]">
    <div className="w-8 h-8 rounded-full border-2 border-[#BFA66A] border-t-[#8A5A00] animate-spin mb-3" />
    <p className="text-[12px] text-[#5F5F5F]">Đang tải màn hình…</p>
  </div>
);

function AppContent() {
  const { activeModule, setActiveModule, subTab } = useAppState();

  // Identity is never sent from the client: the bearer token goes to Kong, which
  // injects the authoritative X-* identity headers the backend trusts.

  const renderView = () => {
    const subItems = MODULE_SUB_ITEMS[activeModule] ?? [];
    const routes = ROUTE_MAP[activeModule] ?? {};
    // Modules with a single screen have no secondary nav and route to `_`.
    const activeSub = subItems.length > 0 ? (subTab[activeModule] ?? subItems[0]?.id) : SINGLE_SCREEN;
    const entry = activeSub ? (routes[activeSub] ?? routes[SINGLE_SCREEN]) : undefined;

    if (!entry) {
      return (
        <Placeholder
          title="Not available"
          description="This area isn't available yet. Pick another item from the menu."
        />
      );
    }
    if ('comingSoon' in entry) {
      return <Placeholder {...entry.comingSoon} />;
    }
    return (
      <React.Suspense fallback={<RouteLoading />}>
        {entry.render()}
      </React.Suspense>
    );
  };

  return (
    <>
      <AppLayout activeTab={activeModule} setActiveTab={setActiveModule}>
        {renderView()}
      </AppLayout>
      {/* Progress overlay - always available, visible when ingestions active */}
      <IngestionProgressOverlay />
      {/* 403 from apiClient → toast (listens to window 'app:forbidden') */}
      <ForbiddenToast />
    </>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <AppContent />
    </AppStateProvider>
  );
}
