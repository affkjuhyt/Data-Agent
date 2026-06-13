import React, { useEffect } from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import KnowledgeOpsCenter from './components/knowledge/KnowledgeOpsCenter';
import AIRuntimeCenter from './components/agent-builder/AIRuntimeCenter';
import SettingsView from './components/Settings/Overview';
import IngestionProgressOverlay from './components/shared/IngestionProgressOverlay';
import { setApiContext } from './lib/mockApi';

const Placeholder = ({ module }: { module: string }) => (
  <div className="h-full flex items-center justify-center text-[#5F5F5F] font-display italic">
    Module &quot;{module}&quot; is under construction...
  </div>
);

function AppContent() {
  const { activeModule, setActiveModule, user } = useAppState();

  // Initialize API context with user/role info
  useEffect(() => {
    setApiContext(
      user.role,
      user.id,
      '00000000-0000-0000-0000-000000000001' // Dev tenant ID
    );
  }, [user.role, user.id]);

  const renderView = () => {
    switch (activeModule) {
      case 'knowledge-ops':
        return <KnowledgeOpsCenter />;
      case 'ai-runtime':
        return <AIRuntimeCenter />;
      case 'settings':
        return <SettingsView />;
      default:
        return <Placeholder module={activeModule} />;
    }
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
