import React from 'react';
import { AppStateProvider, useAppState } from './AppStateContext';
import { AppLayout } from './components/Layout';
import KnowledgeOpsCenter from './components/knowledge/KnowledgeOpsCenter';
import AIRuntimeCenter from './components/agent-builder/AIRuntimeCenter';
import TestCenter from './components/test/TestCenter';
import SettingsView from './components/Settings/Overview';

const Placeholder = ({ module }: { module: string }) => (
  <div className="h-full flex items-center justify-center text-[#5F5F5F] font-display italic">
    Module &quot;{module}&quot; is under construction...
  </div>
);

function AppContent() {
  const { activeModule, setActiveModule } = useAppState();

  const renderView = () => {
    switch (activeModule) {
      case 'knowledge-ops':
        return <KnowledgeOpsCenter />;
      case 'build':
        return <AIRuntimeCenter />;
      case 'test':
        return <TestCenter />;
      case 'settings':
        return <SettingsView />;
      default:
        return <Placeholder module={activeModule} />;
    }
  };

  return (
    <AppLayout activeTab={activeModule} setActiveTab={setActiveModule}>
      {renderView()}
    </AppLayout>
  );
}

export default function App() {
  return (
    <AppStateProvider>
      <AppContent />
    </AppStateProvider>
  );
}
