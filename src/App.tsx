import React from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import { Placeholder } from './components/shared/Placeholder';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { ChatPage } from './chat/ChatPage';
import { ROUTE_MAP } from './routes';
import { MODULE_SUB_ITEMS } from './constants';

function AppContent() {
  const { activeModule, setActiveModule, subTab } = useAppState();

  /* Chat is a standalone full-screen page — skip AppLayout entirely */
  if (activeModule === 'chat') {
    return <ChatPage onExit={() => setActiveModule('build')} />;
  }

  const renderView = () => {
    const subItems  = MODULE_SUB_ITEMS[activeModule] ?? [];
    const activeSub = subTab[activeModule] ?? subItems[0]?.id ?? 'DEFAULT';
    const entry     = ROUTE_MAP[activeModule]?.[activeSub] ?? ROUTE_MAP[activeModule]?.['DEFAULT'];

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
