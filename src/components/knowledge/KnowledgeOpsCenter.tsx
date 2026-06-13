import React, { useState, useEffect } from 'react';
import {
  Database,
  ShieldAlert,
  Network,
  Layers,
  Zap,
  ShieldCheck,
  ArrowRight,
  Eye,
  Clock,
  Terminal,
  Sparkles,
  Edit2,
  Table2,
  Settings,
  ServerCog,
  Boxes,
} from 'lucide-react';
import { cn } from '../../lib/utils';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { ConflictWorkspace } from './conflicts/ConflictWorkspace';
import { IngestionWizard } from './ingest/IngestionWizard';
import { AssetDetailWorkspace } from './inventory/AssetDetailWorkspace';
import { DetailDrawer } from '../shared/DetailDrawer';
import { FleetOverview } from './fleet/FleetOverview';
import { AssetInventory } from './inventory/AssetInventory';
import { PolicyCenter } from './policy/PolicyCenter';
import { KnowledgeBaseCenter } from './knowledge-base/KnowledgeBaseCenter';
import { WarehouseWizard } from './warehouse/WarehouseWizard';
import { KnowledgeHubView } from './knowledge-hub/KnowledgeHub';
import { KnowledgeDocument } from '../../types';
import { useAppState } from '../../AppStateContext';
import { mockMutate } from '../../lib/mockApi';
import { usePromotionQueue } from './inventory/usePromotionQueue';
import { PromotionProgressOverlay } from './inventory/PromotionProgressOverlay';

type KnowledgeTabId = 'FLEET' | 'INGEST' | 'INVENTORY' | 'CONFLICTS' | 'KNOWLEDGE' | 'POLICY' | 'KNOWLEDGE_BASE';

