import React, { useState, useEffect, useCallback } from 'react';
import {
  Eye,
  Layers,
  Terminal,
  Database,
  ShieldCheck,
  User,
  FileText,
  Zap,
  ChevronRight,
  Search,
  Download,
  Maximize2,
  Clock,
  Lock,
  Globe,
  Settings,
  Shield,
  FileCode,
  Tag,
  Calendar,
  Layers3,
  Sparkles,
  Bookmark,
  CheckCircle2,
  Table2,
  Save,
  Pencil,
  X,
  RefreshCw,
  Plus,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { cn } from '../../../lib/utils';
import { useAppState } from '../../../AppStateContext';
import { KnowledgeDocument, KnowledgeLayer, PagedResponse } from '../../../types';
import { mockGet, mockMutate } from '../../../lib/mockApi';
import {
  ChunkVersion, Chunk, TableColumn, DocTable,
  WarehouseConfigTable, WarehouseConfigVersion,
} from './inventory.data';

interface AssetDetailWorkspaceProps {
  document: KnowledgeDocument;
  activeTab?: 'PREVIEW' | 'CHUNKS' | 'TABLES' | 'LOGS' | 'TIMELINE' | 'CONFIGS';
  readOnly?: boolean;
  onClose?: () => void;
  onPromote?: (doc: KnowledgeDocument) => void;
}

export const AssetDetailWorkspace = ({
  document,
  activeTab = 'PREVIEW',
  readOnly = false,
}: AssetDetailWorkspaceProps) => {
  const { updateDocument } = useAppState();

  const currentAccessRole = document.metadata.access_role || 'all';

  // ── Chunks state ─────────────────────────────────────────────────
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [chunksPage, setChunksPage] = useState(1);
  const [chunksHasNext, setChunksHasNext] = useState(false);
  const [selectedChunk, setSelectedChunk] = useState<Chunk | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<ChunkVersion | null>(null);
  const [activatingSave, setActivatingSave] = useState(false);
  const [activateSaveStatus, setActivateSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle');

  // ── Tables state ─────────────────────────────────────────────────
  const [tables, setTables] = useState<DocTable[]>([]);
  const [tablesLoading, setTablesLoading] = useState(false);
  const [tablesPage, setTablesPage] = useState(1);
  const [tablesHasNext, setTablesHasNext] = useState(false);
  const [selectedTable, setSelectedTable] = useState<DocTable | null>(null);
  const [editingCell, setEditingCell] = useState<{ rowIndex: number; column: string } | null>(null);
  const [editingValue, setEditingValue] = useState('');
  const [cellSaveStatus, setCellSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');

  // ── Warehouse Configs state ───────────────────────────────────────
  const [configs, setConfigs] = useState<WarehouseConfigVersion[]>([]);
  const [configsLoading, setConfigsLoading] = useState(false);
  const [selectedConfig, setSelectedConfig] = useState<WarehouseConfigVersion | null>(null);
  const [activatingConfig, setActivatingConfig] = useState(false);
  const [configActivateStatus, setConfigActivateStatus] = useState<'idle' | 'saved' | 'error'>('idle');
  const [showNewConfigFlow, setShowNewConfigFlow] = useState(false);
  const [discoveringNewTables, setDiscoveringNewTables] = useState(false);
  const [newConfigTables, setNewConfigTables] = useState<(WarehouseConfigTable & { selected: boolean })[]>([]);
  const [savingNewConfig, setSavingNewConfig] = useState(false);
  const [newConfigSaveStatus, setNewConfigSaveStatus] = useState<'idle' | 'saved' | 'error'>('idle');

  const [chunkPolls, setChunkPolls] = useState(0);
  const CHUNK_POLL_MAX = 20; // ~70s ceiling — chunks are produced async by the worker

  const fetchChunks = useCallback(() => {
    if (document.current_tier === 'bronze') return;
    setChunksLoading(true);
    mockGet<PagedResponse<Chunk> | Chunk[]>(
      `/api/knowledge/documents/${document.data_id}/chunks`,
      { page: chunksPage },
    )
      .then(data => {
        const raw = Array.isArray(data) ? data : (data as PagedResponse<Chunk>).items ?? [];
        const hasN = Array.isArray(data) ? false : (data as PagedResponse<Chunk>).has_next ?? false;
        const result = raw.map(chunk => ({
          ...chunk,
          versions: chunk.versions.map(v => ({
            ...v,
            status: v.status === 'active' ? 'Active' : 'Inactive',
          })),
        }));
        setChunks(result);
        setChunksHasNext(hasN);
        if (chunksPage === 1 && result.length > 0) {
          setSelectedChunk(result[0]);
          setSelectedVersion(result[0].versions[0] ?? null);
        }
      })
      .catch(() => { setChunks([]); setChunksHasNext(false); })
      .finally(() => setChunksLoading(false));
  }, [document.data_id, document.current_tier, chunksPage]);

  // Fetch chunks when tab is CHUNKS, doc changes, or page changes
  useEffect(() => {
    if (activeTab !== 'CHUNKS') return;
    fetchChunks();
  }, [activeTab, fetchChunks]);

  // Chunks are generated asynchronously by the ingestion worker after a Silver/Gold
  // promotion, so they don't exist the instant the tab opens. Poll (capped) until
  // they appear instead of showing an empty list that never updates.
  useEffect(() => {
    if (activeTab !== 'CHUNKS' || document.current_tier === 'bronze') return;
    if (chunks.length > 0 || chunkPolls >= CHUNK_POLL_MAX) return;
    const id = setTimeout(() => { fetchChunks(); setChunkPolls(p => p + 1); }, 3500);
    return () => clearTimeout(id);
  }, [activeTab, document.current_tier, chunks.length, chunkPolls, fetchChunks]);

  // Reset page + poll counter when doc or tab changes
  useEffect(() => { setChunksPage(1); }, [document.data_id]);
  useEffect(() => { setChunkPolls(0); }, [document.data_id, activeTab]);

  // Fetch tables when tab is TABLES, doc changes, or page changes
  useEffect(() => {
    if (activeTab !== 'TABLES' || document.current_tier === 'bronze') return;
    setTablesLoading(true);
    mockGet<PagedResponse<DocTable> | DocTable[]>(
      `/api/knowledge/documents/${document.data_id}/tables`,
      { page: tablesPage },
    )
      .then(data => {
        const raw = Array.isArray(data) ? data : (data as PagedResponse<DocTable>).items ?? [];
        const hasN = Array.isArray(data) ? false : (data as PagedResponse<DocTable>).has_next ?? false;
        setTables(raw);
        setTablesHasNext(hasN);
        if (tablesPage === 1 && raw.length > 0) setSelectedTable(raw[0]);
      })
      .catch(() => { setTables([]); setTablesHasNext(false); })
      .finally(() => setTablesLoading(false));
  }, [activeTab, document.data_id, document.current_tier, tablesPage]);

  // Reset table page when doc changes
  useEffect(() => { setTablesPage(1); }, [document.data_id]);

  // ── History / Timeline state ─────────────────────────────────────
  interface HistoryEntry {
    history_id: string;
    from_tier: string | null;
    to_tier: string;
    transitioned_at: string | null;
    approved_by: string | null;
    notes: string | null;
    pipeline_metadata: Record<string, unknown> | null;
  }
  const [historyEntries, setHistoryEntries] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);

  useEffect(() => {
    if (activeTab !== 'TIMELINE') return;
    setHistoryLoading(true);
    mockGet<HistoryEntry[]>(`/api/knowledge/documents/${document.data_id}/history`)
      .then(data => setHistoryEntries(data ?? []))
      .catch(() => setHistoryEntries([]))
      .finally(() => setHistoryLoading(false));
  }, [activeTab, document.data_id]);

  // ── Ingestion logs (kb_ingestion_logs) — real per-document log stream ──
  interface LogEntry { timestamp: string | null; level: string; message: string }
  interface LogsResponse { status: string; stage: string; updated_at: string | null; logs: LogEntry[] }
  const [logData, setLogData] = useState<LogsResponse | null>(null);
  const [logsLoading, setLogsLoading] = useState(false);

  useEffect(() => {
    if (activeTab !== 'LOGS') return;
    setLogsLoading(true);
    mockGet<LogsResponse>(`/api/pipeline/ingestion/documents/${document.data_id}/logs`)
      .then(data => setLogData(data ?? null))
      .catch(() => setLogData(null))
      .finally(() => setLogsLoading(false));
  }, [activeTab, document.data_id]);

  // Fetch warehouse configs when CONFIGS tab active
  useEffect(() => {
    if (activeTab !== 'CONFIGS') return;
    setConfigsLoading(true);
    mockGet<WarehouseConfigVersion[]>(`/api/knowledge/documents/${document.data_id}/configs`)
      .then(data => {
        const result = data && data.length > 0 ? data : [];
        setConfigs(result);
        if (result.length > 0) setSelectedConfig(result[0]);
      })
      .catch(() => setConfigs([]))
      .finally(() => setConfigsLoading(false));
  }, [activeTab, document.data_id]);

  const handleActivateConfig = async () => {
    if (!selectedConfig) return;
    setActivatingConfig(true);
    try {
      await mockMutate('PATCH', `/api/knowledge/documents/${document.data_id}/configs/${selectedConfig.id}/activate`, {});
      setConfigs(prev => prev.map(c => ({
        ...c, status: c.id === selectedConfig.id ? 'Active' : 'Inactive'
      })));
      setSelectedConfig(prev => prev ? { ...prev, status: 'Active' } : prev);
      setConfigActivateStatus('saved');
    } catch {
      setConfigActivateStatus('error');
    } finally {
      setActivatingConfig(false);
      setTimeout(() => setConfigActivateStatus('idle'), 2500);
    }
  };

  const startNewConfigFlow = () => {
    setShowNewConfigFlow(true);
    setDiscoveringNewTables(true);

    // Collect unique tables from all existing configs (real data from backend).
    const tableMap = new Map<string, WarehouseConfigTable>();
    configs.forEach(cfg => {
      cfg.tables.forEach(t => {
        if (!tableMap.has(t.name)) tableMap.set(t.name, t);
      });
    });

    const source: Omit<WarehouseConfigTable, 'description'>[] = Array.from(tableMap.values());

    setTimeout(() => {
      setDiscoveringNewTables(false);
      setNewConfigTables(source.map(t => ({ ...t, description: '', selected: false })));
    }, 600);
  };

  const toggleNewConfigTable = (name: string) => {
    setNewConfigTables(prev => prev.map(t => t.name === name ? { ...t, selected: !t.selected } : t));
  };

  const setNewConfigTableDesc = (name: string, description: string) => {
    setNewConfigTables(prev => prev.map(t => t.name === name ? { ...t, description } : t));
  };

  const handleSaveNewConfig = async () => {
    const selected = newConfigTables.filter(t => t.selected);
    if (selected.length === 0) return;
    setSavingNewConfig(true);
    const nextVersionNum = configs.length + 1;
    const nextVersionLabel = `v${nextVersionNum}`;
    const connection = selectedConfig?.connection ?? {};
    const tables = selected.map(({ name, schema, rowCount, description }) => ({ name, schema, rowCount, description }));
    try {
      const raw = await mockMutate<WarehouseConfigVersion>(
        'POST',
        `/api/knowledge/documents/${document.data_id}/configs`,
        { version_number: nextVersionLabel, connection, tables },
      );
      // Backend may return a full WarehouseConfigVersion or just {config_id, version_number}.
      // Normalise to a full shape in either case.
      const created: WarehouseConfigVersion = raw.id
        ? { ...raw, version_number: raw.version_number ?? nextVersionLabel }
        : {
            id: (raw as unknown as Record<string, string>).config_id ?? `cfg-${Date.now()}`,
            version_number: nextVersionLabel,
            status: 'Inactive',
            created_at: new Date().toISOString(),
            connection,
            tables,
          };
      setConfigs(prev => [created, ...prev]);
      setSelectedConfig(created);
      setNewConfigSaveStatus('saved');
      setShowNewConfigFlow(false);
      setNewConfigTables([]);
    } catch {
      setNewConfigSaveStatus('error');
    } finally {
      setSavingNewConfig(false);
      setTimeout(() => setNewConfigSaveStatus('idle'), 2500);
    }
  };

  const handleChunkChange = (chunk: Chunk) => {
    setSelectedChunk(chunk);
    setSelectedVersion(chunk.versions[0] ?? null);
    setActivateSaveStatus('idle');
  };

  const handleVersionChange = (ver: ChunkVersion) => {
    setSelectedVersion(ver);
    setActivateSaveStatus('idle');
  };

  const handleActivateVersion = async () => {
    if (!selectedChunk || !selectedVersion) return;
    setActivatingSave(true);
    try {
      await mockMutate(
        'PATCH',
        `/api/knowledge/documents/${document.data_id}/chunks/${selectedChunk.id}/activate`,
        { version_number: selectedVersion.version_number }
      );
      // Update local state
      setChunks(prev => prev.map(c => {
        if (c.id !== selectedChunk.id) return c;
        const updated = {
          ...c,
          versions: c.versions.map(v => ({
            ...v,
            status: v.version_number === selectedVersion.version_number ? 'Active' : 'Inactive'
          }))
        };
        setSelectedChunk(updated);
        setSelectedVersion(updated.versions.find(v => v.version_number === selectedVersion.version_number) ?? null);
        return updated;
      }));
      setActivateSaveStatus('saved');
    } catch {
      setActivateSaveStatus('error');
    } finally {
      setActivatingSave(false);
      setTimeout(() => setActivateSaveStatus('idle'), 2500);
    }
  };

  const handleCellEdit = (rowIndex: number, column: string, currentValue: string | null) => {
    setEditingCell({ rowIndex, column });
    setEditingValue(currentValue ?? '');
    setCellSaveStatus('idle');
  };

  const handleCellSave = async () => {
    if (!editingCell || !selectedTable) return;
    setCellSaveStatus('saving');
    try {
      await mockMutate(
        'PATCH',
        `/api/knowledge/documents/${document.data_id}/tables/${selectedTable.id}/rows/${editingCell.rowIndex}`,
        { column: editingCell.column, value: editingValue }
      );
      setTables(prev => prev.map(t => {
        if (t.id !== selectedTable.id) return t;
        const updatedRows = t.rows.map((row, idx) => {
          if (idx !== editingCell.rowIndex) return row;
          return { ...row, [editingCell.column]: editingValue };
        });
        const updated = { ...t, rows: updatedRows };
        setSelectedTable(updated);
        return updated;
      }));
      setCellSaveStatus('saved');
      setEditingCell(null);
    } catch {
      setCellSaveStatus('error');
    } finally {
      setTimeout(() => setCellSaveStatus('idle'), 2000);
    }
  };

  const handleCellCancel = () => {
    setEditingCell(null);
    setEditingValue('');
    setCellSaveStatus('idle');
  };

  const extension = document.name.split('.').pop() || 'N/A';
  const language = document.metadata.language || 'English';
  const sourceType = document.metadata.doc_type || 'Raw Data';
  const isWarehouse = sourceType.startsWith('Warehouse/');

  const showFileMetadata = activeTab === 'PREVIEW' && !isWarehouse;
  // Silver tables come from Mongo staging (no Postgres row to PATCH) — preview only.
  // Editing becomes available after Gold promotion.
  const tablesReadOnly = readOnly || document.current_tier === 'silver';

  return (
    <div className="h-full flex flex-col bg-[#FCFBF7] text-[#111111]">
      <div className="flex-1 flex overflow-hidden">
         {/* LEFT PANEL: Metadata sidebar (PREVIEW tab only) */}
         {showFileMetadata && (
            <motion.div
              style={{ width: 320 }}
              className="border-r border-[#BFA66A]/30 p-8 overflow-y-auto custom-scrollbar flex flex-col gap-8 bg-[#FFFDF8] shrink-0"
            >
               <section className="space-y-6">
                  <h4 className="text-[10px] font-black text-[#8A5A00] uppercase tracking-widest border-b border-[#BFA66A]/20 pb-2 flex items-center gap-1.5 font-display">
                     <Bookmark className="w-3.5 h-3.5 text-[#B88719]" />
                     Display Metadata
                  </h4>

                  <div className="space-y-4">
                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Name</span>
                        <span className="text-xs text-[#111111] font-mono break-all bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">{document.name}</span>
                     </div>

                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Extension</span>
                        <span className="text-xs text-[#111111] font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)] uppercase">{extension}</span>
                     </div>

                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Language</span>
                        <span className="text-xs text-[#8A5A00] font-semibold bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">{language}</span>
                     </div>

                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Source Type</span>
                        <span className="text-xs text-amber-900 font-semibold bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">{sourceType}</span>
                     </div>

                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Added On</span>
                        <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">{document.added_on}</span>
                     </div>

                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Added By</span>
                        <div className="text-xs text-[#111111] font-medium bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)] flex items-center gap-2">
                          <span className="w-5 h-5 rounded-full bg-[#B88719]/10 text-[#8A5A00] flex items-center justify-center text-[9px] font-black uppercase">{(document.metadata?.author ?? document.added_by ?? '')[0]}</span>
                          {(document.metadata?.author ?? document.added_by ?? '')}
                        </div>
                     </div>
                  </div>
               </section>

               <section className="space-y-4">
                 <h4 className="text-[10px] font-black text-[#8A5A00] uppercase tracking-widest border-b border-[#BFA66A]/20 pb-2 flex items-center gap-1.5 font-display">
                    <Sparkles className="w-3.5 h-3.5 text-[#B88719]" />
                    Properties
                 </h4>
                 <div className="space-y-4">
                   {(extension.toLowerCase() === 'pdf' || extension.toLowerCase() === 'docx' || sourceType.toLowerCase() === 'markdown') && (
                     <>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Original Author</span>
                          <span className="text-xs text-[#111111] font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.author || 'Anonymous'}
                          </span>
                       </div>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Published Date</span>
                          <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.published_date || '2026-01-10'}
                          </span>
                       </div>
                     </>
                   )}

                   {(sourceType.toLowerCase().includes('web') || document.metadata.url) && (
                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Target Endpoint URL</span>
                        <span className="text-xs text-[#B88719] font-mono underline break-all bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                           {document.metadata.url || 'https://docs.aeroflow.ai/index'}
                        </span>
                     </div>
                   )}

                   {(sourceType.toLowerCase().includes('image') || extension.toLowerCase() === 'png' || extension.toLowerCase() === 'jpg') && (
                     <div className="space-y-4">
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Resolution</span>
                          <span className="text-xs text-[#111111] font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.width || '1920px'} x {document.metadata.height || '1080px'}
                          </span>
                       </div>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Color Space</span>
                          <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.color_space || 'sRGB (Display P3)'}
                          </span>
                       </div>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">File Size</span>
                          <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.file_size || '3.4 MB'}
                          </span>
                       </div>
                     </div>
                   )}

                   {(sourceType.toLowerCase().includes('video') || extension.toLowerCase() === 'mp4') && (
                     <div className="space-y-4">
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Aspect Resolution</span>
                          <span className="text-xs text-[#111111] font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.width || '3840px'} x {document.metadata.height || '2160p (4K)'}
                          </span>
                       </div>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Extraction Frames</span>
                          <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.total_frame || '24,800 frames'}
                          </span>
                       </div>
                       <div className="flex flex-col">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Storage Allocation</span>
                          <span className="text-xs text-slate-600 font-mono bg-white border border-[#BFA66A]/20 px-3 py-2 rounded-xl shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                             {document.metadata.file_size || '52.4 MB'}
                          </span>
                       </div>
                     </div>
                   )}

                   {/* Document-level intents (content-purpose labels, set at Gold). */}
                   {(document.metadata.intents?.length ?? 0) > 0 && (
                     <div className="flex flex-col">
                        <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-1.5">Document Intents</span>
                        <div className="flex flex-wrap gap-1.5">
                           {document.metadata.intents!.map((it, i) => (
                              <span key={`${it}-${i}`} className="text-[10px] font-mono text-[#8A5A00] bg-white border border-[#BFA66A]/25 px-2.5 py-1 rounded-lg shadow-[0_1px_2px_rgba(0,0,0,0.02)]">
                                 {it}
                              </span>
                           ))}
                        </div>
                     </div>
                   )}
                 </div>
               </section>

               <section className="space-y-4 pt-6 border-t border-[#BFA66A]/20">
                  {document.current_tier === 'bronze' ? (
                    <div className="flex flex-col">
                       <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                         <Lock className="w-3.5 h-3.5 text-[#B88719]" />
                         Access Role
                       </span>
                       <div className="relative">
                          <select
                            value={currentAccessRole}
                            onChange={(e) => {
                              updateDocument(document.data_id, {
                                metadata: { access_role: e.target.value }
                              });
                            }}
                            className="w-full bg-white border border-[#BFA66A]/40 hover:border-[#B88719]/60 rounded-xl px-3.5 py-2.5 text-xs text-[#111111] focus:outline-none focus:ring-1 focus:ring-[#B88719] appearance-none font-mono cursor-pointer shadow-xs"
                          >
                             <option value="all">all</option>
                             <option value="admin">admin</option>
                             <option value="kb_editor">kb_editor</option>
                             <option value="user">user</option>
                          </select>
                          <span className="absolute right-3.5 top-1/2 -translate-y-1/2 pointer-events-none text-slate-500 text-[9px]">▼</span>
                       </div>
                       <span className="text-[9px] text-slate-500 mt-1.5 leading-normal">Assign granular access clearances only during Raw Bronze phase.</span>
                    </div>
                  ) : (
                    <div className="flex flex-col">
                       <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest mb-2 flex items-center gap-1.5">
                         <Lock className="w-3.5 h-3.5 text-slate-400" />
                         Access Role (LOCKED)
                       </span>
                       <div className="w-full bg-[#FCFBF7] border border-[#BFA66A]/20 rounded-xl px-3.5 py-2.5 text-xs font-mono text-slate-500 flex items-center justify-between">
                          <span>{currentAccessRole}</span>
                          <span className="text-[8px] bg-slate-500/10 text-slate-500 border border-slate-500/20 rounded px-1.5 font-bold">READONLY</span>
                       </div>
                       <span className="text-[9px] text-slate-500 mt-1.5 leading-normal">Retriever permissions of vectorized stages must remain strictly immutable.</span>
                    </div>
                  )}
               </section>
            </motion.div>
         )}

         {/* RIGHT PANEL: Dynamic View Area */}
         <div className="flex-1 overflow-hidden p-8 flex flex-col bg-[#FCFBF7]">
            <AnimatePresence mode="wait">

                  {/* ─── PREVIEW TAB ─────────────────────────────────────────── */}
                  {activeTab === 'PREVIEW' && (
                    <motion.div
                      key="preview"
                      initial={false}
                      animate={false}
                      className="h-full border border-[#BFA66A]/30 rounded-[2rem] bg-white overflow-hidden flex flex-col shadow-sm"
                    >
                       <div className="h-12 bg-[#FFFDF8] border-b border-[#BFA66A]/20 flex items-center justify-between px-6 shrink-0">
                          <div className="flex items-center gap-2 text-[10px] font-black text-[#5A4209]/70 tracking-wider">
                             <span>{document.name}</span>
                             <ChevronRight className="w-3.5 h-3.5 text-slate-400" />
                             <span>PREVIEW MODE</span>
                          </div>
                          <div className="flex items-center gap-2">
                             <button className="p-1.5 hover:bg-slate-100 rounded-lg"><Search className="w-4 h-4 text-slate-500" /></button>
                             <button className="p-1.5 hover:bg-slate-100 rounded-lg"><Download className="w-4 h-4 text-slate-500" /></button>
                             <button className="p-1.5 hover:bg-slate-100 rounded-lg"><Maximize2 className="w-4 h-4 text-slate-500" /></button>
                          </div>
                       </div>
                       <div className="flex-1 p-10 overflow-y-auto custom-scrollbar flex justify-center bg-[#FCFBF7]/50">
                          {document.current_tier === 'bronze' ? (
                             <div className="max-w-2xl w-full bg-[#FFFFFF] rounded-3xl shadow-[0_8px_30px_rgb(0,0,0,0.03)] p-12 text-slate-800 font-serif leading-relaxed min-h-[500px] border border-[#BFA66A]/30">
                                <h1 className="text-2xl font-bold mb-8 border-b pb-4 border-slate-100 text-[#111111]">{document.name}</h1>
                                <p className="mb-6 text-sm text-[#2A2A2A]">This structural pipeline resource serves as the direct source of truth for regional data ingestion. Raw strings and properties are cataloged on local, low-latency cold-storage sectors prior to chunking and token vectorizations.</p>
                                <div className="h-40 bg-[#FFFDF8] rounded-2xl border border-dashed border-[#BFA66A] flex flex-col items-center justify-center text-[#8A5A00] p-6 mb-8 text-center shadow-xs">
                                   <Database className="w-6 h-6 text-[#B88719] mb-2.5 animate-pulse" />
                                   <span className="text-xs font-mono font-black uppercase tracking-widest">[BRONZE STAGE RAW INPUT REPOSITORY]</span>
                                   <span className="text-[10px] text-slate-600 mt-1.5">Raw stream parsed with 100% security clearance protocols matched</span>
                                </div>
                                <p className="mb-6 text-sm text-[#2A2A2A]">Future promotions to the Silver layer will automatically segment raw blocks into discrete semantic chunks, stripping metadata noise and scrubbing properties for candidate PII leaks.</p>
                             </div>
                          ) : document.current_tier === 'silver' ? (
                             <div className="max-w-2xl w-full space-y-6">
                                <div className="p-5 bg-[#FFF9E8] border border-[#BFA66A]/45 rounded-2xl text-[10px] font-black text-[#8A5A00] uppercase tracking-widest flex items-center justify-center gap-3 shadow-xs">
                                   <Zap className="w-4 h-4 text-[#B88719]" /> AI-Assisted Normalization Active
                                </div>
                                <div className="font-mono text-[11px] text-[#2A2A2A] p-8 bg-white rounded-3xl border border-[#BFA66A]/30 leading-relaxed shadow-sm">
                                   <span className="text-[#8A5A00] font-bold"># Normalized Segment: {document.name}</span><br/><br/>
                                   <span className="text-[#5D4037] font-semibold">## Extraction Metrics</span><br/>
                                   - Ingestion Status: Normalization verified.<br/>
                                   - Candidate tokens parsed: 4,120 successfully sanitized.<br/>
                                   - Document parsing converted to Markdown specs.<br/><br/>
                                   <span className="text-[#5D4037] font-semibold">## Security Scrubber</span><br/>
                                   - Enterprise scope tenant validation: GlobalCorp OK.<br/>
                                   - Automatic PII filter sweep: STAGE COMPLETED.<br/>
                                   - Cross-tenant contamination checks: IMMUNE.
                                </div>
                             </div>
                          ) : (
                             <div className="w-full flex flex-col gap-6 items-center justify-center h-full max-w-sm mx-auto text-center">
                                <div className="w-20 h-20 rounded-full bg-[#FFF9E8] border border-[#BFA66A] flex items-center justify-center shadow-lg shadow-[#B88719]/5 animate-pulse">
                                   <Zap className="w-8 h-8 text-[#B88719]" />
                                </div>
                                <div>
                                   <h3 className="text-base font-bold text-[#111111] mb-2">Vectorized Gold Retrieval Node</h3>
                                   <p className="text-xs text-slate-600 leading-relaxed">This physical knowledge asset resides securely in the Active production GOLD layer. Fully index-ready for prompt semantic retrieval pipelines.</p>
                                </div>
                             </div>
                          )}
                       </div>
                    </motion.div>
                  )}

                  {/* ─── CHUNKS TAB — SILVER ─────────────────────────────────── */}
                  {activeTab === 'CHUNKS' && document.current_tier === 'silver' && (
                    <motion.div
                      key="chunks-silver"
                      initial={false}
                      animate={false}
                      className="h-full flex gap-6 overflow-hidden"
                    >
                       <div className="w-80 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                          <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                             <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-2">
                                <Layers3 className="w-4 h-4 text-[#B88719]" />
                                List of Chunks
                             </h4>
                             <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]/30">{chunks.length} Chunks</span>
                          </div>
                          <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar bg-[#FCFBF7]/30">
                             {chunksLoading ? (
                               <div className="flex items-center justify-center py-12">
                                 <span className="w-5 h-5 rounded-full border-2 border-[#BFA66A]/40 border-t-[#B88719] animate-spin" />
                               </div>
                             ) : chunks.length === 0 ? (
                               <div className="text-center py-12 text-xs text-slate-400 font-mono">No chunks yet.<br/>{chunkPolls < CHUNK_POLL_MAX ? 'The ingestion worker may still be processing — auto-refreshing…' : 'None were produced for this document.'}</div>
                             ) : chunks.map((chunk) => (
                                <div
                                  key={chunk.id}
                                  onClick={() => handleChunkChange(chunk)}
                                  className={cn(
                                    "p-4 border rounded-xl transition-all cursor-pointer group text-left",
                                    chunk.id === selectedChunk?.id
                                      ? "border-[#B88719] bg-[#FFF9E8] shadow-xs"
                                      : "border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]"
                                  )}
                                >
                                   <div className="flex justify-between items-center mb-1.5 text-[10px] font-mono">
                                      <span className={chunk.id === selectedChunk?.id ? "text-[#8A5A00] font-black" : "text-slate-500"}>
                                        {chunk.id}
                                      </span>
                                      <span className="text-[9px] text-[#5A4209]/60 font-semibold">LENGTH: {chunk.text.length}</span>
                                   </div>
                                   <div className="text-xs text-[#111111] font-bold truncate group-hover:text-[#B88719] transition-colors uppercase tracking-tight">{chunk.title}</div>
                                   <p className="text-[11px] text-slate-600 truncate mt-1.5">{chunk.text}</p>
                                </div>
                             ))}
                          </div>
                          {(chunksPage > 1 || chunksHasNext) && (
                            <div className="flex items-center justify-between px-3 py-2 border-t border-[#BFA66A]/15 bg-[#FFFDF8]">
                              <span className="text-[10px] font-mono text-slate-400">Pg {chunksPage}</span>
                              <div className="flex gap-1">
                                <button disabled={chunksPage === 1} onClick={() => setChunksPage(p => Math.max(1, p - 1))}
                                  className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", chunksPage === 1 && "opacity-40 cursor-not-allowed")}>
                                  ‹
                                </button>
                                <button disabled={!chunksHasNext} onClick={() => setChunksPage(p => p + 1)}
                                  className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !chunksHasNext && "opacity-40 cursor-not-allowed")}>
                                  ›
                                </button>
                              </div>
                            </div>
                          )}
                       </div>

                       <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl p-6 bg-white flex flex-col overflow-hidden shadow-xs">
                          {selectedChunk ? (
                            <>
                              <div className="p-4 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8] rounded-2xl shrink-0">
                                 <div>
                                    <span className="text-[11px] font-mono text-[#8A5A00] font-bold uppercase">{selectedChunk.id}</span>
                                    <h4 className="text-sm font-bold text-[#111111] mt-0.5">{selectedChunk.title}</h4>
                                 </div>
                                 <span className="text-[9px] font-black uppercase text-[#8A5A00] bg-[#FFF9E8] border border-[#BFA66A]/30 px-3 py-1 rounded-lg">SILVER NORMALIZED</span>
                              </div>
                              <div className="flex-1 p-6 overflow-y-auto custom-scrollbar mt-4 bg-[#FCFBF7]/30 rounded-2xl border border-dashed border-[#BFA66A]/20">
                                 <div className="p-6 bg-white border border-[#BFA66A]/20 rounded-xl shadow-xs">
                                    <span className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest block mb-4 border-b border-[#BFA66A]/10 pb-2">RAW PARSED STRING</span>
                                    <p className="text-slate-700 text-xs leading-relaxed font-mono whitespace-pre-wrap">{selectedChunk.text}</p>
                                 </div>
                              </div>
                            </>
                          ) : (
                            <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono">Select a chunk to inspect</div>
                          )}
                       </div>
                    </motion.div>
                  )}

                  {/* ─── CHUNKS TAB — GOLD ───────────────────────────────────── */}
                  {activeTab === 'CHUNKS' && document.current_tier === 'gold' && (
                    <motion.div
                      key="chunks-gold"
                      initial={false}
                      animate={false}
                      className="h-full flex gap-6 overflow-hidden"
                    >
                       <div className="w-80 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                          <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                             <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-1.5">
                                <Layers3 className="w-4 h-4 text-[#B88719]" />
                                Active Chunks
                             </h4>
                             <span className="text-[10px] font-mono text-[#8A5A00] font-black bg-[#FFF9E8] border border-[#BFA66A]/30 px-2 py-0.5 rounded">{chunks.length} Chunks</span>
                          </div>
                          <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar bg-[#FCFBF7]/30">
                             {chunksLoading ? (
                               <div className="flex items-center justify-center py-12">
                                 <span className="w-5 h-5 rounded-full border-2 border-[#BFA66A]/40 border-t-[#B88719] animate-spin" />
                               </div>
                             ) : chunks.length === 0 ? (
                               <div className="text-center py-12 text-xs text-slate-400 font-mono">No chunks yet.<br/>{chunkPolls < CHUNK_POLL_MAX ? 'The ingestion worker may still be processing — auto-refreshing…' : 'None were produced for this document.'}</div>
                             ) : chunks.map((chunk) => (
                                <div
                                  key={chunk.id}
                                  onClick={() => handleChunkChange(chunk)}
                                  className={cn(
                                    "p-4 border rounded-xl transition-all cursor-pointer group text-left",
                                    chunk.id === selectedChunk?.id
                                      ? "border-[#B88719] bg-[#FFF9E8] shadow-xs"
                                      : "border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]"
                                  )}
                                >
                                   <div className="flex justify-between items-center mb-1 text-[10px] font-mono">
                                      <span className={chunk.id === selectedChunk?.id ? "text-[#8A5A00] font-black animate-pulse" : "text-slate-500"}>
                                        {chunk.id}
                                      </span>
                                   </div>
                                   <div className="text-xs text-[#111111] font-bold truncate group-hover:text-[#B88719] transition-colors uppercase tracking-tight">{chunk.title}</div>
                                   <p className="text-[10px] text-slate-600 truncate mt-1">{chunk.text}</p>
                                </div>
                             ))}
                          </div>
                          {(chunksPage > 1 || chunksHasNext) && (
                            <div className="flex items-center justify-between px-3 py-2 border-t border-[#BFA66A]/15 bg-[#FFFDF8]">
                              <span className="text-[10px] font-mono text-slate-400">Pg {chunksPage}</span>
                              <div className="flex gap-1">
                                <button disabled={chunksPage === 1} onClick={() => setChunksPage(p => Math.max(1, p - 1))}
                                  className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", chunksPage === 1 && "opacity-40 cursor-not-allowed")}>
                                  ‹
                                </button>
                                <button disabled={!chunksHasNext} onClick={() => setChunksPage(p => p + 1)}
                                  className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !chunksHasNext && "opacity-40 cursor-not-allowed")}>
                                  ›
                                </button>
                              </div>
                            </div>
                          )}
                       </div>

                       <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl p-6 bg-white flex flex-col overflow-hidden shadow-xs">
                          {!selectedChunk || !selectedVersion ? (
                            <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono">Select a chunk to inspect</div>
                          ) : (
                            <>
                              <div className="pb-4 border-b border-[#BFA66A]/20 shrink-0 flex justify-between items-center">
                                 <div>
                                    <span className="text-[10px] font-mono text-[#8A5A00] font-black">{selectedChunk.id} · Semantic Node</span>
                                    <h3 className="text-base font-bold text-[#111111] font-display">{selectedChunk.title}</h3>
                                 </div>
                                 <div className="flex items-center gap-2">
                                    {activateSaveStatus === 'saved' && (
                                      <span className="text-[10px] font-bold text-green-700 flex items-center gap-1">
                                        <CheckCircle2 className="w-3.5 h-3.5" /> Saved
                                      </span>
                                    )}
                                    {activateSaveStatus === 'error' && (
                                      <span className="text-[10px] font-bold text-red-600">Save failed</span>
                                    )}
                                    {!readOnly && selectedVersion.status !== 'Active' && (
                                      <button
                                        onClick={handleActivateVersion}
                                        disabled={activatingSave}
                                        className={cn(
                                          "px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all flex items-center gap-1.5 cursor-pointer",
                                          activatingSave
                                            ? "bg-slate-100 text-slate-400 cursor-not-allowed"
                                            : "bg-[#B88719] text-white hover:bg-[#8A5A00] shadow-md"
                                        )}
                                      >
                                        {activatingSave ? (
                                          <><RefreshCw className="w-3 h-3 animate-spin" /> Saving...</>
                                        ) : (
                                          <><Save className="w-3 h-3" /> Activate Version</>
                                        )}
                                      </button>
                                    )}
                                    <span className="text-[9px] font-black uppercase text-[#B88719] bg-[#FFF9E9] border border-[#BFA66A]/30 px-2.5 py-1 rounded-lg">Multi-Version Gold Layer</span>
                                 </div>
                              </div>

                              <div className="py-4 border-b border-slate-100 flex flex-col gap-2 shrink-0 bg-[#FFFDF8]/40 px-3 rounded-2xl mt-4">
                                 <div className="flex justify-between items-center mb-1">
                                    <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest flex items-center gap-1.5">
                                       <Clock className="w-3 h-3 text-[#B88719]" />
                                       Select Active Revision Node
                                    </span>
                                    <span className="text-[8px] font-mono text-slate-400 font-black tracking-widest uppercase sm:block hidden">Swipe for more →</span>
                                 </div>
                                 <div className="flex gap-3 overflow-x-auto pb-1.5 custom-scrollbar flex-nowrap">
                                    {selectedChunk.versions.map((ver) => (
                                       <button
                                         key={ver.version_number}
                                         onClick={() => handleVersionChange(ver)}
                                         className={cn(
                                           "px-4 py-2.5 rounded-xl border transition-all cursor-pointer text-left flex items-center gap-3 shrink-0 min-w-[130px] shadow-2xs group",
                                           ver.version_number === selectedVersion.version_number
                                             ? "border-[#B88719] bg-[#FFF9E8] text-[#111111]"
                                             : "border-[#BFA66A]/25 bg-white text-slate-700 hover:border-[#BFA66A]/50 hover:bg-[#FCFBF7]"
                                         )}
                                       >
                                          <div className={cn(
                                             "w-6 h-6 rounded-lg flex items-center justify-center border",
                                             ver.version_number === selectedVersion.version_number
                                               ? "bg-[#B88719]/10 border-[#B88719]/20 text-[#8A5A00]"
                                               : "bg-slate-50 border-slate-200 text-slate-400 group-hover:bg-[#FFF9E8]/30"
                                          )}>
                                             <Clock className="w-3.5 h-3.5" />
                                          </div>
                                          <div>
                                             <div className="text-xs font-mono font-black">{ver.version_number}</div>
                                             <span className={cn(
                                               "text-[8px] font-black uppercase px-1 rounded-sm tracking-wide mt-0.5 inline-block",
                                               ver.status === 'Active' ? "text-green-800 bg-green-500/10" : "text-slate-500 bg-slate-100"
                                             )}>
                                                {ver.status}
                                             </span>
                                          </div>
                                       </button>
                                    ))}
                                 </div>
                              </div>

                              <div className="flex-1 overflow-y-auto p-2 space-y-6 custom-scrollbar mt-4">
                                 <div className="bg-[#FFFDF8]/70 border border-[#BFA66A]/30 p-5 rounded-2xl shadow-2xs">
                                    <h4 className="text-[9px] font-black text-[#5A4209]/80 uppercase tracking-widest flex items-center gap-1.5 border-b border-[#BFA66A]/20 pb-2.5 mb-4">
                                       <Tag className="w-3.5 h-3.5 text-[#B88719]" />
                                       Revision Parameters Matrix
                                    </h4>

                                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                                       <div className="flex flex-col">
                                          <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Version Name</span>
                                          <span className="text-xs text-[#111111] font-mono font-black">{selectedVersion.version_number}</span>
                                       </div>

                                       <div className="flex flex-col">
                                          <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Time Registered</span>
                                          <span className="text-xs text-slate-600 font-mono italic">{selectedVersion.create_at}</span>
                                       </div>

                                       <div className="flex flex-col">
                                          <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Retriever Status</span>
                                          <div className="flex items-center gap-1.5 mt-0.5">
                                             <span className={cn("w-1.5 h-1.5 rounded-full", selectedVersion.status === 'Active' ? "bg-green-600" : "bg-slate-400")} />
                                             <span className={cn(
                                               "text-xs font-mono font-black",
                                               selectedVersion.status === 'Active' ? "text-green-700" : "text-slate-500"
                                             )}>{selectedVersion.status === 'Active' ? 'ACTIVE RETRIEVER' : 'STAGED RECORD'}</span>
                                          </div>
                                       </div>

                                       <div className="flex flex-col">
                                          <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Embedding Model Configuration</span>
                                          <span className="text-xs text-slate-600 font-mono truncate">{selectedVersion.embedding_models}</span>
                                       </div>

                                       <div className="flex flex-col md:col-span-2 lg:col-span-3">
                                          <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-2 flex justify-between items-center">
                                             <span>Identified Entities Map</span>
                                             <span className="text-[8px] font-mono text-slate-400 tracking-wider normal-case">(scroll horizontally)</span>
                                          </span>
                                          <div className="flex gap-2 overflow-x-auto pb-2 scrollbar-thin flex-nowrap max-w-full">
                                             {selectedVersion.entities.map((ent, idx) => (
                                               <span key={`${ent}-${idx}`} className="bg-white border border-[#BFA66A]/25 text-[#5A4209] text-[11px] font-mono px-3 py-1.5 rounded-xl shadow-2xs shrink-0 flex items-center hover:bg-[#FFF9E8]/40 transition-colors">
                                                 {ent}
                                               </span>
                                             ))}
                                          </div>
                                       </div>

                                    </div>
                                 </div>

                                 <div className="p-6 bg-white border border-[#BFA66A]/30 rounded-2xl shadow-xs">
                                    <span className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest block mb-3 border-b border-dashed border-[#BFA66A]/20 pb-1.5">
                                       Version Text Data
                                    </span>
                                    <p className="text-[#2A2A2A] text-xs leading-relaxed font-mono whitespace-pre-wrap">{selectedVersion.text}</p>
                                 </div>
                              </div>
                            </>
                          )}
                       </div>
                    </motion.div>
                  )}

                  {/* ─── TABLES TAB ───────────────────────────────────────────── */}
                  {activeTab === 'TABLES' && (document.current_tier === 'silver' || document.current_tier === 'gold') && (
                    <motion.div
                      key="tables"
                      initial={false}
                      animate={false}
                      className="h-full flex gap-6 overflow-hidden"
                    >
                      {/* Left: table list */}
                      <div className="w-80 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                        <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                          <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-2">
                            <Table2 className="w-4 h-4 text-[#B88719]" />
                            List of Tables
                          </h4>
                          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]/30">
                            {tables.length} Tables
                          </span>
                        </div>
                        <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar bg-[#FCFBF7]/30">
                          {tablesLoading ? (
                            <div className="flex items-center justify-center py-12">
                              <span className="w-5 h-5 rounded-full border-2 border-[#BFA66A]/40 border-t-[#B88719] animate-spin" />
                            </div>
                          ) : tables.length === 0 ? (
                            <div className="text-center py-12 text-xs text-slate-400 font-mono">No tables available.<br/>Start the mock server.</div>
                          ) : tables.map((table) => (
                            <div
                              key={table.id}
                              onClick={() => setSelectedTable(table)}
                              className={cn(
                                "p-4 border rounded-xl transition-all cursor-pointer group text-left",
                                table.id === selectedTable?.id
                                  ? "border-[#B88719] bg-[#FFF9E8] shadow-xs"
                                  : "border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]"
                              )}
                            >
                              <div className="flex justify-between items-center mb-1.5 text-[10px] font-mono">
                                <span className={table.id === selectedTable?.id ? "text-[#8A5A00] font-black" : "text-slate-500"}>
                                  {table.id}
                                </span>
                                <span className="text-[9px] text-[#5A4209]/60 font-semibold">{table.columns.length} cols · {table.rows.length} rows</span>
                              </div>
                              <div className="text-xs text-[#111111] font-bold truncate group-hover:text-[#B88719] transition-colors font-mono">{table.name}</div>
                              <p className="text-[11px] text-slate-500 truncate mt-1">{table.description}</p>
                            </div>
                          ))}
                        </div>
                        {(tablesPage > 1 || tablesHasNext) && (
                          <div className="flex items-center justify-between px-3 py-2 border-t border-[#BFA66A]/15 bg-[#FFFDF8]">
                            <span className="text-[10px] font-mono text-slate-400">Pg {tablesPage}</span>
                            <div className="flex gap-1">
                              <button disabled={tablesPage === 1} onClick={() => setTablesPage(p => Math.max(1, p - 1))}
                                className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", tablesPage === 1 && "opacity-40 cursor-not-allowed")}>
                                ‹
                              </button>
                              <button disabled={!tablesHasNext} onClick={() => setTablesPage(p => p + 1)}
                                className={cn("px-2 py-1 rounded text-[10px] font-bold border border-[#BFA66A]/20 bg-white text-[#8A5A00]", !tablesHasNext && "opacity-40 cursor-not-allowed")}>
                                ›
                              </button>
                            </div>
                          </div>
                        )}
                      </div>

                      {/* Right: selected table detail */}
                      <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl bg-white flex flex-col overflow-hidden shadow-xs">
                        {!selectedTable ? (
                          <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono">Select a table to inspect</div>
                        ) : (
                          <>
                            {/* Table header */}
                            <div className="p-5 border-b border-[#BFA66A]/20 bg-[#FFFDF8] shrink-0 flex justify-between items-start">
                              <div>
                                <span className="text-[10px] font-mono text-[#8A5A00] font-black uppercase">{selectedTable.id}</span>
                                <h3 className="text-sm font-bold text-[#111111] font-mono">{selectedTable.name}</h3>
                                <p className="text-[11px] text-slate-500 mt-0.5">{selectedTable.description}</p>
                              </div>
                              <div className="flex items-center gap-2 shrink-0">
                                {cellSaveStatus === 'saved' && (
                                  <span className="text-[10px] font-bold text-green-700 flex items-center gap-1">
                                    <CheckCircle2 className="w-3.5 h-3.5" /> Saved
                                  </span>
                                )}
                                {cellSaveStatus === 'error' && (
                                  <span className="text-[10px] font-bold text-red-600">Save failed</span>
                                )}
                                <span className="text-[9px] font-black uppercase text-[#B88719] bg-[#FFF9E9] border border-[#BFA66A]/30 px-2.5 py-1 rounded-lg">
                                  {document.current_tier} EXTRACTED TABLE
                                </span>
                              </div>
                            </div>

                            {/* Column schema */}
                            <div className="p-4 border-b border-[#BFA66A]/15 bg-[#FFFDF8]/60 shrink-0">
                              <span className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest block mb-3">Column Schema</span>
                              <div className="flex gap-2 overflow-x-auto pb-1 flex-nowrap custom-scrollbar">
                                {selectedTable.columns.map((col) => (
                                  <div key={col.name} className="shrink-0 bg-white border border-[#BFA66A]/25 rounded-xl px-3 py-2 min-w-[120px]">
                                    <div className="text-xs font-mono font-black text-[#111111]">{col.name}</div>
                                    <div className="text-[9px] font-mono text-[#8A5A00] mt-0.5">{col.type}</div>
                                    <div className={cn(
                                      "text-[8px] font-bold uppercase mt-1 inline-block px-1.5 py-0.5 rounded",
                                      col.nullable
                                        ? "bg-slate-100 text-slate-500"
                                        : "bg-amber-500/10 text-amber-700"
                                    )}>
                                      {col.nullable ? 'nullable' : 'required'}
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </div>

                            {/* Data rows — editable table */}
                            <div className="flex-1 overflow-auto custom-scrollbar">
                              <table className="w-full text-left border-collapse min-w-max">
                                <thead className="sticky top-0 z-10">
                                  <tr className="bg-[#FFFDF8] border-b border-[#BFA66A]/20">
                                    <th className="px-4 py-3 text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest w-12 text-center">#</th>
                                    {selectedTable.columns.map((col) => (
                                      <th key={col.name} className="px-4 py-3 text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest whitespace-nowrap">
                                        {col.name}
                                        <span className="ml-1.5 text-[8px] font-mono text-slate-400 normal-case font-normal">{col.type}</span>
                                      </th>
                                    ))}
                                    {!tablesReadOnly && <th className="px-4 py-3 text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest w-16 text-center">Edit</th>}
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-[#BFA66A]/10">
                                  {selectedTable.rows.map((row, rowIdx) => (
                                    <tr key={rowIdx} className="hover:bg-[#FFF9E8]/30 transition-colors group">
                                      <td className="px-4 py-3 text-[10px] font-mono text-slate-400 text-center">{rowIdx + 1}</td>
                                      {selectedTable.columns.map((col) => {
                                        const isEditing = editingCell?.rowIndex === rowIdx && editingCell?.column === col.name;
                                        const cellVal = row[col.name];
                                        return (
                                          <td key={col.name} className="px-4 py-3 text-xs font-mono text-[#2A2A2A]">
                                            {isEditing ? (
                                              <div className="flex items-center gap-1.5">
                                                <input
                                                  autoFocus
                                                  value={editingValue}
                                                  onChange={(e) => setEditingValue(e.target.value)}
                                                  onKeyDown={(e) => {
                                                    if (e.key === 'Enter') handleCellSave();
                                                    if (e.key === 'Escape') handleCellCancel();
                                                  }}
                                                  className="flex-1 min-w-[80px] bg-white border border-[#B88719] rounded-lg px-2 py-1 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#B88719]"
                                                />
                                                <button
                                                  onClick={handleCellSave}
                                                  disabled={cellSaveStatus === 'saving'}
                                                  className="p-1 bg-[#B88719] text-white rounded-lg hover:bg-[#8A5A00] transition-colors cursor-pointer"
                                                  title="Save"
                                                >
                                                  {cellSaveStatus === 'saving'
                                                    ? <RefreshCw className="w-3 h-3 animate-spin" />
                                                    : <Save className="w-3 h-3" />
                                                  }
                                                </button>
                                                <button
                                                  onClick={handleCellCancel}
                                                  className="p-1 bg-slate-100 text-slate-500 rounded-lg hover:bg-slate-200 transition-colors cursor-pointer"
                                                  title="Cancel"
                                                >
                                                  <X className="w-3 h-3" />
                                                </button>
                                              </div>
                                            ) : (
                                              <span
                                                onClick={() => !tablesReadOnly && handleCellEdit(rowIdx, col.name, cellVal)}
                                                className={cn(
                                                  "px-1.5 py-0.5 rounded transition-colors block",
                                                  tablesReadOnly ? "cursor-default" : "cursor-text hover:bg-[#FFF9E8]"
                                                )}
                                                title={tablesReadOnly ? undefined : "Click to edit"}
                                              >
                                                {cellVal === null || cellVal === undefined ? (
                                                  <span className="text-slate-300 italic text-[10px]">null</span>
                                                ) : String(cellVal)}
                                              </span>
                                            )}
                                          </td>
                                        );
                                      })}
                                      {!tablesReadOnly && (
                                        <td className="px-4 py-3 text-center">
                                          <button
                                            onClick={() => {
                                              const firstCol = selectedTable.columns[0];
                                              if (firstCol) handleCellEdit(rowIdx, firstCol.name, row[firstCol.name]);
                                            }}
                                            className="opacity-0 group-hover:opacity-100 p-1 bg-[#FFF9E8] border border-[#BFA66A]/30 text-[#8A5A00] rounded-lg hover:bg-[#B88719] hover:text-white transition-all cursor-pointer"
                                            title="Edit row"
                                          >
                                            <Pencil className="w-3 h-3" />
                                          </button>
                                        </td>
                                      )}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                              {selectedTable.rows.length === 0 && (
                                <div className="text-center py-12 text-xs text-slate-400 font-mono">No rows in this table.</div>
                              )}
                            </div>

                            <div className="px-5 py-3 border-t border-[#BFA66A]/15 bg-[#FFFDF8]/60 shrink-0 flex items-center justify-between">
                              <span className="text-[10px] font-mono text-slate-500">
                                {selectedTable.rows.length} row{selectedTable.rows.length !== 1 ? 's' : ''} · {selectedTable.columns.length} column{selectedTable.columns.length !== 1 ? 's' : ''}
                              </span>
                              {!tablesReadOnly && <span className="text-[9px] font-mono text-slate-400 italic">Click any cell to edit · Enter to save · Esc to cancel</span>}
                            </div>
                          </>
                        )}
                      </div>
                    </motion.div>
                  )}

                  {/* ─── WAREHOUSE PREVIEW ───────────────────────────────────── */}
                  {activeTab === 'PREVIEW' && isWarehouse && (
                    <motion.div key="wh-preview" initial={false} animate={false}
                      className="h-full flex items-center justify-center p-8"
                    >
                      <div className="max-w-md w-full space-y-5">
                        <div className={cn(
                          'flex items-center gap-4 p-6 rounded-2xl border',
                          document.metadata?.warehouse_type === 'snowflake'
                            ? 'bg-blue-50 border-blue-200' : 'bg-red-50 border-red-200'
                        )}>
                          <div className={cn(
                            'w-14 h-14 rounded-2xl flex items-center justify-center border',
                            document.metadata?.warehouse_type === 'snowflake'
                              ? 'bg-white border-blue-200' : 'bg-white border-red-200'
                          )}>
                            {document.metadata?.warehouse_type === 'snowflake'
                              ? <Database className="w-7 h-7 text-blue-500" />
                              : <Zap className="w-7 h-7 text-red-500" />}
                          </div>
                          <div>
                            <p className="text-sm font-black text-[#111111]">{document.name}</p>
                            <p className="text-[11px] font-mono text-slate-500 uppercase mt-0.5">{document.metadata?.warehouse_type}</p>
                            <span className={cn(
                              'text-[9px] font-black uppercase px-2 py-0.5 rounded border mt-1 inline-block',
                              document.status === 'PUBLISHED'
                                ? 'bg-green-500/10 border-green-500/20 text-green-700'
                                : 'bg-amber-500/10 border-amber-500/20 text-amber-700'
                            )}>{document.status}</span>
                          </div>
                        </div>
                        <p className="text-xs text-slate-500 font-mono text-center leading-relaxed">
                          Open the <span className="font-black text-[#8A5A00]">Configs</span> tab to view connection details, manage table selections, and activate config versions.
                        </p>
                      </div>
                    </motion.div>
                  )}

                  {/* ─── CONFIGS TAB ─────────────────────────────────────────── */}
                  {activeTab === 'CONFIGS' && isWarehouse && (
                    <motion.div key="configs" initial={false} animate={false}
                      className="h-full flex gap-6 overflow-hidden"
                    >
                      {/* Left: config version list */}
                      <div className="w-72 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                        <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                          <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-2">
                            <Settings className="w-4 h-4 text-[#B88719]" />
                            Config Versions
                          </h4>
                          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]/30">{configs.length}</span>
                        </div>
                        <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar bg-[#FCFBF7]/30">
                          {configsLoading ? (
                            <div className="flex items-center justify-center py-12">
                              <span className="w-5 h-5 rounded-full border-2 border-[#BFA66A]/40 border-t-[#B88719] animate-spin" />
                            </div>
                          ) : configs.length === 0 ? (
                            <div className="text-center py-12 text-xs text-slate-400 font-mono">No configs yet.<br />Start the mock server.</div>
                          ) : configs.map(cfg => (
                            <div key={cfg.id} onClick={() => { setSelectedConfig(cfg); setShowNewConfigFlow(false); }}
                              className={cn(
                                'p-4 border rounded-xl transition-all cursor-pointer group text-left',
                                cfg.id === selectedConfig?.id
                                  ? 'border-[#B88719] bg-[#FFF9E8] shadow-xs'
                                  : 'border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]'
                              )}
                            >
                              <div className="flex justify-between items-center mb-1 text-[10px] font-mono">
                                <span className={cn('font-black', cfg.id === selectedConfig?.id ? 'text-[#8A5A00]' : 'text-slate-500')}>{cfg.version_number}</span>
                                <span className={cn('text-[9px] font-black uppercase px-1.5 py-0.5 rounded',
                                  cfg.status === 'Active' ? 'bg-green-500/10 text-green-700' : 'bg-slate-100 text-slate-500'
                                )}>{cfg.status}</span>
                              </div>
                              <div className="text-[11px] text-slate-600 font-mono">{cfg.created_at.slice(0, 10)}</div>
                              <div className="text-[10px] text-slate-400 mt-1">{cfg.tables.length} table{cfg.tables.length !== 1 ? 's' : ''} selected</div>
                            </div>
                          ))}
                        </div>
                        {!readOnly && (
                          <div className="p-3 border-t border-[#BFA66A]/15 bg-[#FFFDF8]/60">
                            <button
                              onClick={startNewConfigFlow}
                              className="w-full flex items-center justify-center gap-1.5 px-3 py-2.5 bg-[#B88719] text-white rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-[#8A5A00] transition-all cursor-pointer shadow-sm"
                            >
                              <Plus className="w-3.5 h-3.5" />
                              New Config Version
                            </button>
                          </div>
                        )}
                      </div>

                      {/* Right: config detail or new config flow */}
                      <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl bg-white flex flex-col overflow-hidden shadow-xs">
                        {showNewConfigFlow ? (
                          /* ── New config: table selection ── */
                          <>
                            <div className="p-5 border-b border-[#BFA66A]/20 bg-[#FFFDF8] shrink-0 flex justify-between items-center">
                              <div>
                                <h3 className="text-sm font-bold text-[#111111]">New Config Version</h3>
                                <p className="text-[11px] text-slate-500 mt-0.5">Select tables to include in this configuration</p>
                              </div>
                              <button onClick={() => { setShowNewConfigFlow(false); setNewConfigTables([]); }}
                                className="p-1.5 hover:bg-slate-100 rounded-xl text-slate-400 hover:text-slate-600 transition-colors cursor-pointer">
                                <X className="w-4 h-4" />
                              </button>
                            </div>
                            <div className="flex-1 overflow-y-auto p-5 custom-scrollbar">
                              {discoveringNewTables ? (
                                <div className="h-full flex flex-col items-center justify-center gap-3 text-[#8A5A00]">
                                  <RefreshCw className="w-5 h-5 animate-spin text-[#B88719]" />
                                  <p className="text-xs font-mono font-bold">Discovering tables from {document.metadata?.warehouse_type}...</p>
                                </div>
                              ) : (
                                <div className="space-y-3">
                                  {newConfigTables.map(t => (
                                    <div key={t.name} className={cn(
                                      'border rounded-2xl p-4 transition-all',
                                      t.selected ? 'border-[#B88719] bg-[#FFF9E8]' : 'border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40'
                                    )}>
                                      <div className="flex items-start gap-3">
                                        <button onClick={() => toggleNewConfigTable(t.name)}
                                          className={cn(
                                            'w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all cursor-pointer',
                                            t.selected ? 'bg-[#B88719] border-[#B88719]' : 'border-[#BFA66A]/40 bg-white hover:border-[#B88719]'
                                          )}
                                        >
                                          {t.selected && <CheckCircle2 className="w-3 h-3 text-white" />}
                                        </button>
                                        <div className="flex-1">
                                          <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-xs font-mono font-black text-[#111111]">{t.name}</span>
                                            <span className="text-[9px] font-mono text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">{t.schema}</span>
                                            {t.rowCount && <span className="text-[9px] font-mono text-slate-400">{t.rowCount} rows</span>}
                                          </div>
                                          {t.selected && (
                                            <input type="text" placeholder="Describe this table for AI agents..."
                                              value={t.description}
                                              onChange={e => setNewConfigTableDesc(t.name, e.target.value)}
                                              className="mt-2 w-full bg-white border border-[#BFA66A]/30 rounded-xl px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#B88719] placeholder:text-slate-300 transition-all"
                                            />
                                          )}
                                        </div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                            <div className="p-4 border-t border-[#BFA66A]/15 bg-[#FFFDF8]/60 shrink-0 flex items-center justify-between">
                              {newConfigSaveStatus === 'error' && <span className="text-[10px] font-bold text-red-600">Save failed</span>}
                              <div className="ml-auto flex gap-2">
                                <button onClick={() => { setShowNewConfigFlow(false); setNewConfigTables([]); }}
                                  className="px-4 py-2 bg-white border border-[#BFA66A]/30 text-[#8A5A00] rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-[#FFF9E8] transition-all cursor-pointer">
                                  Cancel
                                </button>
                                <button
                                  onClick={handleSaveNewConfig}
                                  disabled={savingNewConfig || newConfigTables.filter(t => t.selected).length === 0}
                                  className={cn(
                                    'flex items-center gap-1.5 px-5 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer',
                                    newConfigTables.filter(t => t.selected).length > 0 && !savingNewConfig
                                      ? 'bg-[#B88719] text-white hover:bg-[#8A5A00] shadow-md'
                                      : 'bg-slate-100 text-slate-400 cursor-not-allowed'
                                  )}
                                >
                                  {savingNewConfig
                                    ? <><RefreshCw className="w-3 h-3 animate-spin" />Saving...</>
                                    : <><Save className="w-3 h-3" />Create Version</>}
                                </button>
                              </div>
                            </div>
                          </>
                        ) : !selectedConfig ? (
                          <div className="flex-1 flex items-center justify-center text-slate-400 text-xs font-mono">Select a config version to inspect</div>
                        ) : (
                          /* ── Selected config detail ── */
                          <>
                            <div className="p-5 border-b border-[#BFA66A]/20 bg-[#FFFDF8] shrink-0 flex justify-between items-start">
                              <div>
                                <span className="text-[10px] font-mono text-[#8A5A00] font-black">{selectedConfig.version_number}</span>
                                <h3 className="text-sm font-bold text-[#111111] font-mono mt-0.5">Warehouse Config</h3>
                                <p className="text-[11px] text-slate-500">{selectedConfig.created_at}</p>
                              </div>
                              <div className="flex items-center gap-2 shrink-0">
                                {configActivateStatus === 'saved' && (
                                  <span className="text-[10px] font-bold text-green-700 flex items-center gap-1">
                                    <CheckCircle2 className="w-3.5 h-3.5" /> Activated
                                  </span>
                                )}
                                {configActivateStatus === 'error' && (
                                  <span className="text-[10px] font-bold text-red-600">Failed</span>
                                )}
                                {!readOnly && selectedConfig.status !== 'Active' && (
                                  <button onClick={handleActivateConfig} disabled={activatingConfig}
                                    className={cn(
                                      'flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer',
                                      activatingConfig ? 'bg-slate-100 text-slate-400' : 'bg-[#B88719] text-white hover:bg-[#8A5A00] shadow-md'
                                    )}
                                  >
                                    {activatingConfig
                                      ? <><RefreshCw className="w-3 h-3 animate-spin" />Activating...</>
                                      : <><Save className="w-3 h-3" />Activate</>}
                                  </button>
                                )}
                                <span className={cn(
                                  'text-[9px] font-black uppercase px-2.5 py-1 rounded-lg border',
                                  selectedConfig.status === 'Active'
                                    ? 'bg-green-500/10 border-green-500/20 text-green-700'
                                    : 'bg-slate-100 border-slate-200 text-slate-500'
                                )}>{selectedConfig.status}</span>
                              </div>
                            </div>

                            <div className="flex-1 overflow-y-auto p-5 custom-scrollbar space-y-5">
                              {/* Connection summary */}
                              {Object.keys(selectedConfig.connection).length > 0 && (
                                <div className="bg-[#FFFDF8] border border-[#BFA66A]/20 rounded-2xl p-5">
                                  <h4 className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest mb-3 flex items-center gap-1.5">
                                    <Lock className="w-3 h-3 text-[#B88719]" />
                                    Connection Parameters
                                  </h4>
                                  <div className="grid grid-cols-2 gap-3">
                                    {Object.entries(selectedConfig.connection).map(([k, v]) => (
                                      <div key={k}>
                                        <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider block">{k}</span>
                                        <span className="text-xs font-mono text-[#111111] break-all">{v}</span>
                                      </div>
                                    ))}
                                  </div>
                                </div>
                              )}

                              {/* Table list */}
                              <div className="bg-[#FFFDF8] border border-[#BFA66A]/20 rounded-2xl p-5">
                                <h4 className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest mb-3 flex items-center gap-1.5">
                                  <Table2 className="w-3 h-3 text-[#B88719]" />
                                  Selected Tables ({selectedConfig.tables.length})
                                </h4>
                                <div className="space-y-3">
                                  {selectedConfig.tables.map(t => (
                                    <div key={t.name} className="flex items-start gap-2.5 p-3 bg-white border border-[#BFA66A]/15 rounded-xl">
                                      <CheckCircle2 className="w-3.5 h-3.5 text-green-600 mt-0.5 shrink-0" />
                                      <div className="min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                          <span className="text-xs font-mono font-black text-[#111111]">{t.name}</span>
                                          <span className="text-[9px] font-mono text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">{t.schema}</span>
                                          {t.rowCount && <span className="text-[9px] font-mono text-slate-400">{t.rowCount} rows</span>}
                                        </div>
                                        {t.description && (
                                          <p className="text-[11px] text-slate-500 mt-1 leading-normal">{t.description}</p>
                                        )}
                                      </div>
                                    </div>
                                  ))}
                                  {selectedConfig.tables.length === 0 && (
                                    <p className="text-xs text-slate-400 font-mono">No tables in this config.</p>
                                  )}
                                </div>
                              </div>
                            </div>
                          </>
                        )}
                      </div>
                    </motion.div>
                  )}

                  {/* ─── LOGS TAB ─────────────────────────────────────────────── */}
                  {activeTab === 'LOGS' && (() => {
                    const levelColor = (lvl: string) => {
                      const l = lvl.toUpperCase();
                      if (l === 'ERROR')   return 'text-red-400 font-bold';
                      if (l === 'WARN' || l === 'WARNING') return 'text-amber-500 font-bold';
                      if (l === 'SUCCESS') return 'text-emerald-400 font-bold';
                      if (l === 'DEBUG')   return 'text-slate-500';
                      return 'text-amber-300/80';
                    };
                    const fmtTime = (iso: string | null) => {
                      if (!iso) return '--:--:--';
                      const d = new Date(iso);
                      return isNaN(d.getTime()) ? '--:--:--' : d.toLocaleTimeString();
                    };
                    const logs = logData?.logs ?? [];
                    return (
                    <motion.div key="logs" className="h-full border border-[#BFA66A]/30 rounded-[2rem] bg-[#1E1B15] overflow-hidden flex flex-col shadow-sm">
                       <div className="p-4 border-b border-white/5 flex justify-between items-center bg-black/20">
                          <div className="flex items-center gap-3">
                             <Terminal className="w-4 h-4 text-[#D9B86C]" />
                             <h4 className="text-[10px] font-black text-[#FFFDF8] uppercase tracking-widest">
                                Ingestion Engine Logs
                             </h4>
                          </div>
                          {logData?.status && (
                            <span className="text-[9px] font-mono font-bold text-[#D9B86C] uppercase tracking-widest px-2 py-0.5 rounded-full border border-[#D9B86C]/30 bg-[#D9B86C]/10">
                              {logData.status}{logData.stage ? ` · ${logData.stage}` : ''}
                            </span>
                          )}
                       </div>
                       <div className="flex-1 p-6 overflow-y-auto space-y-3 font-mono text-[11px] leading-relaxed custom-scrollbar text-slate-300">
                          {logsLoading ? (
                            <div className="flex items-center justify-center h-32 text-[#D9B86C]/70 text-xs">Loading logs…</div>
                          ) : logs.length === 0 ? (
                            <div className="flex items-center justify-center h-32 text-slate-500 text-xs">No ingestion logs recorded for this document yet.</div>
                          ) : (
                            logs.map((log, idx) => (
                              <div key={`${log.timestamp}-${idx}`} className="flex gap-4 group">
                                 <span className="text-slate-500 shrink-0">{fmtTime(log.timestamp)}</span>
                                 <span className={cn("shrink-0 font-bold", levelColor(log.level))}>[{log.level.toUpperCase()}]</span>
                                 <span className="text-slate-300 break-words">{log.message}</span>
                              </div>
                            ))
                          )}
                       </div>
                    </motion.div>
                    );
                  })()}

                  {/* ─── TIMELINE TAB ─────────────────────────────────────────── */}
                  {activeTab === 'TIMELINE' && (
                    <motion.div key="timeline" className="h-full border border-[#BFA66A]/30 rounded-[2rem] bg-white overflow-hidden flex flex-col shadow-sm">
                       <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                          <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest">
                            Pipeline Lifecycle Timeline
                          </h4>
                       </div>
                       <div className="flex-1 overflow-y-auto p-8 custom-scrollbar relative bg-[#FCFBF7]/30">
                          {historyLoading ? (
                            <div className="flex items-center justify-center h-32 text-[#8A5A00] text-xs">Loading history…</div>
                          ) : historyEntries.length === 0 ? (
                            <div className="flex items-center justify-center h-32 text-slate-400 text-xs">No lifecycle events yet.</div>
                          ) : (
                            <div className="relative space-y-8">
                              <div className="absolute left-[39px] top-2 bottom-2 w-0.5 bg-[#BFA66A]/20" />
                              {historyEntries.map((entry, idx) => {
                                const isFirst = idx === 0;
                                const tierColor = entry.to_tier === 'gold'
                                  ? 'border-[#B88719] text-[#B88719]'
                                  : entry.to_tier === 'silver'
                                    ? 'border-slate-500 text-slate-500'
                                    : 'border-slate-400 text-slate-400';
                                const meta = entry.pipeline_metadata ?? {};
                                const ts = entry.transitioned_at
                                  ? new Date(entry.transitioned_at).toLocaleString()
                                  : '—';

                                // Build pipeline stat badges from metadata
                                const statBadges: { label: string; value: string | number; green?: boolean; red?: boolean }[] = [];
                                if (meta.chunks_accepted != null)   statBadges.push({ label: 'accepted', value: meta.chunks_accepted as number, green: true });
                                if (meta.chunks_rejected != null)   statBadges.push({ label: 'rejected', value: meta.chunks_rejected as number, red: (meta.chunks_rejected as number) > 0 });
                                if (meta.chunks_committed != null)  statBadges.push({ label: 'committed', value: meta.chunks_committed as number, green: true });
                                if (meta.chunks_conflicted != null) statBadges.push({ label: 'conflicts', value: meta.chunks_conflicted as number, red: (meta.chunks_conflicted as number) > 0 });
                                if (meta.chunks_skipped != null)    statBadges.push({ label: 'skipped', value: meta.chunks_skipped as number });
                                if (meta.chunks_failed != null)     statBadges.push({ label: 'failed', value: meta.chunks_failed as number, red: (meta.chunks_failed as number) > 0 });
                                if (meta.tables_found != null)      statBadges.push({ label: 'tables', value: meta.tables_found as number });
                                if (meta.file_size != null)         statBadges.push({ label: 'bytes', value: (meta.file_size as number).toLocaleString() });

                                return (
                                  <div key={entry.history_id} className="relative pl-12 text-left">
                                    <div className={cn(
                                      "absolute left-[32px] top-1.5 w-4.5 h-4.5 rounded-full border bg-white flex items-center justify-center z-10",
                                      tierColor
                                    )}>
                                      <div className="w-1.5 h-1.5 rounded-full bg-current" />
                                    </div>
                                    <div className="p-5 bg-white border border-[#BFA66A]/20 rounded-2xl shadow-2xs max-w-xl">
                                      <div className="flex items-center gap-2 flex-wrap">
                                        <span className="text-[10px] font-mono font-black text-[#8A5A00] uppercase">{entry.to_tier}</span>
                                        <span className="text-[10px] font-mono text-slate-500">{ts}</span>
                                        {isFirst && (
                                          <span className="px-1.5 bg-[#FFF9E8] border border-[#BFA66A]/30 text-[#8A5A00] text-[9px] font-black rounded uppercase">Latest</span>
                                        )}
                                      </div>
                                      <div className="text-xs font-black text-[#111111] mt-1.5">{entry.notes ?? `Transitioned to ${entry.to_tier}`}</div>
                                      {entry.approved_by && (
                                        <div className="text-[10px] text-slate-500 mt-1">
                                          By: <span className="text-[#8A5A00] font-bold">{entry.approved_by}</span>
                                        </div>
                                      )}
                                      {statBadges.length > 0 && (
                                        <div className="flex flex-wrap gap-1.5 mt-3">
                                          {statBadges.map(b => (
                                            <span key={b.label} className={cn(
                                              "px-2 py-0.5 rounded text-[9px] font-black uppercase border",
                                              b.green ? "bg-green-50 border-green-200 text-green-700"
                                                : b.red ? "bg-red-50 border-red-200 text-red-600"
                                                  : "bg-slate-50 border-slate-200 text-slate-500"
                                            )}>
                                              {b.label}: {b.value}
                                            </span>
                                          ))}
                                        </div>
                                      )}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                       </div>
                    </motion.div>
                  )}

            </AnimatePresence>
         </div>
      </div>
    </div>
  );
};
