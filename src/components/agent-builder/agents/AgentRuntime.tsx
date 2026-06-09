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
import { Bot, Terminal, Sliders, Activity, Zap, Plus, Settings } from 'lucide-react';
import { cn } from '../../../lib/utils';

const FLOW_BUILDER_URL = (import.meta as any).env?.VITE_FLOW_BUILDER_URL ?? 'http://localhost:8002';

export type RegistryView = 'OVERVIEW' | 'CLI' | 'CONFIG' | 'TRACES' | 'RUNS';

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
      case 'CONFIG':  return <ConfigRegistry />;
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
          await fetch(`${FLOW_BUILDER_URL}/api/agents/${builderAgent.agentId}`, { method: 'DELETE' });
          setWizardRestore({ name: builderAgent.name });
          setBuilderAgent(null);
        }}
      />
    );
  }

  return (
    <div className="flex flex-col relative">
      {/* Internal view tabs â€” kept as a fallback so the component still works standalone */}
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
        title="Fleet Provisioning"
        subtitle="Autoscale agent clusters based on demand"
        icon={Zap}
        size="standard"
        persistKey="agent-provision"
      >
        <ProvisioningView />
      </DetailDrawer>

      <DetailDrawer
        isOpen={false}
        onClose={() => setSelectedAgentId(null)}
        title={selectedAgentId ? `Agent: ${selectedAgentId}` : 'Agent Detail'}
        subtitle="Control Plane â€¢ Production Runtime â€¢ V2.0"
        icon={Bot}
        size="xwide"
        persistKey="agent-detail"
        tabs={[
          { id: 'CONFIG',  label: 'Configuration', icon: Settings },
          { id: 'LOGS',    label: 'Real-time Logs', icon: Terminal },
          { id: 'METRICS', label: 'Metrics',       icon: Activity },
          { id: 'POLICY',  label: 'Governance',    icon: Sliders },
        ]}
        footer={
          <div className="flex gap-3">
            <button className="btn-secondary">Restart Instance</button>
            <button className="btn-primary">Synchronize Configuration</button>
          </div>
        }
      >
        {selectedAgentId && (
          <AgentDetailView
            agentId={selectedAgentId}
            onBack={() => setSelectedAgentId(null)}
            onRun={() => { setView('RUNS'); setSelectedAgentId(null); }}
            onTrace={() => { setView('TRACES'); setSelectedAgentId(null); }}
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