const KnowledgeOpsCenter = () => {
  const { subTab, setSubTab, updateDocument, pendingAction, setPendingAction } = useAppState();
  const { queue: promotionQueue, startPromotion, dismissEntry } = usePromotionQueue(updateDocument);
  const promotingIds = new Set(
    promotionQueue.filter(e => e.status === 'queued' || e.status === 'processing').map(e => e.data_id),
  );
  const activeSubTab = (subTab['knowledge-ops'] as KnowledgeTabId) ?? 'FLEET';
  const setActiveSubTab = (id: KnowledgeTabId) => setSubTab('knowledge-ops', id);

  const [selectedAsset, setSelectedAsset] = useState<KnowledgeDocument | null>(null);
  const [selectedAssetReadOnly, setSelectedAssetReadOnly] = useState(false);
  const [assetTab, setAssetTab] = useState<'PREVIEW' | 'CHUNKS' | 'TABLES' | 'LOGS' | 'TIMELINE' | 'CONFIGS'>('PREVIEW');
  const [showIngestionWizard, setShowIngestionWizard] = useState(false);
  const [showWarehouseWizard, setShowWarehouseWizard] = useState(false);
  const [showPromoteWizard, setShowPromoteWizard] = useState(false);

  // Handle sidebar action buttons (ACTION_INGEST, ACTION_WAREHOUSE)
  useEffect(() => {
    if (pendingAction === 'ACTION_INGEST') {
      setShowIngestionWizard(true);
      setPendingAction(null);
    } else if (pendingAction === 'ACTION_WAREHOUSE') {
      setShowWarehouseWizard(true);
      setPendingAction(null);
    }
  }, [pendingAction, setPendingAction]);

  const isWarehouse = (type: string | undefined) => !!type?.startsWith('Warehouse/');
  const hasTables = (type: string | undefined) =>
    !type || type.startsWith('Doc/') || type.toLowerCase() === 'web';

  const handleSelectAsset = (asset: KnowledgeDocument) => {
    setSelectedAsset(asset);
    setSelectedAssetReadOnly(activeSubTab === 'INVENTORY' && asset.current_tier === 'gold');
    // Always reset to a valid default tab for the asset type
    setAssetTab(isWarehouse(asset.metadata?.doc_type) ? 'CONFIGS' : 'PREVIEW');
  };

  const subTabs = [
    { id: 'FLEET',     label: 'Fleet Overview', icon: Database },
    { id: 'INVENTORY', label: 'Data Layers',    icon: Layers },
    { id: 'KNOWLEDGE', label: 'Knowledge Hub',  icon: Network },
    { id: 'CONFLICTS', label: 'Conflicts',      icon: ShieldAlert, count: 12 },
    { id: 'POLICY',    label: 'Policies',       icon: ShieldCheck },
    { id: 'KNOWLEDGE_BASE', label: 'Knowledge Bases', icon: Boxes },
  ];

  const renderContent = () => {
    switch (activeSubTab) {
      case 'FLEET':  return <FleetOverview />;
      case 'INGEST':
        return (
          <div className="max-w-4xl mx-auto bg-white border border-[#BFA66A]/20 rounded-3xl overflow-hidden shadow-sm animate-in fade-in duration-300">
            <IngestionWizard
              onCancel={() => setActiveSubTab('FLEET')}
              onComplete={() => setActiveSubTab('INVENTORY')}
            />
          </div>
        );
      case 'INVENTORY':  return (
        <AssetInventory
          onSelectAsset={handleSelectAsset}
          onPromote={(doc) => startPromotion(doc.data_id, doc.name, doc.current_tier as 'bronze' | 'silver', doc.current_tier === 'bronze' ? 'silver' : 'gold')}
          promotingIds={promotingIds}
        />
      );
      case 'KNOWLEDGE':
        return <KnowledgeHubView />;
      case 'CONFLICTS':  return <ConflictWorkspace />;
      case 'POLICY':     return <PolicyCenter />;
      case 'KNOWLEDGE_BASE': return <KnowledgeBaseCenter />;
      default:           return null;
    }
  };

  return (
    <div className="space-y-8 h-full">

      {/* ── Ingestion wizard drawer ── */}
      <DetailDrawer
        isOpen={showIngestionWizard}
        onClose={() => setShowIngestionWizard(false)}
        title="Add Data"
        subtitle="Ingest raw sources to Bronze level data"
        icon={Zap}
        size="wide"
        persistKey="kb-ingest"
        footer={null}
      >
        <IngestionWizard
          onCancel={() => setShowIngestionWizard(false)}
          onComplete={() => {
            setShowIngestionWizard(false);
            setActiveSubTab('INVENTORY');
          }}
        />
      </DetailDrawer>

      {/* ── Warehouse wizard drawer ── */}
      <DetailDrawer
        isOpen={showWarehouseWizard}
        onClose={() => setShowWarehouseWizard(false)}
        title="Add Warehouse Connection"
        subtitle="Connect Snowflake or Databricks to the knowledge pipeline"
        icon={ServerCog}
        size="wide"
        persistKey="kb-warehouse"
        footer={null}
      >
        <WarehouseWizard
          onCancel={() => setShowWarehouseWizard(false)}
          onComplete={() => {
            setShowWarehouseWizard(false);
            setActiveSubTab('KNOWLEDGE');
          }}
        />
      </DetailDrawer>

      {/* ── Asset detail drawer ── */}
      <DetailDrawer
        isOpen={!!selectedAsset}
        onClose={() => {
          setSelectedAsset(null);
          setSelectedAssetReadOnly(false);
          setAssetTab('PREVIEW');
        }}
        title={selectedAsset?.name || 'Asset Detail'}
        subtitle={selectedAsset ? `${selectedAsset.current_tier.toUpperCase()} LAYER • ${""} • ${selectedAsset.data_id}` : ''}
        icon={selectedAsset?.current_tier === 'gold' ? Zap : selectedAsset?.current_tier === 'silver' ? Edit2 : Database}
        size="xwide"
        persistKey="kb-asset-detail"
        fixedHeight
        tabs={
          isWarehouse(selectedAsset?.metadata?.doc_type) ? [
            { id: 'PREVIEW',  label: 'Preview',  icon: Eye },
            { id: 'CONFIGS',  label: 'Configs',  icon: Settings },
            { id: 'TIMELINE', label: 'Timeline', icon: Clock },
          ] : selectedAsset?.current_tier === 'bronze' ? [
            { id: 'PREVIEW',  label: 'Preview',  icon: Eye },
            { id: 'LOGS',     label: 'Logs',     icon: Terminal },
            { id: 'TIMELINE', label: 'Timeline', icon: Clock },
          ] : [
            { id: 'PREVIEW',  label: 'Preview',  icon: Eye },
            { id: 'CHUNKS',   label: 'Chunks',   icon: Layers },
            ...(hasTables(selectedAsset?.metadata?.doc_type)
              ? [{ id: 'TABLES', label: 'Tables', icon: Table2 }]
              : []),
            { id: 'LOGS',     label: 'Logs',     icon: Terminal },
            { id: 'TIMELINE', label: 'Timeline', icon: Clock },
          ]
        }
        activeTab={assetTab}
        onTabChange={(id) => setAssetTab(id as any)}
        footer={
          <div className="flex gap-3 justify-end w-full">
            {selectedAsset && selectedAsset.current_tier !== 'gold' && !isWarehouse(selectedAsset.metadata?.doc_type) && !selectedAssetReadOnly && (
              <button
                onClick={async () => {
                  const doc = selectedAsset;
                  const targetL = doc.current_tier === 'bronze' ? 'silver' : 'gold';
                  try {
                    await mockMutate('POST', `/api/pipeline/ingestion/promote/${doc.data_id}/${targetL}`);
                    const nextStatus = targetL === 'silver' ? 'CHUNKING' : 'GRAPH_EXTRACTING';
                    updateDocument(doc.data_id, { status: nextStatus });
                    setSelectedAsset(prev => prev ? { ...prev, status: nextStatus } : null);
                    startPromotion(doc.data_id, doc.name, doc.current_tier as 'bronze' | 'silver', targetL);
                  } catch { /* ForbiddenToast handles 403 */ }
                }}
                className="btn-primary"
              >
                Process to {selectedAsset.current_tier === 'bronze' ? 'Silver Data Layer' : 'Gold Data Layer'}
                <ArrowRight className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        }
      >
        {selectedAsset && (
          <AssetDetailWorkspace
            document={selectedAsset}
            activeTab={assetTab}
            readOnly={selectedAssetReadOnly}
          />
        )}
      </DetailDrawer>

      {/* ── Promote wizard drawer ── */}
      <DetailDrawer
        isOpen={showPromoteWizard}
        onClose={() => setShowPromoteWizard(false)}
        title="Promote Knowledge Artifact"
        subtitle="Lifecycle State Transition · Bronze → Silver → Gold"
        icon={Sparkles}
        size="wide"
        persistKey="kb-promote"
      >
        <div className="p-6">
          <p className="text-[#2A2A2A] text-sm">Promotion wizard — select target layer, validate prerequisites, configure embedding refresh, attach approver.</p>
        </div>
      </DetailDrawer>

      {/* ── Header ── */}
      <OperationalHeader
        title="Knowledge Operations"
        subtitle="Global Data Ingestion, Semantic Normalization & Graph Synthesis"
        breadcrumbs={[{ label: 'Knowledge' }, { label: 'Operations' }]}
        status={<StatusBadge status="HEALTHY" size="lg" />}
        actions={null}
      />

      {/* ── Sub-tab bar ── */}
      <div className="sub-tab-bar max-w-full">
        {subTabs.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveSubTab(tab.id as KnowledgeTabId)}
            className={cn('sub-tab', activeSubTab === tab.id && 'active')}
          >
            <tab.icon className="tab-icon w-3.5 h-3.5" />
            <span>{tab.label}</span>
            {tab.count && (
              <span className="px-1.5 py-0.5 rounded-full bg-[#FAD7D7] text-[#9F1D1D] text-[9px] font-bold border border-[#C94A4A]">{tab.count}</span>
            )}
          </button>
        ))}

      </div>

      <div className="pb-20">
        {renderContent()}
      </div>

      <PromotionProgressOverlay queue={promotionQueue} onDismiss={dismissEntry} />
    </div>
  );
};

export default KnowledgeOpsCenter;
