import React, { useState, useEffect, useMemo, useRef } from 'react';
import ForceGraph2D from 'react-force-graph-2d';
// Same force module react-force-graph uses internally — adds collision so nodes
// don't pile on top of each other (overlapping dots are impossible to click).
import { forceCollide } from 'd3-force-3d';
import {
  Network, Database, Zap, Plus, Layers, Info, Code, Play,
  ArrowDown, Circle, CheckCircle2, Eye, ArrowLeft,
  Layers3, Clock, Tag, GitBranch, Trash2, FileText, Video, Globe, ServerCog,
  Table2, RefreshCw, AlertCircle, Server, Link, Search,
  Cpu, SendHorizonal, Loader2, Hash, List, MessageSquare,
} from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useAppState } from '../../../AppStateContext';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { KnowledgeDocument } from '../../../types';
import {
  GraphHubTab, DBDocument, getSubType,
  WTableEntry, WConfig, WConnection, getWarehouseConnection, formatRows, nextVersion,
  ChunkVersion, Chunk, QdrantCollection, QdrantPoint, QueryStep,
  GraphNode, GraphEdge, Neo4jSchema,
  buildCypher, subgraphByType, nodeColor,
} from './knowledgeHub.data';
import { SourceCategory, ALL_SOURCE_CATEGORIES, sourceParam } from '../inventory/inventory.data';

// Source-type filter chips for the Database tab (Gold layer). Same `SourceCategory`
// tokens the backend understands; icons kept for parity with the old sub-type pills.
const DB_SOURCE_CHIPS: { id: SourceCategory; label: string; icon: React.ElementType }[] = [
  { id: 'doc',       label: 'Document',  icon: FileText  },
  { id: 'media',     label: 'Media',     icon: Video     },
  { id: 'web',       label: 'Web',       icon: Globe     },
];
function normalizeWConfig(raw: Record<string, unknown>, idx: number): WConfig {
  const tables = ((raw.tables ?? []) as Array<Record<string, unknown>>).map((t, ti) => ({
    id: (t.id as string) ?? `${String(raw.id)}-t-${ti}`,
    schema: (t.schema as string) ?? '',
    tableName: (t.tableName as string) ?? (t.name as string) ?? '',
    rowCount: typeof t.rowCount === 'number'
      ? t.rowCount
      : parseInt(String(t.rowCount ?? '0').replace(/[^0-9]/g, ''), 10) || 0,
    // The backend sends `columns` as the full column-definition array
    // ([{name, type, …}]); the UI only shows a count. Rendering the array as a
    // React child throws "Objects are not valid as a React child" (white screen),
    // so coerce to its length here (defensive: accept a pre-counted number too).
    columns: Array.isArray(t.columns)
      ? t.columns.length
      : (typeof t.columns === 'number' ? t.columns : 0),
  }));
  const rawStatus = (raw.status as string) ?? '';
  return {
    id: raw.id as string,
    name: (raw.name as string) ?? `Config ${idx + 1}`,
    version: (raw.version as string) ?? (raw.version_number as string) ?? 'v1',
    createdAt: (raw.createdAt as string) ?? (raw.created_at as string) ?? '',
    status: rawStatus === 'Active' ? 'Active' : rawStatus === 'Archived' ? 'Archived' : 'Draft',
    syncSchedule: (raw.syncSchedule as string) ?? (raw.sync_schedule as string) ?? 'Manual',
    connection: raw.connection as Record<string, string> | undefined,
    tables,
  };
}

