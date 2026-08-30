import React, { useState, useEffect } from 'react';
import { AnimatePresence } from 'motion/react';
import { AgentRegistryOverview } from './AgentOverview';
import { NewAgentWizard, type CreatedAgent } from './AgentWizard';
import { CLIScreen } from './AgentCLI';
import { ConfigRegistry } from './AgentConfig';
import { TraceExplorer } from './AgentTraces';
import { ProvisioningView } from './AgentProvisioning';
import { RunRegistry } from './AgentRuns';
import { AgentDetailView } from './AgentDetail';
import { AgentDetailPage } from './AgentDetailPage';
import { DetailDrawer } from '../../shared/DetailDrawer';
import WorkflowBuilder from '../workflow/Builder';
import { Bot, Terminal, Sliders, Activity, Zap, LayoutTemplate } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiMutate } from '../../../lib/apiClient';

type RegistryView = 'OVERVIEW' | 'CLI' | 'CONFIG' | 'TRACES' | 'RUNS';

interface AgentRuntimeProps {
  view?: RegistryView;
  onViewChange?: (view: RegistryView) => void;
  /* External imperative triggers */
  openWizard?: boolean;
  onWizardClose?: () => void;
  openProvision?: boolean;
  onProvisionClose?: () => void;
}

export const AgentRuntimeView = ({
  view: externalView,
  onViewChange,
  openWizard,
  onWizardClose,
  openProvision,
  onProvisionClose,
}: AgentRuntimeProps = {}) => {
  const [internalView, setInternalView] = useState<RegistryView>('OVERVIEW');
  const view = externalView ?? internalView;
  const setView = (v: RegistryView) => {
    if (onViewChange) onViewChange(v);
    else setInternalView(v);
  };

  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [quickViewAgentId, setQuickViewAgentId] = useState<string | null>(null);
  const [showWizard, setShowWizard] = useState(false);
  const [showProvisioning, setShowProvisioning] = useState(false);
  const [builderAgent, setBuilderAgent] = useState<CreatedAgent | null>(null);
  const [wizardRestore, setWizardRestore] = useState<{ name: string } | null>(null);

  useEffect(() => { if (openWizard) setShowWizard(true); }, [openWizard]);
  useEffect(() => { if (openProvision) setShowProvisioning(true); }, [openProvision]);

  const handleCloseWizard = () => {
    setShowWizard(false);
    onWizardClose?.();
  };

  const handleWizardComplete = (data: CreatedAgent) => {
    setShowWizard(false);
    setBuilderAgent(data);
    // onWizardClose không gọi ở đây — gọi nó sẽ đổi activeSubTab trong AIRuntimeCenter
    // → key={activeSubTab} trên motion.div unmount AgentRuntimeView → mất builderAgent state
  };
  const handleCloseProvision = () => {
    setShowProvisioning(false);
    onProvisionClose?.();
  };

  const handleSelectAgent = (id: string) => {
    setQuickViewAgentId(null);
    setSelectedAgentId(id);
    setView('OVERVIEW'); // stay in OVERVIEW but show detail
  };

  const renderView = () => {
    // Agent Detail — full page, replaces list
    if (selectedAgentId && view === 'OVERVIEW') {
      return (
        <AgentDetailPage
          key={selectedAgentId}
          agentId={selectedAgentId}
          onBack={() => setSelectedAgentId(null)}
        />
      );
    }

    switch (view) {
      case 'CLI':     return <CLIScreen />;
      case 'CONFIG':  return <ConfigRegistry onSelect={handleSelectAgent} onQuickView={setQuickViewAgentId} />;
      case 'TRACES':  return <TraceExplorer />;
      case 'RUNS':    return <RunRegistry />;
      case 'OVERVIEW':
      default:
        return (
          <AgentRegistryOverview
            onNewAgent={() => setShowWizard(true)}
            onOpenCLI={() => setView('CLI')}
            onOpenProvision={() => setShowProvisioning(true)}
            onSelectAgent={handleSelectAgent}
          />
        );
    }
  };

  if (builderAgent) {
    return (
      <WorkflowBuilder
        workflow={null}
        agentId={builderAgent.agentId}
        workflowId={builderAgent.workflowId}
        workflowVersionId={builderAgent.workflowVersionId}
        isNewWorkflow
        initialNodes={builderAgent.initialNodes}
        initialEdges={builderAgent.initialEdges}
        onClose={() => setBuilderAgent(null)}
        onLeave={async () => {
          try {
            await apiMutate('DELETE', `/api/agents/${builderAgent.agentId}`);
          } catch { /* agent may not exist yet — ignore */ }
          setWizardRestore({ name: builderAgent.name });
          setBuilderAgent(null);
        }}
      />
    );
  }

  return (
    <div className="flex flex-col relative">
      {/* Internal view tabs — kept as a fallback so the component still works standalone */}
      {!externalView && (
        <div className="sub-tab-bar mb-6">
          {[
            { id: 'OVERVIEW', label: 'Registry', icon: Bot },
            { id: 'CONFIG',   label: 'Config',   icon: Sliders },
            { id: 'TRACES',   label: 'Traces',   icon: Activity },
            { id: 'RUNS',     label: 'Runs',     icon: Zap },
            { id: 'CLI',      label: 'CLI',      icon: Terminal },
          ].map(nav => (
            <button
              key={nav.id}
              onClick={() => { setView(nav.id as RegistryView); setSelectedAgentId(null); }}
              className={cn('sub-tab', view === nav.id && 'active')}
            >
              <nav.icon className="tab-icon w-3.5 h-3.5" />
              {nav.label}
            </button>
          ))}
        </div>
      )}

      {(showWizard || !!wizardRestore) && (
        wizardRestore ? (
          <NewAgentWizard
            onCancel={() => setWizardRestore(null)}
            onComplete={data => { setWizardRestore(null); setBuilderAgent(data); }}
            initialStep={2}
            initialName={wizardRestore.name}
          />
        ) : (
          <NewAgentWizard
            onCancel={handleCloseWizard}
            onComplete={handleWizardComplete}
          />
        )
      )}

      <DetailDrawer
        isOpen={showProvisioning}
        onClose={handleCloseProvision}
        title="Agent templates"
        subtitle="Template lưu trong platform settings — tạo agent qua Wizard"
        icon={LayoutTemplate}
        size="standard"
        persistKey="agent-provision"
      >
        <ProvisioningView
          onUseTemplate={t => { handleCloseProvision(); setWizardRestore({ name: t.name }); }}
          onCreateBlank={() => { handleCloseProvision(); setShowWizard(true); }}
        />
      </DetailDrawer>

      <DetailDrawer
        isOpen={!!quickViewAgentId}
        onClose={() => setQuickViewAgentId(null)}
        title="Agent"
        subtitle="Xem nhanh cấu hình và runs gần đây"
        icon={Bot}
        size="wide"
        persistKey="agent-quick-view"
      >
        {quickViewAgentId && (
          <AgentDetailView
            key={quickViewAgentId}
            agentId={quickViewAgentId}
            onOpenConfig={() => handleSelectAgent(quickViewAgentId)}
            onViewRuns={() => { setQuickViewAgentId(null); setView('RUNS'); }}
          />
        )}
      </DetailDrawer>

      <AnimatePresence mode="wait">
        <div key={view} className="flex-1">
          {renderView()}
        </div>
      </AnimatePresence>

    </div>
  );
};
