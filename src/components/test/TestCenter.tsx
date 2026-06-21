import React from 'react';
import { motion } from 'motion/react';
import { ClipboardCheck, Zap } from 'lucide-react';
import { useAppState } from '../../AppStateContext';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import AgentPlayground from '../agent-builder/AgentPlayground';

const Placeholder = ({ label }: { label: string }) => (
  <div style={{
    display: 'flex', flexDirection: 'column', alignItems: 'center',
    justifyContent: 'center', padding: '80px 20px', gap: 12,
  }}>
    <div style={{
      width: 48, height: 48, borderRadius: 14,
      background: '#FFF1BF', border: '1.5px solid #B88719',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <ClipboardCheck size={22} style={{ color: '#8A5A00' }} />
    </div>
    <div style={{ fontSize: 11, fontWeight: 700, color: '#5C3900', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
      Coming Soon
    </div>
    <h3 style={{ fontSize: 18, fontWeight: 600, color: '#111', margin: 0 }}>{label}</h3>
    <p style={{ fontSize: 13, color: '#5A5A5A', textAlign: 'center', maxWidth: 400, lineHeight: 1.6, margin: 0 }}>
      Routing accuracy evals, confusion matrix and regression test suite — coming in Phase T2.
    </p>
  </div>
);

const TestCenter = () => {
  const { subTab, setSubTab } = useAppState();
  const activeSubTab = subTab['test'] ?? 'PLAYGROUND';

  return (
    <div className="space-y-7">
      <OperationalHeader
        title="Test"
        subtitle="Playground for live agent testing and routing evaluation suites"
        breadcrumbs={[{ label: 'Test' }]}
        status={<StatusBadge status="ACTIVE" size="lg" />}
        actions={
          <button className="btn-secondary" onClick={() => setSubTab('test', 'EVALS')}>
            <ClipboardCheck className="w-4 h-4" />
            Run Evals
          </button>
        }
      />

      <motion.div
        key={activeSubTab}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.18 }}
      >
        {activeSubTab === 'PLAYGROUND' && <AgentPlayground />}
        {activeSubTab === 'EVALS'      && <Placeholder label="Evals" />}
      </motion.div>
    </div>
  );
};

export default TestCenter;