export const KnowledgeHubView = () => {
  const [activeTab, setActiveTab] = useState<GraphHubTab>('DATABASE');

  /* ── Database: GOLD docs via stateless cursor pagination (same source/feel as Data Layers) ── */
  // Multi-select source chips, default all-on. Backend filters via `?source=…`
  // (WHERE source_type IN (...)); no "All" chip — all-on already means everything.
  const [selectedSources, setSelectedSources] = useState<Set<SourceCategory>>(
    () => new Set(ALL_SOURCE_CATEGORIES),
  );
  const sourceQuery = sourceParam(selectedSources);
  const {
    items: goldDocs, page: dbPage, totalPages, hasNext: dbHasNext,
    isLoading: docsLoading, canJump: dbCanJump, goTo: dbGoTo, reload: dbReload,
  } = useCursorPagination<KnowledgeDocument>(
    '/api/data/documents',
    { tier: 'gold', source: sourceQuery },
    [sourceQuery],
  );

  const docs = useMemo<DBDocument[]>(
    () => goldDocs.map(d => ({
      id: d.data_id,
      name: d.name,
      type: d.metadata?.doc_type ?? 'Unknown',
      added_date: d.added_on ?? '',
      language: d.metadata?.language ?? 'N/A',
      author: d.metadata?.author ?? d.added_by ?? '',
      version: '',
      subType: getSubType(d.metadata?.doc_type ?? ''),
    })),
    [goldDocs],
  );

  const [dbSearch, setDbSearch]             = useState('');
  const [selectedDoc, setSelectedDoc]       = useState<DBDocument | null>(null);
  const [chunks, setChunks]                 = useState<Chunk[]>([]);
  const [chunksLoading, setChunksLoading]   = useState(false);
  const [selectedChunk, setSelectedChunk]   = useState<Chunk | null>(null);
  const [selectedVersion, setSelectedVersion] = useState<ChunkVersion | null>(null);

  /* â"€â"€ Warehouse config state â"€â"€ */
  const [warehouseConfigs, setWarehouseConfigs]           = useState<WConfig[]>([]);
  const [warehouseConfigsLoading, setWarehouseConfigsLoading] = useState(false);
  const [selectedConfig, setSelectedConfig]               = useState<WConfig | null>(null);
  const [newCfgDrawerOpen, setNewCfgDrawerOpen]           = useState(false);
  const [newCfgName, setNewCfgName]                       = useState('');
  const [newCfgSchedule, setNewCfgSchedule]               = useState('Every 6 hours');
  const [newCfgSelectedIds, setNewCfgSelectedIds]         = useState<Set<string>>(new Set());
  const [newCfgSchemaFilter, setNewCfgSchemaFilter]       = useState<string>('All');

  /* â"€â"€ Qdrant state â"€â"€ */
  const [qdrantCollections, setQdrantCollections]       = useState<QdrantCollection[]>([]);
  const [selectedCollection, setSelectedCollection]     = useState<QdrantCollection | null>(null);
  const [qdrantQuery, setQdrantQuery]                   = useState('');
  const [qdrantPoints, setQdrantPoints]                 = useState<QdrantPoint[]>([]);
  const [qdrantSearching, setQdrantSearching]           = useState(false);
  const [qdrantTopK, setQdrantTopK]                     = useState(5);

  /* New-version form */
  const [newVerDrawerOpen, setNewVerDrawerOpen] = useState(false);
  const [newVerContent, setNewVerContent]       = useState('');

  /* â"€â"€ Auto-computed next version tags â"€â"€ */
  const autoNextChunkVer = useMemo(
    () => nextVersion(selectedChunk?.versions.map(v => v.version_number) ?? []),
    [selectedChunk],
  );
  const autoNextCfgVer = useMemo(
    () => nextVersion(warehouseConfigs.map(c => c.version)),
    [warehouseConfigs],
  );

  /* Derive discoverable tables from all loaded warehouse configs (union, de-duped by schema.tableName) */
  const availableTablesForNewConfig = useMemo<WTableEntry[]>(() => {
    const seen = new Set<string>();
    const result: WTableEntry[] = [];
    warehouseConfigs.forEach(cfg => {
      cfg.tables.forEach(t => {
        const key = `${t.schema}.${t.tableName}`;
        if (!seen.has(key)) { seen.add(key); result.push(t); }
      });
    });
    return result;
  }, [warehouseConfigs]);

  /* â"€â"€ Query builder state â"€â"€ */
  const [startNode, setStartNode]         = useState('');
  const [steps, setSteps]                 = useState<QueryStep[]>([]);
  const [cypher, setCypher]               = useState('');
  const [queryRan, setQueryRan]           = useState(false);
  const [queryRunning, setQueryRunning]   = useState(false);
  const [entitySearchQuery, setEntitySearchQuery] = useState('');
  const [entitySearchOpen,  setEntitySearchOpen]  = useState(false);

  /* â"€â"€ Neo4j graph state â"€â"€ */
  const [neoSchema,      setNeoSchema]      = useState<Neo4jSchema | null>(null);
  const [neoLoading,     setNeoLoading]     = useState(false);
  const [rawGraphNodes,  setRawGraphNodes]  = useState<GraphNode[]>([]);
  const [rawGraphEdges,  setRawGraphEdges]  = useState<GraphEdge[]>([]);
  const [filteredNodes,  setFilteredNodes]  = useState<GraphNode[] | null>(null);
  const [filteredEdges,  setFilteredEdges]  = useState<GraphEdge[] | null>(null);
  const [graphFiltered,  setGraphFiltered]  = useState(false);
  const [selectedNeoNode, setSelectedNeoNode] = useState<{ id: string; name: string; description?: string | null; type?: string | null } | null>(null);
  // Currently hovered node id — drives the in-canvas hover label/ring so hovering
  // a node actually shows feedback (previously hover did nothing).
  const [hoverNodeId, setHoverNodeId] = useState<string | null>(null);

  const fgRef              = useRef<any>(null);
  const graphContainerRef  = useRef<HTMLDivElement>(null);
  const [graphDims, setGraphDims] = useState({ w: 600, h: 540 });
  // zoomToFit only once per data load — otherwise every drag/settle re-fits the
  // view and the canvas appears to zoom out on its own while you're clicking.
  const didFitRef = useRef(false);

  /* Keep canvas sized to its container. Depends on activeTab because the graph
   * container only mounts when the NEO4J tab is active — without this the
   * observer never attaches and the canvas stays stuck at its initial size. */
  useEffect(() => {
    if (activeTab !== 'NEO4J') return;
    const el = graphContainerRef.current;
    if (!el) return;
    const update = () => setGraphDims({ w: el.clientWidth, h: el.clientHeight });
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [activeTab]);

  /* Derived graph data for ForceGraph2D */
  const graphData = useMemo(() => ({
    nodes: (filteredNodes ?? rawGraphNodes).map(n => ({ ...n })),
    links: (filteredEdges ?? rawGraphEdges).map(e => ({ source: e.from, target: e.to, description: e.description })),
  }), [rawGraphNodes, rawGraphEdges, filteredNodes, filteredEdges]);

  /* Distinct node types currently on the canvas — drives the colour legend. */
  const presentTypes = useMemo(() => {
    const set = new Set<string>();
    for (const n of (filteredNodes ?? rawGraphNodes)) if (n.type) set.add(n.type);
    return [...set].sort();
  }, [rawGraphNodes, filteredNodes]);

  /* Tune the force simulation once the graph is mounted/populated:
   *  - stronger charge + longer links  → nodes sit further apart
   *  - low link strength               → links are springy, so dragging a node
   *    stretches its links and only pulls the cluster once they're taut. */
  useEffect(() => {
    if (activeTab !== 'NEO4J') return;
    didFitRef.current = false; // new data → allow one auto-fit
    const fg = fgRef.current;
    if (!fg || graphData.nodes.length === 0) return;
    fg.d3Force('charge')?.strength(-180).distanceMax(450);
    fg.d3Force('link')?.distance(90).strength(0.12);
    // Collision keeps every node ≥ this radius apart so dots never overlap and
    // each one stays individually hoverable/clickable.
    fg.d3Force('collide', forceCollide(26).strength(0.9));
    fg.d3ReheatSimulation?.();
  }, [activeTab, graphData]);

  /* â"€â"€ Fetch: Qdrant on mount â"€â"€ */
  useEffect(() => {
    apiGet<QdrantCollection[]>('/api/knowledge/qdrant/collections')
      .then(data => setQdrantCollections(data ?? []))
      .catch(() => setQdrantCollections([]));
  }, []);

  /* â"€â"€ Fetch: Neo4j graph + schema when NEO4J tab is active â"€â"€ */
  useEffect(() => {
    if (activeTab !== 'NEO4J') return;
    setNeoLoading(true);
    Promise.all([
      apiGet<{ nodes: GraphNode[]; edges: GraphEdge[] }>('/api/knowledge/neo4j/graph'),
      apiGet<Neo4jSchema>('/api/knowledge/neo4j/schema'),
    ])
      .then(([graphData, schema]) => {
        if (graphData?.nodes?.length) {
          setRawGraphNodes(graphData.nodes);
          setRawGraphEdges(graphData.edges ?? []);
          setFilteredNodes(null);
          setFilteredEdges(null);
          setGraphFiltered(false);
        }
        if (schema?.entities?.length) {
          setNeoSchema(schema);
          setStartNode(prev => prev || schema.entities[0]);
        }
      })
      .catch(() => {})
      .finally(() => setNeoLoading(false));
  }, [activeTab]);

  /* â"€â"€ Delete confirmation state â"€â"€ */
  const [pendingDeleteDoc,     setPendingDeleteDoc]     = useState<string | null>(null);
  const [pendingDeleteChunk,   setPendingDeleteChunk]   = useState<string | null>(null);
  const [pendingDeleteVersion, setPendingDeleteVersion] = useState<string | null>(null);
  const [pendingDeleteConfig,  setPendingDeleteConfig]  = useState<string | null>(null);
  const [pendingDeleteTable,   setPendingDeleteTable]   = useState<string | null>(null);

  const { deleteDocument } = useAppState();

  const handleDeleteDoc = async (docId: string) => {
    try {
      await apiMutate('DELETE', `/api/data/documents/${docId}`);
      deleteDocument(docId);
      if (selectedDoc?.id === docId) setSelectedDoc(null);
      dbReload(true); // refresh the current gold page after removal

    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteDoc(null); }
  };

  const handleDeleteChunk = async (chunkId: string) => {
    if (!selectedDoc) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/documents/${selectedDoc.id}/chunks/${chunkId}`);
      setChunks(prev => prev.filter(c => c.id !== chunkId));
      if (selectedChunk?.id === chunkId) { setSelectedChunk(null); setSelectedVersion(null); }
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteChunk(null); }
  };

  const handleDeleteVersion = async (versionNumber: string) => {
    if (!selectedDoc || !selectedChunk) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/documents/${selectedDoc.id}/chunks/${selectedChunk.id}/versions/${versionNumber}`);
      const updated = { ...selectedChunk, versions: selectedChunk.versions.filter(v => v.version_number !== versionNumber) };
      setChunks(prev => prev.map(c => c.id === selectedChunk.id ? updated : c));
      setSelectedChunk(updated);
      if (selectedVersion?.version_number === versionNumber) setSelectedVersion(updated.versions[0] ?? null);
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteVersion(null); }
  };

  const handleDeleteConfig = async (configId: string) => {
    if (!selectedDoc) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/warehouses/${selectedDoc.id}/configs/${configId}`);
      const updated = warehouseConfigs.filter(c => c.id !== configId);
      setWarehouseConfigs(updated);
      if (selectedConfig?.id === configId) setSelectedConfig(updated[0] ?? null);
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteConfig(null); }
  };

  const handleDeleteTable = async (tableId: string) => {
    if (!selectedDoc || !selectedConfig) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/warehouses/${selectedDoc.id}/configs/${selectedConfig.id}/tables/${tableId}`);
      const updated = { ...selectedConfig, tables: selectedConfig.tables.filter(t => t.id !== tableId) };
      setWarehouseConfigs(prev => prev.map(c => c.id === selectedConfig.id ? updated : c));
      setSelectedConfig(updated);
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteTable(null); }
  };

  /* â"€â"€ Qdrant toggle active â"€â"€ */
  const [qdrantToggling, setQdrantToggling] = useState(false);
  const handleToggleActive = async () => {
    if (!selectedCollection) return;
    setQdrantToggling(true);
    try {
      const updated = await apiMutate<QdrantCollection>(
        'PATCH',
        `/api/knowledge/qdrant/collections/${selectedCollection.id}`,
        { active: !selectedCollection.active },
      );
      if (updated) {
        setQdrantCollections(prev => prev.map(c => c.id === updated.id ? updated : c));
        setSelectedCollection(updated);
      }
    } finally {
      setQdrantToggling(false);
    }
  };

  /* â"€â"€ Qdrant semantic search â"€â"€ */
  const handleQdrantSearch = async () => {
    if (!selectedCollection || !qdrantQuery.trim()) return;
    setQdrantSearching(true);
    try {
      const points = await apiMutate<QdrantPoint[]>(
        'POST',
        `/api/knowledge/qdrant/collections/${selectedCollection.id}/search`,
        { query: qdrantQuery.trim(), limit: qdrantTopK },
      );
      setQdrantPoints(points ?? []);
    } catch {
      setQdrantPoints([]);
    } finally {
      setQdrantSearching(false);
    }
  };

  /* â"€â"€ Fetch: chunks or warehouse configs when a doc is selected â"€â"€ */
  useEffect(() => {
    if (!selectedDoc) {
      setChunks([]); setSelectedChunk(null); setSelectedVersion(null);
      setWarehouseConfigs([]); setSelectedConfig(null);
      return;
    }
    if (selectedDoc.subType === 'Warehouse') {
      setWarehouseConfigsLoading(true);
      apiGet<Record<string, unknown>[] | { items: Record<string, unknown>[] }>(`/api/knowledge/warehouses/${selectedDoc.id}/configs`)
        .then(data => {
          const rows = Array.isArray(data) ? data : (data?.items ?? []);
          const list = rows.map((r, i) => normalizeWConfig(r, i));
          setWarehouseConfigs(list);
          setSelectedConfig(list[0] ?? null);
        })
        .catch(() => {
          setWarehouseConfigs([]);
          setSelectedConfig(null);
        })
        .finally(() => setWarehouseConfigsLoading(false));
    } else {
      setChunksLoading(true);
      apiGet<Chunk[] | { items: Chunk[] }>(`/api/knowledge/documents/${selectedDoc.id}/chunks`)
        .then(data => {
          const rows = Array.isArray(data) ? data : (data?.items ?? []);
          const list = rows.map(chunk => ({
            ...chunk,
            versions: chunk.versions.map(v => ({
              ...v,
              status: (v.status?.toLowerCase() === 'active' ? 'Active' : 'Inactive') as 'Active' | 'Inactive',
            })),
          }));
          setChunks(list);
          setSelectedChunk(list[0] ?? null);
          setSelectedVersion(list[0]?.versions[0] ?? null);
        })
        .catch(() => {
          setChunks([]);
          setSelectedChunk(null);
          setSelectedVersion(null);
        })
        .finally(() => setChunksLoading(false));
    }
  }, [selectedDoc]);

  /* â"€â"€ Chunk helpers â"€â"€ */
  const handleChunkChange = (chunk: Chunk) => {
    setSelectedChunk(chunk);
    setSelectedVersion(chunk.versions[0]);
  };

  /* â"€â"€ Activate a version (set as Active, demote others) â"€â"€ */
  const activateVersion = (ver: ChunkVersion) => {
    if (!selectedChunk || !selectedDoc) return;
    const updatedVersions = selectedChunk.versions.map(v => ({
      ...v,
      status: (v.version_number === ver.version_number ? 'Active' : 'Inactive') as 'Active' | 'Inactive',
    }));
    const updatedChunk = { ...selectedChunk, versions: updatedVersions };
    setChunks(prev => prev.map(c => c.id === selectedChunk.id ? updatedChunk : c));
    setSelectedChunk(updatedChunk);
    setSelectedVersion({ ...ver, status: 'Active' });
    apiMutate('PATCH', `/api/knowledge/documents/${selectedDoc.id}/chunks/${selectedChunk.id}/activate`, {
      version_number: ver.version_number,
    }).catch(() => {});
  };

  /* â"€â"€ Create new chunk version (auto-versioned, persisted to server) â"€â"€ */
  const handleCreateVersion = () => {
    if (!newVerContent.trim() || !selectedChunk || !selectedDoc) return;
    const now = new Date().toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
    const newVer: ChunkVersion = {
      version_number: autoNextChunkVer,
      create_at: now,
      status: 'Inactive',
      embedding_models: 'text-embedding-3-large (3072d)',
      entities: [],
      intent: '',
      text: newVerContent.trim(),
    };
    const updatedChunk = { ...selectedChunk, versions: [newVer, ...selectedChunk.versions] };
    setChunks(prev => prev.map(c => c.id === selectedChunk.id ? updatedChunk : c));
    setSelectedChunk(updatedChunk);
    setSelectedVersion(newVer);
    setNewVerContent('');
    setNewVerDrawerOpen(false);
    apiMutate('POST', `/api/knowledge/documents/${selectedDoc.id}/chunks/${selectedChunk.id}/versions`, {
      version_number: newVer.version_number,
      text: newVer.text,
    }).catch(() => {});
  };

  /* â"€â"€ Warehouse config helpers â"€â"€ */
  const activateConfig = (cfg: WConfig) => {
    if (!selectedDoc) return;
    const updated = warehouseConfigs.map(c => ({
      ...c,
      status: (c.id === cfg.id ? 'Active' : c.status === 'Active' ? 'Inactive' : c.status) as WConfig['status'],
    }));
    setWarehouseConfigs(updated);
    setSelectedConfig(updated.find(c => c.id === cfg.id) ?? null);
    apiMutate('PATCH', `/api/knowledge/warehouses/${selectedDoc.id}/configs/${cfg.id}/activate`, {}).catch(() => {});
  };

  const handleCreateConfig = () => {
    if (!newCfgName.trim() || newCfgSelectedIds.size === 0 || !selectedDoc) return;
    const pickedTables = availableTablesForNewConfig.filter(t => newCfgSelectedIds.has(t.id));
    const newCfg: WConfig = {
      id: `cfg-${Date.now()}`,
      name: newCfgName.trim(),
      version: autoNextCfgVer,
      createdAt: new Date().toISOString().replace('T', ' ').slice(0, 16) + ' UTC',
      status: 'Draft',
      syncSchedule: newCfgSchedule,
      tables: pickedTables,
    };
    setWarehouseConfigs(prev => [newCfg, ...prev]);
    setSelectedConfig(newCfg);
    setNewCfgName(''); setNewCfgSelectedIds(new Set()); setNewCfgSchemaFilter('All');
    setNewCfgDrawerOpen(false);
    apiMutate('POST', `/api/knowledge/warehouses/${selectedDoc.id}/configs`, {
      name: newCfg.name,
      version: newCfg.version,
      syncSchedule: newCfg.syncSchedule,
      tables: newCfg.tables,
      status: 'Draft',
    }).catch(() => {});
  };

  const toggleSource = (cat: SourceCategory) => {
    setSelectedSources(prev => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
  };

  // Source filtering is server-side now (`?source=…`). Only free-text search stays
  // client-side, scoped to the current server page (same model as Data Layers).
  const pagedDocs = useMemo(() => {
    if (!dbSearch.trim()) return docs;
    const q = dbSearch.toLowerCase();
    return docs.filter(d =>
      d.name.toLowerCase().includes(q) ||
      d.type.toLowerCase().includes(q) ||
      d.author.toLowerCase().includes(q) ||
      d.language.toLowerCase().includes(q),
    );
  }, [docs, dbSearch]);

  /* ── Query builder helpers ── */
  const allEntities = neoSchema?.entities ?? [];
  const filteredEntityOptions = entitySearchQuery.trim()
    ? allEntities.filter(e => e.toLowerCase().includes(entitySearchQuery.toLowerCase()))
    : allEntities;
  // Target options are entity TYPES (labels). schema.connections maps a label to
  // its relationship types (not target labels), so it must not be used here —
  // doing so put rel names like "RELATED_TO" into the node dropdown.
  const getTargets = (_fromEntity: string) => allEntities.filter(Boolean);
  const addStep = () => {
    if (!allEntities.length) return;
    const prevEntity = steps.length ? steps[steps.length - 1].nodeType : startNode;
    const targets = getTargets(prevEntity);
    setSteps(p => [...p, { id: `s-${Date.now()}`, relationship: 'RELATED_TO', nodeType: targets[0] ?? allEntities[0] }]);
    setQueryRan(false);
  };
  const deleteStep = (id: string) => { setSteps(p => p.filter(s => s.id !== id)); setQueryRan(false); };
  const updateStep = (id: string, v: string) => { setSteps(p => p.map(s => s.id === id ? { ...s, nodeType: v } : s)); setQueryRan(false); };

  const tabs: { id: GraphHubTab; label: string; icon: React.ElementType }[] = [
    { id: 'DATABASE', label: 'Database', icon: Database },
    { id: 'QDRANT',   label: 'Qdrant',   icon: Layers   },
    { id: 'NEO4J',    label: 'Neo4j',    icon: Network  },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-500 text-[#111111]">

      {/* PAGE HEADER */}
      <div>
        <div className="flex items-center gap-1.5 text-[10px] font-black tracking-widest uppercase text-[#8A5A00] font-mono mb-1">
          <Network className="w-3.5 h-3.5" />
          Knowledge Graph
        </div>
        <h1 className="text-3xl font-display font-bold tracking-tight">Knowledge Hub</h1>
        <p className="text-slate-500 text-sm mt-1 max-w-2xl">
          Manage Gold-layer databases, inspect Qdrant vector collections, and explore Neo4j entity graphs.
        </p>
      </div>

      {/* INNER TABS */}
      <div className="flex gap-0 border-b border-[#BFA66A]/20">
        {tabs.map(tab => (
          <button key={tab.id} onClick={() => { setActiveTab(tab.id); setSelectedDoc(null); }}
            className={cn(
              'flex items-center gap-2 px-5 py-2.5 text-xs font-bold uppercase tracking-widest transition-all border-b-2 -mb-px',
              activeTab === tab.id
                ? 'border-[#B88719] text-[#B88719]'
                : 'border-transparent text-slate-500 hover:text-[#8A5A00] hover:border-[#BFA66A]/40'
            )}>
            <tab.icon className="w-3.5 h-3.5" />
            {tab.label}
          </button>
        ))}
      </div>

      {/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• DATABASE TAB â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
      {activeTab === 'DATABASE' && (
        <>
          {/* New-version drawer */}
          <DetailDrawer
            isOpen={newVerDrawerOpen}
            onClose={() => setNewVerDrawerOpen(false)}
            title={`New Version Â· ${selectedChunk?.id ?? ''}`}
            subtitle={selectedChunk?.title ?? ''}
            icon={GitBranch}
            size="standard"
            footer={
              <div className="flex justify-end gap-2.5 w-full">
                <button onClick={() => setNewVerDrawerOpen(false)}
                  className="px-3.5 py-2 bg-white border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-bold text-slate-500 uppercase tracking-wide cursor-pointer">
                  Cancel
                </button>
                <button onClick={handleCreateVersion}
                  disabled={!newVerContent.trim()}
                  className={cn(
                    'px-5 py-2 rounded-xl text-xs font-black uppercase tracking-wider flex items-center gap-1.5 cursor-pointer transition-all',
                    newVerContent.trim()
                      ? 'bg-[#B88719] text-white hover:bg-[#8A5A00]'
                      : 'bg-slate-100 text-slate-400 border border-slate-200 cursor-not-allowed'
                  )}>
                  <Plus className="w-3.5 h-3.5" />
                  Create Version
                </button>
              </div>
            }>
            <div className="p-6 space-y-5">
              <div className="flex items-center gap-3 px-4 py-2.5 bg-[#FFFDF8] border border-[#BFA66A]/30 rounded-xl">
                <GitBranch className="w-3.5 h-3.5 text-[#B88719] shrink-0" />
                <div>
                  <p className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono">Next Version (auto-assigned)</p>
                  <p className="text-sm font-mono font-black text-[#111111] mt-0.5">{autoNextChunkVer}</p>
                </div>
              </div>
              <div className="space-y-1.5">
                <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">Chunk Text Content *</label>
                <textarea value={newVerContent} onChange={e => setNewVerContent(e.target.value)}
                  placeholder="Enter the updated chunk text for this version..."
                  rows={6}
                  className="w-full bg-white border border-[#BFA66A]/35 rounded-xl px-4 py-3 text-xs text-[#111111] placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-[#B88719] transition-all font-mono leading-relaxed resize-none" />
              </div>
              <div className="p-3.5 bg-[#FFF9E8] border border-[#BFA66A]/30 rounded-xl flex gap-3">
                <Info className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
                <p className="text-[11px] text-[#5A4209] leading-relaxed">
                  The new version will be created as <strong>Inactive</strong>. Use the <strong>Set Active</strong> button to promote it as the live retriever.
                </p>
              </div>
            </div>
          </DetailDrawer>

          {docsLoading ? (
            <div className="flex items-center justify-center py-20 text-slate-400 text-sm">Loading databaseâ€¦</div>
          ) : !selectedDoc ? (
            /* â"€â"€ GOLD-STYLE DOCUMENT TABLE â"€â"€ */
            <div className="space-y-4">

              {/* Source filter placed directly above the search bar (same layout as Data Layers) */}
              <div className="bg-[#FFFDF8] border border-[#BFA66A]/20 p-5 rounded-2xl flex flex-col gap-4">
                {/* Source-type filter pills — multi-select, server-side filtering */}
                <div className="flex flex-col gap-2">
                  <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] opacity-80 font-mono">
                    Source Type
                  </span>
                  <div className="flex gap-1.5 flex-wrap">
                    {DB_SOURCE_CHIPS.map(st => {
                      const active = selectedSources.has(st.id);
                      return (
                        <button
                          key={st.id}
                          onClick={() => toggleSource(st.id)}
                          aria-pressed={active}
                          className={cn(
                            'flex items-center gap-1.5 px-3 py-1.5 rounded-xl border text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer',
                            active
                              ? 'bg-[#111111] border-[#B88719]/60 text-[#D9B86C]'
                              : 'bg-white border-[#BFA66A]/25 text-[#5A4209]/70 opacity-60 hover:opacity-100 hover:border-[#BFA66A]/50 hover:bg-[#FFFDF8]'
                          )}
                        >
                          <st.icon className="w-3 h-3" />
                          {st.label}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {/* Search input */}
                <div className="border-t border-[#BFA66A]/15 pt-3.5 flex items-center">
                  <div className="relative group w-full sm:w-80">
                    <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                    <input
                      value={dbSearch}
                      onChange={e => setDbSearch(e.target.value)}
                      placeholder="Search by name, type, author, languageâ€¦"
                      className="w-full pl-9 pr-4 py-2 bg-white border border-[#BFA66A]/25 rounded-xl text-xs text-[#111111] placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719]/40 transition-all"
                    />
                  </div>
                </div>
              </div>

              <div className="border border-[#BFA66A]/20 rounded-3xl overflow-hidden bg-white shadow-[0_4px_24px_rgba(184,135,25,0.02)]">
                {/* Table header */}
                <div className="px-6 py-4 bg-[#FFFDF8] border-b border-[#BFA66A]/15 flex items-center justify-between">
                  <span className="text-[10px] font-mono font-black text-[#5A4209]/80 uppercase tracking-widest">
                    Gold Layer Â· {selectedSources.size === DB_SOURCE_CHIPS.length
                      ? 'All Types'
                      : selectedSources.size === 0
                        ? 'None Selected'
                        : DB_SOURCE_CHIPS.filter(c => selectedSources.has(c.id)).map(c => c.label).join(', ')}
                    {dbSearch.trim() && (
                      <span className="ml-2 text-[#B88719]">Â· "{dbSearch.trim()}"</span>
                    )}
                  </span>
                  <span className="text-[9px] font-mono font-bold py-1 px-2 bg-slate-50 border border-slate-200 rounded-lg text-slate-600">
                    {pagedDocs.length} on page {dbPage}
                  </span>
                </div>

                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="bg-[#FFFDF8] text-[10px] font-black text-[#5A4209]/80 uppercase tracking-widest border-b border-[#BFA66A]/20">
                      <th className="px-6 py-5">Document Name</th>
                      <th className="px-6 py-5">Source Type</th>
                      <th className="px-6 py-5">Added Date</th>
                      <th className="px-6 py-5">Language</th>
                      <th className="px-8 py-5 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#BFA66A]/10 text-[#111111]">
                    {pagedDocs.length === 0 ? (
                      <tr>
                        <td colSpan={5} className="px-6 py-12 text-center text-xs text-slate-400 font-mono">
                          {dbSearch.trim()
                            ? `No results for "${dbSearch.trim()}"`
                            : selectedSources.size === 0
                              ? 'No source types selected.'
                              : 'No documents in the Gold layer.'}
                        </td>
                      </tr>
                    ) : pagedDocs.map(doc => (
                      <tr key={doc.id}
                        onClick={() => setSelectedDoc(doc)}
                        className="group bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/50 transition-colors cursor-pointer">
                        <td className="px-6 py-5">
                          <div className="flex items-center gap-4">
                            <div className="w-9 h-9 rounded-xl flex items-center justify-center border bg-[#FFFDF8] border-[#BFA66A]/30 text-[#B88719] shrink-0">
                              <Zap className="w-4 h-4 animate-pulse" />
                            </div>
                            <div className="min-w-0">
                              <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors uppercase tracking-tight truncate">
                                {doc.name}
                              </div>
                              <div className="text-[10px] text-slate-500 mt-0.5">Added by {doc.author || '—'}</div>
                            </div>
                          </div>
                        </td>
                        <td className="px-6 py-5 font-mono text-xs text-slate-600">{doc.type}</td>
                        <td className="px-6 py-5 font-mono text-xs text-slate-500">{doc.added_date}</td>
                        <td className="px-6 py-5 font-mono text-xs text-slate-600">{doc.language}</td>
                        <td className="px-8 py-5 text-right" onClick={e => e.stopPropagation()}>
                          <div className="flex items-center justify-end gap-2.5">
                            <button
                              onClick={() => setSelectedDoc(doc)}
                              className="px-2.5 py-1.5 bg-[#FFF9E8] hover:bg-[#B88719] hover:text-white border border-[#BFA66A]/45 hover:border-[#B88719]/40 rounded-lg text-[#8A5A00] font-bold text-[10px] uppercase tracking-wider transition-all inline-flex items-center gap-1 cursor-pointer">
                              <Eye className="w-3.5 h-3.5" />
                              Inspect
                            </button>
                            <div className="inline-flex items-center gap-1.5 px-3 py-1 bg-green-500/10 border border-green-500/20 rounded-lg text-green-700">
                              <CheckCircle2 className="w-3.5 h-3.5" />
                              <span className="text-[10px] font-mono font-black uppercase tracking-wider">Active</span>
                            </div>
                            {pendingDeleteDoc === doc.id ? (
                              <div className="flex items-center gap-1">
                                <button onClick={() => handleDeleteDoc(doc.id)} className="px-2 py-1 bg-red-600 text-white rounded-lg text-[9px] font-black uppercase tracking-wide cursor-pointer">Confirm</button>
                                <button onClick={() => setPendingDeleteDoc(null)} className="px-2 py-1 bg-slate-100 text-slate-600 rounded-lg text-[9px] font-black uppercase tracking-wide cursor-pointer">Cancel</button>
                              </div>
                            ) : (
                              <button onClick={() => setPendingDeleteDoc(doc.id)} className="p-1.5 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all cursor-pointer">
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* Pagination */}
                {(totalPages > 1 || dbHasNext) && (
                  <div className="px-6 py-4 border-t border-[#BFA66A]/15 bg-[#FFFDF8] flex items-center justify-between">
                    <span className="text-[10px] font-mono text-slate-500">
                      Page <span className="font-bold text-[#111111]">{dbPage}</span> of {totalPages}
                      {docsLoading && <span className="text-slate-400"> · loading…</span>}
                    </span>
                    <Pagination
                      page={dbPage}
                      totalPages={totalPages}
                      hasNext={dbHasNext}
                      canJump={dbCanJump}
                      onJump={dbGoTo}
                    />
                  </div>
                )}
              </div>
            </div>

          ) : selectedDoc.subType === 'Warehouse' ? (
            /* â"€â"€ WAREHOUSE CONFIGS VIEW (chunk-style split panel) â"€â"€ */
            <div className="space-y-4">
              {/* New-config drawer */}
              <DetailDrawer
                isOpen={newCfgDrawerOpen}
                onClose={() => { setNewCfgDrawerOpen(false); setNewCfgSelectedIds(new Set()); setNewCfgSchemaFilter('All'); }}
                title="New Config"
                subtitle={`Define a table set for ${selectedDoc.name}`}
                icon={Table2}
                size="standard"
                footer={
                  <div className="flex justify-end gap-2.5 w-full">
                    <button onClick={() => setNewCfgDrawerOpen(false)}
                      className="px-3.5 py-2 bg-white border border-slate-200 hover:bg-slate-50 rounded-xl text-xs font-bold text-slate-500 uppercase tracking-wide cursor-pointer">
                      Cancel
                    </button>
                    <button onClick={handleCreateConfig}
                      disabled={!newCfgName.trim() || newCfgSelectedIds.size === 0}
                      className={cn(
                        'px-5 py-2 rounded-xl text-xs font-black uppercase tracking-wider flex items-center gap-1.5 cursor-pointer transition-all',
                        newCfgName.trim() && newCfgSelectedIds.size > 0
                          ? 'bg-[#B88719] text-white hover:bg-[#8A5A00]'
                          : 'bg-slate-100 text-slate-400 border border-slate-200 cursor-not-allowed'
                      )}>
                      <Plus className="w-3.5 h-3.5" />
                      Create Config
                    </button>
                  </div>
                }>
                {(() => {
                  const available = availableTablesForNewConfig;
                  const schemas = ['All', ...Array.from(new Set(available.map(t => t.schema)))];
                  const visible = newCfgSchemaFilter === 'All' ? available : available.filter(t => t.schema === newCfgSchemaFilter);
                  return (
                    <div className="p-6 space-y-5">
                      <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-1.5">
                          <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">Config Name *</label>
                          <input value={newCfgName} onChange={e => setNewCfgName(e.target.value)} placeholder="e.g. Analytics Core"
                            className="w-full bg-white border border-[#BFA66A]/35 rounded-xl px-4 py-2.5 text-xs text-[#111111] placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-[#B88719] transition-all" />
                        </div>
                        <div className="flex items-center gap-2.5 px-4 py-2.5 bg-[#FFFDF8] border border-[#BFA66A]/30 rounded-xl">
                          <GitBranch className="w-3.5 h-3.5 text-[#B88719] shrink-0" />
                          <div>
                            <p className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono">Version (auto)</p>
                            <p className="text-sm font-mono font-black text-[#111111]">{autoNextCfgVer}</p>
                          </div>
                        </div>
                      </div>
                      <div className="space-y-1.5">
                        <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono block">Sync Schedule</label>
                        <select value={newCfgSchedule} onChange={e => setNewCfgSchedule(e.target.value)}
                          className="w-full bg-white border border-[#BFA66A]/35 rounded-xl px-4 py-2.5 text-xs text-[#111111] focus:outline-none focus:ring-1 focus:ring-[#B88719] cursor-pointer">
                          {['Every 4 hours','Every 6 hours','Every 12 hours','Daily','Weekly','Manual'].map(s => <option key={s}>{s}</option>)}
                        </select>
                      </div>
                      <div className="space-y-2">
                        <div className="flex items-center justify-between">
                          <label className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono">Select Tables *</label>
                          <span className="text-[10px] font-mono text-slate-500">{newCfgSelectedIds.size} selected</span>
                        </div>
                        {/* Schema filter */}
                        <div className="flex gap-1.5 flex-wrap">
                          {schemas.map(s => (
                            <button key={s} onClick={() => setNewCfgSchemaFilter(s)}
                              className={cn(
                                'px-2.5 py-1 rounded-lg border text-[10px] font-bold uppercase tracking-wide transition-all cursor-pointer',
                                newCfgSchemaFilter === s
                                  ? 'bg-[#111111] border-[#B88719]/50 text-[#D9B86C]'
                                  : 'bg-white border-[#BFA66A]/25 text-[#5A4209]/70 hover:border-[#BFA66A]/50'
                              )}>{s}</button>
                          ))}
                        </div>
                        {/* Table list */}
                        <div className="border border-[#BFA66A]/20 rounded-2xl overflow-hidden">
                          {visible.map((tbl, idx) => {
                            const checked = newCfgSelectedIds.has(tbl.id);
                            return (
                              <label key={tbl.id}
                                className={cn(
                                  'flex items-center gap-3 px-4 py-3 cursor-pointer transition-colors',
                                  idx > 0 && 'border-t border-[#BFA66A]/10',
                                  checked ? 'bg-[#FFF9E8]' : 'bg-white hover:bg-[#FFFDF8]'
                                )}>
                                <input type="checkbox" checked={checked}
                                  onChange={() => {
                                    setNewCfgSelectedIds(prev => {
                                      const next = new Set(prev);
                                      next.has(tbl.id) ? next.delete(tbl.id) : next.add(tbl.id);
                                      return next;
                                    });
                                  }}
                                  className="w-3.5 h-3.5 accent-[#B88719] cursor-pointer shrink-0" />
                                <span className="px-1.5 py-0.5 bg-slate-100 border border-slate-200 rounded text-[9px] font-mono font-bold text-slate-500 shrink-0">{tbl.schema}</span>
                                <span className="text-xs font-mono font-bold text-[#111111] flex-1 truncate">{tbl.tableName}</span>
                                <span className="text-[10px] font-mono text-slate-400 shrink-0">{formatRows(tbl.rowCount)} rows</span>
                                <span className="text-[10px] font-mono text-slate-400 shrink-0">{tbl.columns} cols</span>
                              </label>
                            );
                          })}
                        </div>
                      </div>
                      <div className="p-3.5 bg-[#FFF9E8] border border-[#BFA66A]/30 rounded-xl flex gap-3">
                        <Info className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
                        <p className="text-[11px] text-[#5A4209] leading-relaxed">
                          The new config will be created as <strong>Draft</strong>. Use <strong>Set Active</strong> to promote it as the live sync config.
                        </p>
                      </div>
                    </div>
                  );
                })()}
              </DetailDrawer>

              {/* Back header */}
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setSelectedDoc(null)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#BFA66A]/35 hover:bg-[#FFF9E8] rounded-xl text-xs font-bold text-[#8A5A00] transition-all cursor-pointer shadow-2xs">
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Database
                </button>
                <span className="text-[10px] font-mono text-slate-400">Â·</span>
                <span className="text-xs font-bold text-[#111111] uppercase truncate max-w-xs">{selectedDoc.name}</span>
                <span className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1 bg-green-50 border border-green-200 rounded-lg text-green-700 text-[10px] font-bold">
                  <Link className="w-3.5 h-3.5" />
                  Connected Â· {selectedDoc.type}
                </span>
              </div>

              {warehouseConfigsLoading ? (
                <div className="flex items-center justify-center py-20 text-slate-400 text-sm">Loading configsâ€¦</div>
              ) : (
                <div className="flex gap-6 overflow-hidden" style={{ height: 'calc(100vh - 340px)', minHeight: 520 }}>

                  {/* LEFT: config list */}
                  <div className="w-72 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                    <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8] shrink-0">
                      <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-1.5">
                        <Layers3 className="w-4 h-4 text-[#B88719]" />
                        Configs
                      </h4>
                      <button onClick={() => setNewCfgDrawerOpen(true)}
                        className="flex items-center gap-1 px-2.5 py-1.5 bg-[#FFF9E8] border border-[#BFA66A]/40 hover:bg-[#B88719] hover:text-white text-[#8A5A00] rounded-lg text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer">
                        <Plus className="w-3 h-3" />
                        New
                      </button>
                    </div>
                    <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar">
                      {warehouseConfigs.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-32 gap-2 text-center text-slate-400">
                          <Layers3 className="w-6 h-6 opacity-30" />
                          <p className="text-[11px] font-mono">No configs yet</p>
                          <p className="text-[10px] text-slate-300">Click New to create one</p>
                        </div>
                      )}
                      {warehouseConfigs.map(cfg => (
                        <div key={cfg.id}
                          className={cn(
                            'p-4 border rounded-xl transition-all relative group',
                            cfg.id === selectedConfig?.id
                              ? 'border-[#B88719] bg-[#FFF9E8] shadow-xs'
                              : 'border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]'
                          )}>
                          <div className="cursor-pointer" onClick={() => setSelectedConfig(cfg)}>
                            <div className="flex items-center justify-between mb-1.5">
                              <span className={cn(
                                'text-[9px] font-black uppercase px-1.5 py-0.5 rounded tracking-wide',
                                cfg.status === 'Active' ? 'text-green-800 bg-green-500/10' : 'text-slate-500 bg-slate-100'
                              )}>{cfg.status}</span>
                              <span className="text-[9px] font-mono text-slate-400">{cfg.tables.length} tables</span>
                            </div>
                            <div className="text-xs text-[#111111] font-bold truncate uppercase tracking-tight">{cfg.name}</div>
                            <div className="text-[10px] text-slate-500 mt-0.5 font-mono">{cfg.version} Â· {cfg.syncSchedule}</div>
                          </div>
                          {cfg.status !== 'Active' && (
                            pendingDeleteConfig === cfg.id ? (
                              <div className="flex gap-1 mt-2">
                                <button onClick={() => handleDeleteConfig(cfg.id)} className="flex-1 py-1 bg-red-600 text-white rounded-lg text-[9px] font-black uppercase cursor-pointer">Confirm Delete</button>
                                <button onClick={() => setPendingDeleteConfig(null)} className="px-2 py-1 bg-slate-100 text-slate-600 rounded-lg text-[9px] font-black cursor-pointer">âœ•</button>
                              </div>
                            ) : (
                              <button onClick={e => { e.stopPropagation(); setPendingDeleteConfig(cfg.id); }}
                                className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all cursor-pointer">
                                <Trash2 className="w-3 h-3" />
                              </button>
                            )
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* RIGHT: config detail */}
                  {selectedConfig ? (
                    <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl bg-white flex flex-col overflow-hidden shadow-xs">

                      {/* Config header */}
                      <div className="px-6 pt-5 pb-4 border-b border-[#BFA66A]/20 shrink-0 bg-[#FFFDF8]">
                        <div className="flex items-start justify-between">
                          <div>
                            <span className="text-[10px] font-mono text-[#8A5A00] font-black">{selectedConfig.id} Â· Table Config</span>
                            <h3 className="text-base font-bold text-[#111111] font-display">{selectedConfig.name}</h3>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span className="text-[9px] font-black uppercase text-[#B88719] bg-[#FFF9E9] border border-[#BFA66A]/30 px-2.5 py-1 rounded-lg font-mono">
                              {selectedConfig.version}
                            </span>
                            {selectedConfig.status !== 'Active' && (
                              <button onClick={() => activateConfig(selectedConfig)}
                                className="flex items-center gap-1 px-3 py-1.5 bg-[#FFF9E8] border border-[#B88719]/40 hover:bg-[#B88719] hover:text-white text-[#8A5A00] rounded-lg text-[10px] font-black uppercase tracking-wide transition-all cursor-pointer">
                                <Zap className="w-3 h-3" />
                                Set Active
                              </button>
                            )}
                            {selectedConfig.status === 'Active' && (
                              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-green-50 border border-green-200 rounded-lg text-green-700 text-[10px] font-bold">
                                <CheckCircle2 className="w-3 h-3" /> Active
                              </span>
                            )}
                          </div>
                        </div>

                        {/* Connection mini-row */}
                        {(() => {
                          const conn = getWarehouseConnection(selectedDoc.type, selectedConfig.connection);
                          const fields = [
                            conn.platform,
                            conn.host,
                            conn.database ?? conn.catalog ?? '',
                            conn.warehouse ?? conn.cluster ?? '',
                            conn.role ?? '',
                            `Sync: ${selectedConfig.syncSchedule}`,
                          ].filter(Boolean);
                          return (
                            <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1">
                              {fields.map((f, i) => (
                                <span key={i} className="text-[10px] font-mono text-slate-500">{f}</span>
                              ))}
                            </div>
                          );
                        })()}
                      </div>

                      {/* Table list body */}
                      <div className="flex-1 overflow-y-auto custom-scrollbar">
                        <div className="px-6 py-3 border-b border-slate-100 flex items-center justify-between bg-[#FEFEFE] shrink-0">
                          <span className="text-[9px] font-black uppercase tracking-widest text-[#5A4209]/70 font-mono flex items-center gap-1.5">
                            <Table2 className="w-3.5 h-3.5 text-[#B88719]" />
                            Table Set Â· {selectedConfig.tables.length} tables
                          </span>
                          <span className="text-[9px] font-mono text-slate-400">{selectedConfig.createdAt}</span>
                        </div>
                        <table className="w-full text-left border-collapse">
                          <thead>
                            <tr className="bg-[#FFFDF8] text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest border-b border-[#BFA66A]/15">
                              <th className="px-6 py-3">Schema</th>
                              <th className="px-6 py-3">Table Name</th>
                              <th className="px-6 py-3 text-right">Rows</th>
                              <th className="px-6 py-3 text-right">Columns</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-[#BFA66A]/8">
                            {selectedConfig.tables.map(tbl => (
                              <tr key={tbl.id} className="group hover:bg-[#FFFDF8]/60 transition-colors">
                                <td className="px-6 py-4">
                                  <span className="inline-block px-2 py-0.5 bg-slate-100 border border-slate-200 rounded text-[10px] font-mono font-bold text-slate-600">{tbl.schema}</span>
                                </td>
                                <td className="px-6 py-4 font-mono text-xs font-bold text-[#111111]">{tbl.tableName}</td>
                                <td className="px-6 py-4 text-right font-mono text-xs font-bold text-[#2A2A2A]">{formatRows(tbl.rowCount)}</td>
                                <td className="px-6 py-4 text-right font-mono text-xs text-slate-500">{tbl.columns}</td>
                                <td className="px-4 py-4 text-right" onClick={e => e.stopPropagation()}>
                                  {pendingDeleteTable === tbl.id ? (
                                    <div className="flex items-center justify-end gap-1">
                                      <button onClick={() => handleDeleteTable(tbl.id)} className="px-2 py-0.5 bg-red-600 text-white rounded text-[9px] font-black cursor-pointer">Confirm</button>
                                      <button onClick={() => setPendingDeleteTable(null)} className="px-2 py-0.5 bg-slate-100 text-slate-600 rounded text-[9px] font-black cursor-pointer">âœ•</button>
                                    </div>
                                  ) : (
                                    <button onClick={() => setPendingDeleteTable(tbl.id)} className="opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all cursor-pointer">
                                      <Trash2 className="w-3 h-3" />
                                    </button>
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </div>

          ) : (
            /* â"€â"€ CHUNKS VIEW â"€â"€ */
            <div className="space-y-4">
              {/* Back header */}
              <div className="flex items-center gap-3">
                <button
                  onClick={() => setSelectedDoc(null)}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#BFA66A]/35 hover:bg-[#FFF9E8] rounded-xl text-xs font-bold text-[#8A5A00] transition-all cursor-pointer shadow-2xs">
                  <ArrowLeft className="w-3.5 h-3.5" />
                  Back to Database
                </button>
                <span className="text-[10px] font-mono text-slate-400">Â·</span>
                <span className="text-xs font-bold text-[#111111] uppercase truncate max-w-xs">{selectedDoc.name}</span>
                <span className="ml-auto inline-flex items-center gap-1.5 px-2.5 py-1 bg-green-50 border border-green-200 rounded-lg text-green-700 text-[10px] font-bold">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  Gold Layer Â· Active
                </span>
              </div>

              {chunksLoading ? (
                <div className="flex items-center justify-center py-20 text-slate-400 text-sm">Loading chunksâ€¦</div>
              ) : (
                /* Split panel */
                <div className="flex gap-6 overflow-hidden" style={{ height: 'calc(100vh - 340px)', minHeight: 520 }}>

                  {/* LEFT: chunk list */}
                  <div className="w-80 border border-[#BFA66A]/30 rounded-3xl flex flex-col bg-white overflow-hidden shrink-0 shadow-xs">
                    <div className="p-5 border-b border-[#BFA66A]/20 flex justify-between items-center bg-[#FFFDF8]">
                      <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest flex items-center gap-1.5">
                        <Layers3 className="w-4 h-4 text-[#B88719]" />
                        Active Chunks
                      </h4>
                      <span className="text-[10px] font-mono text-[#8A5A00] font-black bg-[#FFF9E8] border border-[#BFA66A]/30 px-2 py-0.5 rounded">
                        {chunks.length} Chunks
                      </span>
                    </div>
                    <div className="flex-1 overflow-y-auto p-3 space-y-2 custom-scrollbar bg-[#FCFBF7]/30">
                      {chunks.length === 0 && (
                        <div className="flex flex-col items-center justify-center h-32 gap-2 text-center text-slate-400">
                          <Layers3 className="w-6 h-6 opacity-30" />
                          <p className="text-[11px] font-mono">No chunks for this document</p>
                        </div>
                      )}
                      {chunks.map(chunk => (
                        <div key={chunk.id}
                          className={cn(
                            'p-4 border rounded-xl transition-all group text-left relative',
                            chunk.id === selectedChunk?.id
                              ? 'border-[#B88719] bg-[#FFF9E8] shadow-xs'
                              : 'border-[#BFA66A]/20 bg-white hover:border-[#BFA66A]/40 hover:bg-[#FFFDF8]'
                          )}>
                          <div className="cursor-pointer" onClick={() => handleChunkChange(chunk)}>
                            <div className="flex justify-between items-center mb-1 text-[10px] font-mono">
                              <span className={chunk.id === selectedChunk?.id ? 'text-[#8A5A00] font-black animate-pulse' : 'text-slate-500'}>
                                {chunk.id}
                              </span>
                              <span className="text-[9px] text-slate-400">{chunk.versions.length}v</span>
                            </div>
                            <div className="text-xs text-[#111111] font-bold truncate group-hover:text-[#B88719] transition-colors uppercase tracking-tight">
                              {chunk.title}
                            </div>
                            <p className="text-[10px] text-slate-600 truncate mt-1">{chunk.text}</p>
                          </div>
                          {pendingDeleteChunk === chunk.id ? (
                            <div className="flex items-center gap-1 mt-2">
                              <button onClick={() => handleDeleteChunk(chunk.id)} className="flex-1 py-1 bg-red-600 text-white rounded-lg text-[9px] font-black uppercase tracking-wide cursor-pointer">Confirm Delete</button>
                              <button onClick={() => setPendingDeleteChunk(null)} className="px-2 py-1 bg-slate-100 text-slate-600 rounded-lg text-[9px] font-black cursor-pointer">âœ•</button>
                            </div>
                          ) : (
                            <button
                              onClick={e => { e.stopPropagation(); setPendingDeleteChunk(chunk.id); }}
                              className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 p-1 text-slate-400 hover:text-red-500 hover:bg-red-50 rounded-lg transition-all cursor-pointer">
                              <Trash2 className="w-3 h-3" />
                            </button>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* RIGHT: chunk detail */}
                  {selectedChunk && selectedVersion ? (
                    <div className="flex-1 border border-[#BFA66A]/30 rounded-3xl bg-white flex flex-col overflow-hidden shadow-xs">

                      {/* Chunk header */}
                      <div className="px-6 pt-5 pb-4 border-b border-[#BFA66A]/20 shrink-0 flex justify-between items-center bg-[#FFFDF8]">
                        <div>
                          <span className="text-[10px] font-mono text-[#8A5A00] font-black">{selectedChunk.id} Â· Semantic Node</span>
                          <h3 className="text-base font-bold text-[#111111] font-display">{selectedChunk.title}</h3>
                        </div>
                        <span className="text-[9px] font-black uppercase text-[#B88719] bg-[#FFF9E9] border border-[#BFA66A]/30 px-2.5 py-1 rounded-lg">
                          Multi-Version Gold Layer
                        </span>
                      </div>

                      {/* Version tabs row */}
                      <div className="px-5 py-4 border-b border-slate-100 bg-[#FFFDF8]/40 shrink-0">
                        <div className="flex justify-between items-center mb-2">
                          <span className="text-[9px] font-black text-[#5A4209]/60 uppercase tracking-widest flex items-center gap-1.5">
                            <Clock className="w-3 h-3 text-[#B88719]" />
                            Select Revision Node
                          </span>
                          <button
                            onClick={() => setNewVerDrawerOpen(true)}
                            className="flex items-center gap-1 px-2.5 py-1.5 bg-[#FFF9E8] border border-[#BFA66A]/40 hover:bg-[#B88719] hover:text-white text-[#8A5A00] rounded-lg text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer">
                            <Plus className="w-3 h-3" />
                            New Version
                          </button>
                        </div>
                        <div className="flex gap-3 overflow-x-auto pb-1.5 custom-scrollbar flex-nowrap">
                          {selectedChunk.versions.map(ver => (
                            <div key={ver.version_number}
                              className={cn(
                                'flex items-center gap-1 rounded-xl border transition-all shrink-0 shadow-2xs',
                                ver.version_number === selectedVersion.version_number
                                  ? 'border-[#B88719] bg-[#FFF9E8]'
                                  : 'border-[#BFA66A]/25 bg-white hover:border-[#BFA66A]/50 hover:bg-[#FCFBF7]'
                              )}>
                            <button onClick={() => setSelectedVersion(ver)}
                              className={cn(
                                'px-4 py-2.5 transition-all cursor-pointer text-left flex items-center gap-3 min-w-[130px]',
                                ver.version_number === selectedVersion.version_number ? 'text-[#111111]' : 'text-slate-700'
                              )}>
                              <div className={cn(
                                'w-6 h-6 rounded-lg flex items-center justify-center border',
                                ver.version_number === selectedVersion.version_number
                                  ? 'bg-[#B88719]/10 border-[#B88719]/20 text-[#8A5A00]'
                                  : 'bg-slate-50 border-slate-200 text-slate-400'
                              )}>
                                <Clock className="w-3.5 h-3.5" />
                              </div>
                              <div>
                                <div className="text-xs font-mono font-black">{ver.version_number}</div>
                                <span className={cn(
                                  'text-[8px] font-black uppercase px-1 rounded-sm tracking-wide mt-0.5 inline-block',
                                  ver.status === 'Active' ? 'text-green-800 bg-green-500/10' : 'text-slate-500 bg-slate-100'
                                )}>
                                  {ver.status}
                                </span>
                              </div>
                            </button>
                            {ver.status !== 'Active' && (
                              pendingDeleteVersion === ver.version_number ? (
                                <div className="flex gap-1 pr-2">
                                  <button onClick={() => handleDeleteVersion(ver.version_number)} className="px-1.5 py-0.5 bg-red-600 text-white rounded text-[8px] font-black cursor-pointer">OK</button>
                                  <button onClick={() => setPendingDeleteVersion(null)} className="px-1.5 py-0.5 bg-slate-100 text-slate-600 rounded text-[8px] font-black cursor-pointer">âœ•</button>
                                </div>
                              ) : (
                                <button onClick={() => setPendingDeleteVersion(ver.version_number)} className="pr-2 text-slate-300 hover:text-red-500 transition-colors cursor-pointer">
                                  <Trash2 className="w-3 h-3" />
                                </button>
                              )
                            )}
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Version detail body */}
                      <div className="flex-1 overflow-y-auto p-6 space-y-6 custom-scrollbar">

                        {/* Revision parameters */}
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
                              <div className="flex items-center gap-2 mt-0.5 flex-wrap">
                                <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', selectedVersion.status === 'Active' ? 'bg-green-600' : 'bg-slate-400')} />
                                <span className={cn('text-xs font-mono font-black', selectedVersion.status === 'Active' ? 'text-green-700' : 'text-slate-500')}>
                                  {selectedVersion.status === 'Active' ? 'ACTIVE RETRIEVER' : 'STAGED RECORD'}
                                </span>
                                {selectedVersion.status === 'Inactive' && (
                                  <button
                                    onClick={() => activateVersion(selectedVersion)}
                                    className="flex items-center gap-1 px-2 py-0.5 bg-[#FFF9E8] border border-[#B88719]/40 hover:bg-[#B88719] hover:text-white text-[#8A5A00] rounded-lg text-[9px] font-black uppercase tracking-wide transition-all cursor-pointer">
                                    <Zap className="w-2.5 h-2.5" />
                                    Set Active
                                  </button>
                                )}
                              </div>
                            </div>
                            <div className="flex flex-col">
                              <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-0.5">Embedding Model</span>
                              <span className="text-xs text-slate-600 font-mono truncate">{selectedVersion.embedding_models}</span>
                            </div>
                            {selectedVersion.entities.length > 0 && (
                              <div className="flex flex-col md:col-span-2 lg:col-span-3">
                                <span className="text-[9px] text-slate-500 font-bold uppercase tracking-wider mb-2">Identified Entities Map</span>
                                <div className="flex gap-2 overflow-x-auto pb-1 flex-nowrap">
                                  {selectedVersion.entities.map((ent, i) => (
                                    <span key={i} className="bg-white border border-[#BFA66A]/25 text-[#5A4209] text-[11px] font-mono px-3 py-1.5 rounded-xl shadow-2xs shrink-0">
                                      {ent}
                                    </span>
                                  ))}
                                </div>
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Version text */}
                        <div className="p-6 bg-white border border-[#BFA66A]/30 rounded-2xl shadow-xs">
                          <span className="text-[9px] font-black text-[#5A4209]/70 uppercase tracking-widest block mb-3 border-b border-dashed border-[#BFA66A]/20 pb-1.5">
                            Version Text Data
                          </span>
                          <p className="text-[#2A2A2A] text-xs leading-relaxed font-mono whitespace-pre-wrap">{selectedVersion.text}</p>
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• QDRANT TAB â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
      {activeTab === 'QDRANT' && (
        <>
          {/* â"€â"€ Collection detail drawer â"€â"€ */}
          <DetailDrawer
            isOpen={!!selectedCollection}
            onClose={() => { setSelectedCollection(null); setQdrantQuery(''); setQdrantPoints([]); }}
            title={selectedCollection?.name ?? ''}
            subtitle={`${selectedCollection?.id ?? ''} Â· ${selectedCollection?.embedding_model ?? ''}`}
            icon={Layers}
            size="xwide"
            persistKey="qdrant-collection-detail"
            fixedHeight
          >
            {selectedCollection && (
              <div className="flex h-full gap-0 divide-x divide-[#BFA66A]/15">

                {/* â"€â"€ LEFT: Collection metadata â"€â"€ */}
                <div className="w-[280px] shrink-0 p-5 space-y-5 overflow-y-auto">
                  <p className="text-[10px] font-black uppercase tracking-widest text-[#6B6B6B]">Collection Info</p>

                  {[
                    { label: 'Collection ID',    value: selectedCollection.id },
                    { label: 'Embedding Model',  value: selectedCollection.embedding_model ?? 'â€"' },
                    { label: 'Dimensions',       value: String(selectedCollection.dimensions) },
                    { label: 'Distance Metric',  value: selectedCollection.distance },
                    { label: 'Vector Points (your tenant)', value: selectedCollection.points.toLocaleString() },
                    { label: 'Vector Points (all tenants)', value: (selectedCollection.total_points ?? selectedCollection.points).toLocaleString() },
                  ].map(row => (
                    <div key={row.label}>
                      <span className="text-[9px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-0.5">{row.label}</span>
                      <span className="text-xs font-bold font-mono text-[#2A2A2A] break-all">{row.value}</span>
                    </div>
                  ))}

                  <div>
                    <span className="text-[9px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-1">Indexed</span>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                        <div className={cn('h-full rounded-full', selectedCollection.indexed === 100 ? 'bg-green-500' : 'bg-[#B88719]')} style={{ width: `${selectedCollection.indexed}%` }} />
                      </div>
                      <span className="text-[10px] font-bold font-mono text-slate-600 shrink-0">{selectedCollection.indexed}%</span>
                    </div>
                  </div>

                  <div className="pt-2 space-y-2.5">
                    <span className={cn(
                      'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[10px] font-bold border',
                      selectedCollection.active
                        ? 'bg-green-50 border-green-300 text-green-700'
                        : 'bg-slate-100 border-slate-200 text-slate-500',
                    )}>
                      <span className={cn('w-1.5 h-1.5 rounded-full', selectedCollection.active ? 'bg-green-500 animate-pulse' : 'bg-slate-400')} />
                      {selectedCollection.active ? 'Active' : 'Inactive'}
                    </span>
                    <button
                      onClick={handleToggleActive}
                      disabled={qdrantToggling}
                      className={cn(
                        'w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider border transition-all disabled:opacity-40 disabled:cursor-not-allowed',
                        selectedCollection.active
                          ? 'bg-white border-red-200 text-red-600 hover:bg-red-50'
                          : 'bg-white border-green-300 text-green-700 hover:bg-green-50',
                      )}
                    >
                      {qdrantToggling
                        ? <Loader2 className="w-3 h-3 animate-spin" />
                        : <span className={cn('w-1.5 h-1.5 rounded-full', selectedCollection.active ? 'bg-red-500' : 'bg-green-500')} />}
                      {selectedCollection.active ? 'Set Inactive' : 'Set Active'}
                    </button>
                  </div>
                </div>

                {/* â"€â"€ RIGHT: Natural-language search â"€â"€ */}
                <div className="flex-1 flex flex-col min-w-0">
                  {/* Query bar */}
                  <div className={cn('px-5 py-4 border-b border-[#BFA66A]/15 shrink-0', selectedCollection.active ? 'bg-[#FFFDF8]' : 'bg-slate-50')}>
                    <div className="flex items-center justify-between mb-2">
                      <p className="text-[10px] font-black uppercase tracking-widest text-[#6B6B6B]">
                        Semantic Search &middot; Top {qdrantTopK} Points
                      </p>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[9px] font-mono text-slate-400 uppercase tracking-wider">Top K</span>
                        <input
                          type="number"
                          min={1}
                          max={20}
                          value={qdrantTopK}
                          onChange={e => setQdrantTopK(Math.min(20, Math.max(1, Number(e.target.value) || 1)))}
                          disabled={!selectedCollection.active}
                          className={cn(
                            'w-14 border rounded-lg px-2 py-0.5 text-xs font-mono text-center focus:outline-none transition-all',
                            selectedCollection.active
                              ? 'bg-white border-[#BFA66A]/30 text-[#2A2A2A] focus:border-[#B88719]'
                              : 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed',
                          )}
                        />
                      </div>
                    </div>
                    <div className="flex gap-2">
                      <input
                        type="text"
                        value={qdrantQuery}
                        onChange={e => selectedCollection.active && setQdrantQuery(e.target.value)}
                        onKeyDown={e => e.key === 'Enter' && selectedCollection.active && handleQdrantSearch()}
                        placeholder={selectedCollection.active ? 'Ask a natural-language question…' : 'Collection is inactive — activate to query'}
                        disabled={!selectedCollection.active}
                        className={cn(
                          'flex-1 border rounded-xl px-4 py-2 text-xs font-mono placeholder-slate-400 focus:outline-none transition-all',
                          selectedCollection.active
                            ? 'bg-white border-[#BFA66A]/30 text-[#2A2A2A] focus:border-[#B88719]'
                            : 'bg-slate-100 border-slate-200 text-slate-400 cursor-not-allowed',
                        )}
                      />
                      <button
                        onClick={handleQdrantSearch}
                        disabled={!selectedCollection.active || qdrantSearching || !qdrantQuery.trim()}
                        className="flex items-center gap-1.5 px-4 py-2 bg-[#B88719] text-white rounded-xl text-xs font-black uppercase tracking-wider hover:bg-[#8A5A00] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
                      >
                        {qdrantSearching
                          ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          : <SendHorizonal className="w-3.5 h-3.5" />}
                        Search
                      </button>
                    </div>
                  </div>

                  {/* Results */}
                  <div className="flex-1 overflow-y-auto p-5 space-y-4">
                    {!selectedCollection.active && (
                      <div className="flex flex-col items-center justify-center h-full gap-3 text-center text-slate-400">
                        <AlertCircle className="w-10 h-10 opacity-30" />
                        <p className="text-xs font-medium">This collection is inactive.<br />Set it to Active to enable querying.</p>
                      </div>
                    )}
                    {selectedCollection.active && qdrantPoints.length === 0 && !qdrantSearching && (
                      <div className="flex flex-col items-center justify-center h-full gap-3 text-center text-slate-400">
                        <MessageSquare className="w-10 h-10 opacity-30" />
                        <p className="text-xs font-medium">Enter a query above to retrieve the top {qdrantTopK} matching points from this collection.</p>
                      </div>
                    )}
                    {selectedCollection.active && qdrantSearching && (
                      <div className="flex items-center justify-center h-full gap-2 text-[#B88719]">
                        <Loader2 className="w-5 h-5 animate-spin" />
                        <span className="text-xs font-medium">Searchingâ€¦</span>
                      </div>
                    )}
                    {selectedCollection.active && !qdrantSearching && qdrantPoints.map((pt, idx) => (
                      <div key={pt.point_id} className="border border-[#BFA66A]/20 rounded-2xl overflow-hidden bg-white">
                        {/* Point header */}
                        <div className="px-4 py-2.5 bg-[#FFFDF8] border-b border-[#BFA66A]/15 flex items-center justify-between">
                          <div className="flex items-center gap-2">
                            <span className="w-5 h-5 rounded-full bg-[#BFA66A]/15 text-[#B88719] text-[9px] font-black flex items-center justify-center shrink-0">
                              {idx + 1}
                            </span>
                            <span className="text-[10px] font-mono font-bold text-[#2A2A2A]">{pt.point_id}</span>
                          </div>
                          <span className="text-[9px] font-mono font-bold text-slate-400">
                            score <span className="text-[#B88719]">{pt.score.toFixed(2)}</span>
                          </span>
                        </div>
                        {/* Point body */}
                        <div className="p-4 space-y-3">
                          {/* Summary */}
                          <div>
                            <div className="flex items-center gap-1 mb-1">
                              <Hash className="w-3 h-3 text-slate-400" />
                              <span className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-400">Summary</span>
                            </div>
                            <p className="text-xs text-[#2A2A2A] leading-relaxed font-mono">{pt.summary}</p>
                          </div>
                          {/* Entities */}
                          <div>
                            <div className="flex items-center gap-1 mb-1.5">
                              <Cpu className="w-3 h-3 text-slate-400" />
                              <span className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-400">Entities</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {pt.entities.map(e => (
                                <span key={e} className="px-2 py-0.5 bg-blue-50 border border-blue-200 text-blue-700 rounded-full text-[10px] font-bold">{e}</span>
                              ))}
                            </div>
                          </div>
                          {/* Intent */}
                          <div>
                            <div className="flex items-center gap-1 mb-1.5">
                              <List className="w-3 h-3 text-slate-400" />
                              <span className="text-[9px] font-mono font-bold uppercase tracking-wider text-slate-400">Intent</span>
                            </div>
                            <div className="flex flex-wrap gap-1.5">
                              {pt.intent.map(i => (
                                <span key={i} className="px-2 py-0.5 bg-[#FFF9E8] border border-[#BFA66A]/40 text-[#8A5A00] rounded-full text-[10px] font-bold">{i}</span>
                              ))}
                            </div>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>

              </div>
            )}
          </DetailDrawer>

          {/* ── Collection grid ── */}
          <div className="space-y-4">
            {qdrantCollections.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-20 gap-3 text-center text-slate-400">
                <Layers className="w-10 h-10 opacity-30" />
                <p className="text-sm font-medium">No Qdrant collections registered</p>
                <p className="text-xs text-slate-400">Register a Qdrant connection to see collections here.</p>
              </div>
            ) : (
              <>
            <p className="text-sm text-slate-500 font-medium">
              {qdrantCollections.filter(c => c.active).length} active · {qdrantCollections.length} total collections — click a card to inspect
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {qdrantCollections.map(col => (
                <button
                  key={col.id}
                  onClick={() => { setSelectedCollection(col); setQdrantQuery(''); setQdrantPoints([]); }}
                  className={cn(
                    'border rounded-3xl overflow-hidden transition-all text-left cursor-pointer group',
                    col.active
                      ? 'border-[#BFA66A]/30 bg-white shadow-[0_4px_24px_rgba(184,135,25,0.04)] hover:shadow-[0_6px_30px_rgba(184,135,25,0.10)] hover:border-[#BFA66A]/60'
                      : 'border-slate-200 bg-slate-50/60 hover:border-slate-300',
                  )}
                >
                  <div className={cn('px-5 py-4 border-b flex items-center justify-between', col.active ? 'bg-[#FFFDF8] border-[#BFA66A]/15' : 'bg-slate-50 border-slate-200')}>
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className={cn('p-1.5 rounded-lg border shrink-0', col.active ? 'bg-[#FFF9E8] border-[#BFA66A]/40' : 'bg-white border-slate-200')}>
                        <Layers className={cn('w-4 h-4', col.active ? 'text-[#B88719]' : 'text-slate-400')} />
                      </div>
                      <div className="min-w-0">
                        <h3 className="text-xs font-bold font-mono text-[#111111] truncate group-hover:text-[#B88719] transition-colors">{col.name}</h3>
                        <p className="text-[9px] text-slate-400 font-mono mt-0.5">{col.id}</p>
                      </div>
                    </div>
                    <span className={cn('shrink-0 inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-bold border',
                      col.active ? 'bg-green-50 border-green-300 text-green-700' : 'bg-slate-100 border-slate-200 text-slate-500')}>
                      <span className={cn('w-1.5 h-1.5 rounded-full', col.active ? 'bg-green-500 animate-pulse' : 'bg-slate-400')} />
                      {col.active ? 'Active' : 'Inactive'}
                    </span>
                  </div>
                  <div className="p-5 grid grid-cols-2 gap-5">
                    <div>
                      <span className="text-[8px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-0.5">Vector Points</span>
                      <span className="text-2xl font-bold font-mono text-[#111111]">{col.points.toLocaleString()}</span>
                    </div>
                    <div>
                      <span className="text-[8px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-0.5">Dimensions</span>
                      <span className="text-2xl font-bold font-mono text-[#111111]">{col.dimensions}</span>
                    </div>
                    <div>
                      <span className="text-[8px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-0.5">Embedding Model</span>
                      <span className="text-[10px] font-bold font-mono text-slate-600">{col.embedding_model ?? 'â€"'}</span>
                    </div>
                    <div>
                      <span className="text-[8px] font-mono text-slate-400 font-bold uppercase tracking-wider block mb-1">Indexed</span>
                      <div className="flex items-center gap-2">
                        <div className="flex-1 h-1.5 bg-slate-100 rounded-full overflow-hidden">
                          <div className={cn('h-full rounded-full', col.indexed === 100 ? 'bg-green-500' : 'bg-[#B88719]')} style={{ width: `${col.indexed}%` }} />
                        </div>
                        <span className="text-[10px] font-bold font-mono text-slate-600 shrink-0">{col.indexed}%</span>
                      </div>
                    </div>
                  </div>
                </button>
              ))}
            </div>
              </>
            )}
          </div>
        </>
      )}

      {/* â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• NEO4J TAB â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â• */}
      {activeTab === 'NEO4J' && (
        <div className="flex gap-5" style={{ height: 580 }}>

          {/* â"€â"€ LEFT: Query Builder â"€â"€ */}
          <div className="w-[340px] shrink-0 border border-[#BFA66A]/25 rounded-3xl overflow-hidden bg-white shadow-sm flex flex-col">
            {/* Header */}
            <div className="px-5 py-4 bg-[#FFFDF8] border-b border-[#BFA66A]/15 flex items-center justify-between shrink-0">
              <div className="flex items-center gap-2.5">
                <div className="p-1.5 bg-[#FFF9E8] border border-[#BFA66A]/40 rounded-lg">
                  <Code className="w-4 h-4 text-[#B88719]" />
                </div>
                <div>
                  <h3 className="text-xs font-bold font-mono uppercase tracking-widest text-[#2A2A2A]">Query Builder</h3>
                  <p className="text-[10px] text-slate-500">Generates Cypher path</p>
                </div>
              </div>
              <button
                onClick={async () => {
                  const generated = buildCypher(startNode, steps);
                  setCypher(generated);
                  setQueryRan(true);
                  setQueryRunning(true);

                  // 1) Ask the backend to run the Cypher and return the matched subgraph.
                  let sub: { nodes?: GraphNode[]; edges?: GraphEdge[] } | null = null;
                  try {
                    sub = await apiMutate<{ nodes?: GraphNode[]; edges?: GraphEdge[] }>(
                      'POST', '/api/knowledge/neo4j/query', { cypher: generated },
                    );
                  } catch { /* Neo4j offline — fall back to a local traversal below */ }
                  finally { setQueryRunning(false); }

                  // 2) Prefer the backend subgraph; otherwise traverse the loaded graph
                  //    from the start node so the canvas always reflects the query.
                  let nodes = sub?.nodes ?? [];
                  let edges = sub?.edges ?? [];
                  if (nodes.length === 0 && rawGraphNodes.length > 0) {
                    const local = subgraphByType(rawGraphNodes, rawGraphEdges, startNode, Math.max(steps.length, 1));
                    nodes = local.nodes;
                    edges = local.edges;
                  }
                  if (nodes.length > 0) {
                    setFilteredNodes(nodes);
                    setFilteredEdges(edges);
                    setGraphFiltered(true);
                    setTimeout(() => fgRef.current?.zoomToFit(400, 40), 60);
                  }
                }}
                className="flex items-center gap-1.5 px-3 py-2 bg-[#B88719] text-white rounded-xl text-xs font-black uppercase tracking-wider hover:bg-[#8A5A00] transition-all cursor-pointer">
                {queryRunning
                  ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  : <Play    className="w-3.5 h-3.5" />}
                {queryRunning ? 'Building' : 'Build'}
              </button>
            </div>

            {/* Builder steps */}
            <div className="flex-1 overflow-y-auto p-4 space-y-2 custom-scrollbar">
              {/* Start node — searchable combobox */}
              {allEntities.length === 0 ? (
                <div className="px-3 py-2.5 bg-slate-50 border border-slate-200 rounded-xl text-[10px] text-slate-400 font-mono text-center">
                  No entities — Neo4j schema not loaded
                </div>
              ) : (
                <div className="flex items-center gap-2 px-3 py-2.5 bg-[#FFF9E8] border-2 border-[#B88719] rounded-xl relative">
                  <Circle className="w-3.5 h-3.5 text-[#B88719] shrink-0" />
                  <div className="relative flex-1">
                    <input
                      value={entitySearchOpen ? entitySearchQuery : startNode}
                      onFocus={() => { setEntitySearchOpen(true); setEntitySearchQuery(''); }}
                      onChange={e => { setEntitySearchQuery(e.target.value); setEntitySearchOpen(true); }}
                      onBlur={() => setTimeout(() => { setEntitySearchOpen(false); setEntitySearchQuery(''); }, 160)}
                      placeholder="Search entity…"
                      className="bg-transparent text-xs font-bold text-[#8A5A00] focus:outline-none w-full placeholder:text-[#8A5A00]/40"
                    />
                    {entitySearchOpen && (
                      <div className="absolute top-full left-0 z-50 mt-1.5 w-52 max-h-44 overflow-y-auto bg-white border border-[#BFA66A]/40 rounded-xl shadow-lg custom-scrollbar">
                        {filteredEntityOptions.length === 0 ? (
                          <div className="px-3 py-2 text-[10px] text-slate-400 font-mono">No match</div>
                        ) : filteredEntityOptions.map(n => (
                          <button
                            key={n}
                            onMouseDown={() => {
                              setStartNode(n);
                              setSteps([]);
                              setQueryRan(false);
                              setEntitySearchQuery('');
                              setEntitySearchOpen(false);
                            }}
                            className={cn(
                              'w-full text-left px-3 py-2 text-xs font-bold border-b border-[#BFA66A]/10 last:border-0 transition-colors cursor-pointer',
                              n === startNode ? 'bg-[#FFF9E8] text-[#B88719]' : 'text-[#8A5A00] hover:bg-[#FFFDF8]',
                            )}>
                            {n}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <span className="text-[9px] font-mono text-[#8A5A00]/50 uppercase tracking-wider shrink-0">start</span>
                </div>
              )}

              {/* Step rows â€" vertical */}
              {steps.map((step, si) => {
                const fromEntity = si === 0 ? startNode : steps[si - 1].nodeType;
                const targets = getTargets(fromEntity);
                return (
                  <div key={step.id} className="ml-3 border-l-2 border-[#BFA66A]/30 pl-3 space-y-1.5">
                    {/* Relationship — matches any link type/direction */}
                    <div className="flex items-center gap-1.5">
                      <ArrowDown className="w-3 h-3 text-slate-300 shrink-0" />
                      <div className="flex items-center gap-1 px-2.5 py-1.5 bg-slate-50 border border-slate-200 rounded-lg flex-1 min-w-0">
                        <span className="text-[9px] font-mono text-slate-400 shrink-0">[</span>
                        <span className="text-[11px] font-bold text-slate-500 font-mono flex-1 select-none">any relationship</span>
                        <span className="text-[9px] font-mono text-slate-400 shrink-0">]</span>
                      </div>
                    </div>
                    {/* Target node + delete */}
                    <div className="flex items-center gap-1.5 ml-4">
                      <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-[#F5F0E8] border border-[#BFA66A]/40 rounded-xl flex-1 min-w-0">
                        <Circle className="w-2.5 h-2.5 text-[#B88719] shrink-0" />
                        <select value={step.nodeType} onChange={e => updateStep(step.id, e.target.value)}
                          className="bg-transparent text-[11px] font-bold text-[#8A5A00] focus:outline-none cursor-pointer flex-1 min-w-0">
                          {targets.map(n => <option key={n} value={n}>{n}</option>)}
                        </select>
                      </div>
                      <button onClick={() => deleteStep(step.id)}
                        className="p-1.5 text-slate-300 hover:text-red-500 hover:bg-red-50 rounded-lg transition-colors cursor-pointer border border-transparent hover:border-red-100 shrink-0">
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}

              {/* Add Step */}
              <button onClick={addStep}
                className="w-full flex items-center justify-center gap-1.5 py-2 border border-dashed border-[#BFA66A]/50 hover:border-[#B88719] hover:bg-[#FFF9E8] text-slate-400 hover:text-[#8A5A00] rounded-xl text-[11px] font-bold transition-all cursor-pointer mt-1">
                <Plus className="w-3 h-3" />
                Add Step
              </button>

              {/* Generated Cypher */}
              {queryRan && cypher && (
                <div className="space-y-1.5 pt-2">
                  <span className="text-[9px] font-black uppercase tracking-widest text-[#8A5A00] font-mono flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5 text-green-600" />
                    Generated Cypher
                  </span>
                  <pre className="bg-[#0D0D0B] text-green-400 text-[10px] font-mono p-3 rounded-xl overflow-x-auto leading-relaxed border border-[#BFA66A]/20 whitespace-pre-wrap break-all">{cypher}</pre>
                </div>
              )}
            </div>
          </div>

          {/* â"€â"€ RIGHT: Graph Canvas â"€â"€ */}
          <div ref={graphContainerRef} className="flex-1 rounded-3xl relative overflow-hidden bg-[#0A0909] border border-white/6">
            {neoLoading && (
              <div className="absolute inset-0 z-20 flex items-center justify-center bg-[#0A0909]/80 backdrop-blur-sm">
                <div className="flex flex-col items-center gap-2">
                  <Loader2 className="w-6 h-6 text-[#B88719] animate-spin" />
                  <span className="text-[10px] font-mono text-[#B88719]/70 uppercase tracking-widest">Loading Graph</span>
                </div>
              </div>
            )}
            <div className="absolute top-5 left-5 flex flex-col gap-1.5 z-10">
              {([
                { label: '+', fn: () => fgRef.current?.zoom(fgRef.current.zoom() * 1.3, 300) },
                { label: '−', fn: () => fgRef.current?.zoom(fgRef.current.zoom() / 1.3, 300) },
                { label: '⊡', fn: () => fgRef.current?.zoomToFit(400, 40) },
              ] as { label: string; fn: () => void }[]).map(({ label, fn }) => (
                <button key={label} onClick={fn}
                  className="w-8 h-8 bg-white/5 border border-white/10 rounded-lg hover:bg-white/10 text-slate-400 text-sm flex items-center justify-center font-mono cursor-pointer transition-colors">
                  {label}
                </button>
              ))}
            </div>
            <div className="absolute top-5 right-5 z-10 flex items-center gap-2">
              {graphFiltered && (
                <button
                  onClick={() => {
                    setFilteredNodes(null);
                    setFilteredEdges(null);
                    setGraphFiltered(false);
                    setQueryRan(false);
                    setTimeout(() => fgRef.current?.zoomToFit(400, 40), 50);
                  }}
                  title="Revert the canvas to the full graph"
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-white/10 border border-white/20 text-white/70 hover:bg-white/20 rounded-full text-[9px] font-black uppercase tracking-widest transition-all cursor-pointer">
                  <RefreshCw className="w-3 h-3" />
                  Reset to Full Graph
                </button>
              )}
              <span className="pointer-events-none flex items-center gap-1.5 px-3 py-1.5 bg-[#B88719]/10 border border-[#B88719]/20 text-[#B88719] rounded-full text-[9px] font-black uppercase tracking-widest">
                <span className="w-1.5 h-1.5 bg-[#B88719] rounded-full animate-pulse" />
                {graphFiltered ? 'Query Result' : 'Force Layout Active'}
              </span>
            </div>
            <ForceGraph2D
              ref={fgRef}
              graphData={graphData}
              width={graphDims.w}
              height={graphDims.h}
              backgroundColor="#0A0909"
              linkColor={() => 'rgba(184,135,25,0.3)'}
              linkWidth={1.5}
              linkDirectionalArrowLength={5}
              linkDirectionalArrowRelPos={1}
              linkDirectionalArrowColor={() => 'rgba(184,135,25,0.5)'}
              // Keep the render loop (and therefore the offscreen hit-detection
              // shadow canvas) alive after the layout settles. With the default
              // auto-pause, the shadow canvas stops refreshing once the engine
              // cools down, so settled nodes — and any node after a pan/zoom —
              // lose their hover/click hit areas and appear "dead".
              autoPauseRedraw={false}
              onNodeHover={(node: any) => setHoverNodeId(node ? node.id : null)}
              onNodeClick={(node: any) => setSelectedNeoNode(
                selectedNeoNode?.id === node.id ? null : { id: node.id, name: node.name ?? '', description: node.description, type: node.type },
              )}
              nodeCanvasObject={(node: any, ctx, globalScale) => {
                // Positions are NaN on the first layout tick — skip until resolved
                // (createRadialGradient throws on non-finite coordinates).
                if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
                const isSel = node.id === selectedNeoNode?.id;
                const isHover = node.id === hoverNodeId;
                const emphasised = isSel || isHover;
                // Node radius is a fixed GRAPH-UNIT size, so it scales with the
                // zoom — dots grow as you zoom in and shrink as you zoom out. The
                // on-screen size is clamped so they never collapse into specks at
                // full zoom-out nor swell into blobs at full zoom-in.
                const baseGraph = emphasised ? 6 : 5;
                const screenPx = Math.min(46, Math.max(3, baseGraph * globalScale));
                const r = screenPx / globalScale; // back to graph units for drawing
                const base = nodeColor(node.type);
                // Glow (subtle for normal nodes, brighter when selected/hovered)
                const glowR = r * (emphasised ? 4 : 2.4);
                const grad = ctx.createRadialGradient(node.x, node.y, 0, node.x, node.y, glowR);
                grad.addColorStop(0, isSel ? '#FFD700' : base);
                grad.addColorStop(1, 'transparent');
                ctx.save();
                ctx.globalAlpha = isSel ? 0.4 : isHover ? 0.3 : 0.18;
                ctx.beginPath(); ctx.arc(node.x, node.y, glowR, 0, 2 * Math.PI);
                ctx.fillStyle = grad; ctx.fill();
                ctx.restore();
                // Node body — colour by entity type
                ctx.beginPath(); ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
                ctx.fillStyle = isSel ? '#FFD700' : base; ctx.fill();
                ctx.strokeStyle = isSel
                  ? 'rgba(255,215,0,0.85)'
                  : isHover ? 'rgba(255,255,255,0.7)' : 'rgba(255,255,255,0.28)';
                // Outline thickness tracks the (scaled) node so it stays proportional.
                ctx.lineWidth = r * (emphasised ? 0.32 : 0.24); ctx.stroke();
                // Name label for the selected OR hovered node so hovering always
                // surfaces the node's name on the canvas (the full detail still
                // appears in the node-info panel on click). Kept at a constant
                // on-screen size so it stays readable at any zoom.
                if (emphasised) {
                  const fontSize = Math.max(9, 11 / globalScale);
                  ctx.font = `bold ${fontSize}px monospace`;
                  ctx.textAlign = 'center'; ctx.textBaseline = 'top';
                  ctx.fillStyle = 'rgba(255,255,255,0.9)';
                  ctx.fillText(node.name ?? '', node.x, node.y + r + 3 / globalScale);
                }
              }}
              nodeCanvasObjectMode={() => 'replace'}
              nodeLabel={(node: any) => node.name || node.id || ''}
              nodeRelSize={6}
              nodePointerAreaPaint={(node: any, color, ctx, globalScale = 1) => {
                // Hit-area must be painted explicitly when using a custom node
                // renderer. Mirror the visible dot's zoom-scaling (same clamped
                // graph-unit size, a touch larger) so "what you see is what you
                // can click" at every zoom level — with a screen-px floor so even
                // fully zoomed-out specks stay comfortably clickable.
                if (!Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
                const screenPx = Math.min(46, Math.max(3, 5 * globalScale));
                const hitR = Math.max(7, screenPx * 1.4) / globalScale;
                ctx.fillStyle = color;
                ctx.beginPath(); ctx.arc(node.x, node.y, hitR, 0, 2 * Math.PI); ctx.fill();
              }}
              d3VelocityDecay={0.3}
              d3AlphaDecay={0.05}
              cooldownTicks={60}
              onEngineStop={() => {
                // Fit to view only the first time after each data load — not on
                // every settle, so dragging a node doesn't snap the zoom back.
                if (!didFitRef.current) {
                  didFitRef.current = true;
                  fgRef.current?.zoomToFit(400, 40);
                }
              }}
            />
            {/* Node info panel */}
            {selectedNeoNode && (
              <div className="absolute bottom-20 left-5 z-20 bg-[#12110E]/92 backdrop-blur-sm border border-[#B88719]/35 rounded-2xl p-4 max-w-[260px] shadow-[0_8px_32px_rgba(0,0,0,0.7)]">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <span className="text-[9px] font-black uppercase tracking-widest text-[#B88719] font-mono">Node Info</span>
                  <button onClick={() => setSelectedNeoNode(null)} className="text-white/30 hover:text-white/60 transition-colors text-xs leading-none cursor-pointer">✕</button>
                </div>
                <p className="text-sm font-bold text-white font-mono">{selectedNeoNode.name || '(unnamed)'}</p>
                {selectedNeoNode.type && (
                  <span className="inline-flex items-center gap-1 mt-1.5 px-2 py-0.5 rounded-full text-[9px] font-bold font-mono"
                    style={{ background: `${nodeColor(selectedNeoNode.type)}22`, color: nodeColor(selectedNeoNode.type), border: `1px solid ${nodeColor(selectedNeoNode.type)}55` }}>
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: nodeColor(selectedNeoNode.type) }} />
                    {selectedNeoNode.type}
                  </span>
                )}
                <div className="mt-2">
                  <span className="text-[8px] font-black uppercase tracking-widest text-white/30 font-mono">Description</span>
                  <p className="text-[11px] text-white/55 leading-relaxed mt-0.5">
                    {selectedNeoNode.description || 'No description available.'}
                  </p>
                </div>
                <div className="mt-3 pt-2 border-t border-white/10">
                  <span className="text-[9px] text-white/20 font-mono break-all">{selectedNeoNode.id}</span>
                </div>
              </div>
            )}
            {/* Node-type colour legend */}
            {presentTypes.length > 0 && (
              <div className="pointer-events-none absolute bottom-5 right-5 z-10 bg-[#12110E]/85 backdrop-blur-sm border border-white/10 rounded-xl px-3 py-2.5 max-w-[180px]">
                <div className="text-[8px] font-black uppercase tracking-widest text-white/40 font-mono mb-1.5">Node Types</div>
                <div className="flex flex-col gap-1">
                  {presentTypes.map(t => (
                    <div key={t} className="flex items-center gap-1.5">
                      <span className="w-2 h-2 rounded-full shrink-0" style={{ background: nodeColor(t) }} />
                      <span className="text-[9px] text-white/60 font-mono truncate">{t}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
