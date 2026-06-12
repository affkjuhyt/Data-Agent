import React from 'react';
import { motion } from 'motion/react';
import { Zap } from 'lucide-react';
import { useAppState } from '../../AppStateContext';
import { AgentRuntimeView, RegistryView } from './agents/AgentRuntime';
import KnowledgeOpsCenter from '../knowledge/KnowledgeOpsCenter';

const Placeholder = ({ label }: { label: string }) => (
  <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        color: '#B88719', fontSize: 10, fontWeight: 700,
        fontFamily: 'var(--font-mono)', letterSpacing: '0.14em',
        textTransform: 'uppercase', marginBottom: 6,
      }}>
        <Zap size={13} />
        Build / {label}
      </div>
      <h2 style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-display)', margin: 0 }}>
        {label}
      </h2>
    </div>
    <div style={{
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', padding: '60px 20px', gap: 12,
      background: '#FFFFFF', border: '1.5px solid #BFA66A', borderRadius: 14,
    }}>
      <div style={{
        width: 48, height: 48, borderRadius: 14,
        background: '#FFF1BF', border: '1.5px solid #B88719',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
      }}>
        <Zap size={22} style={{ color: '#8A5A00' }} />
      </div>
      <div style={{ fontSize: 11, fontWeight: 700, color: '#5C3900', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
        Coming Soon
      </div>
      <p style={{ fontSize: 13, color: '#5A5A5A', textAlign: 'center', maxWidth: 400, lineHeight: 1.6, margin: 0 }}>
        Backend integration pending.
      </p>
    </div>
  </div>
);

const registryViewMap: Record<string, RegistryView | null> = {
  AGENTS:    'OVERVIEW',
  NEW_AGENT: null,
  PROVISION: null,
};

const AIRuntimeCenter = () => {
  const { subTab, setSubTab } = useAppState();
  const activeSubTab = subTab['build'] ?? 'AGENTS';
  const setActiveSubTab = (id: string) => setSubTab('build', id);

  const externalRegistryView = registryViewMap[activeSubTab] ?? undefined;
  const openWizard    = activeSubTab === 'NEW_AGENT';
  const openProvision = activeSubTab === 'PROVISION';

  const handleNewAgent  = () => setActiveSubTab('NEW_AGENT');
  const handleProvision = () => setActiveSubTab('PROVISION');

  const renderContent = () => {
    if (activeSubTab === 'KNOWLEDGE') return <KnowledgeOpsCenter />;
    if (activeSubTab === 'ROUTER')    return <Placeholder label="Router" />;
    if (activeSubTab === 'TOOLS')     return <Placeholder label="Tools" />;

    // AGENTS, NEW_AGENT, PROVISION → AgentRuntimeView
    return (
      <AgentRuntimeView
        view={externalRegistryView}
        onViewChange={(v) => {
          const inverse: Record<RegistryView, string> = {
            OVERVIEW: 'AGENTS', CLI: 'AGENTS', CONFIG: 'AGENTS',
            TRACES: 'AGENTS', RUNS: 'AGENTS',
          };
          setActiveSubTab(inverse[v]);
        }}
        openWizard={openWizard}
        onWizardClose={() => setActiveSubTab('AGENTS')}
        openProvision={openProvision}
        onProvisionClose={() => setActiveSubTab('AGENTS')}
      />
    );
  };

  return (
    <motion.div
      key={activeSubTab}
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.18 }}
    >
      {renderContent()}
    </motion.div>
  );
};

export default AIRuntimeCenter;
