import React, { useEffect } from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import { Placeholder } from './components/shared/Placeholder';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { ROUTE_MAP } from './routes';
import { MODULE_SUB_ITEMS } from './constants';
import { setApiContext } from './lib/mockApi';

function AppContent() {
  const { activeModule, setActiveModule, subTab, user } = useAppState();

  // Initialize API context with user/role info
  useEffect(() => {
    setApiContext(
      user.role,
      user.id,
      '00000000-0000-0000-0000-000000000001' // Dev tenant ID
    );
  }, [user.role, user.id]);

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
