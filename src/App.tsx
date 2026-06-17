import React from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import { Placeholder } from './components/shared/Placeholder';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { ROUTE_MAP } from './routes';
import { MODULE_SUB_ITEMS } from './constants';

function AppContent() {
  const { activeModule, setActiveModule, subTab } = useAppState();

  // Identity is never sent from the client: the bearer token goes to Kong, which
  // injects the authoritative X-* identity headers the backend trusts.

  const renderView = () => {
    const subItems = MODULE_SUB_ITEMS[activeModule] ?? [];
    const activeSub = subTab[activeModule] ?? subItems[0]?.id;
    const entry = activeSub ? ROUTE_MAP[activeModule]?.[activeSub] : undefined;

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
    return <>{entry.render()}</>;
  };

  return (
    <>
      <AppLayout activeTab={activeModule} setActiveTab={setActiveModule}>
        {renderView()}
      </AppLayout>
      {/* Progress overlay - always available, visible when ingestions active */}
      <IngestionProgressOverlay />
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
