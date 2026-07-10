import React, { useState, useCallback, useMemo, useRef, useContext, useEffect } from 'react';
import {
  Save, Play, Layout, Code as CodeIcon, CheckCircle2, Terminal,
  ArrowLeft, ChevronDown, Bot, Database, Activity, Zap, Cpu,
  Search, ShieldCheck, Wrench, Layers, Filter,
  GitBranch, Bell, RefreshCw, GripVertical, X, Copy, Trash2, Tag, Plus,
  Eye, EyeOff, PlugZap,
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import type { Workflow } from '../../../types/workflow';
import {
  ReactFlow,
  addEdge,
  useNodesState,
  useEdgesState,
  useReactFlow,
  Controls,
  Background,
  BackgroundVariant,
  MarkerType,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type Node,
  type Edge,
  type OnConnect,
  type NodeProps,
  type EdgeProps,
  type ReactFlowInstance,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useRuntimeConfig } from '../../../lib/runtimeConfig';

const LOCKED_NODE_TYPES    = new Set(['guardrail_input', 'guardrail_output', 'guardrail']);

// â”€â”€â”€ Types â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export type TemplateId = 'blank' | 'rag' | 'multi-agent' | 'hitl';

type FlowNodeType =
  | 'trigger' | 'guardrail' | 'planner' | 'kb_search' | 'mcp' | 'tool_executor'
  | 'rrf_ranking' | 'reranker' | 'responder' | 'output'
  | 'human_approval' | 'condition' | 'db_query'
  | 'send_notification' | 'loop' | 'judge' | 'metadata_injector' | 'db_connector';

export interface FlowNodeData extends Record<string, unknown> {
  nodeType: FlowNodeType;
  label: string;
  locked?: boolean;
  // mcp
  mcpServerUrl?: string;
  allowedTools?: string[];
  // tool_executor
  allowedBuiltinTools?: string[];
  defaultFilePath?: string;
  pandasAllowedDir?: string;
  fileReadAllowedDir?: string;
  grepAllowedDir?: string;
  globAllowedDir?: string;
  writeAllowedDir?: string;
  bashEnabled?: boolean;
  bashCwd?: string;
  // reranker
  rerankerModel?: string;
  topN?: number;
  // responder
  systemPromptName?: string;
  temperature?: number;
  maxTokens?: number;
  // human_approval
  approverRole?: string;
  // metadata_injector
  metadataKv?: string;
  // db_connector — chuỗi JSON, mảng {name, host, port, database, user, secretId, note}
  dbConnections?: string;
}

// â”€â”€â”€ Toast Context (shared with custom edge) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const BuilderToastCtx = React.createContext<(msg: string) => void>(() => {});

// â”€â”€â”€ Node Style Map â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const NODE_STYLE: Record<string, { typeLabel: string; Icon: any; color: string }> = {
  trigger:           { typeLabel: 'Trigger',        Icon: Zap,         color: '#6B7280' },
  guardrail:         { typeLabel: 'Guardrail',       Icon: ShieldCheck, color: '#EF4444' },
  planner:           { typeLabel: 'Planner',        Icon: Bot,         color: '#3B82F6' },
  kb_search:         { typeLabel: 'KB Search',      Icon: Database,    color: '#10B981' },
  mcp:               { typeLabel: 'MCP Tool',       Icon: Wrench,      color: '#F59E0B' },
  tool_executor:     { typeLabel: 'Tool Executor',  Icon: Zap,         color: '#F97316' },
  rrf_ranking:       { typeLabel: 'RRF Ranking',    Icon: Layers,      color: '#8B5CF6' },
  reranker:          { typeLabel: 'Reranker',       Icon: Filter,      color: '#8B5CF6' },
  responder:         { typeLabel: 'Responder',      Icon: Cpu,         color: '#3B82F6' },
  output:            { typeLabel: 'Output',         Icon: Activity,    color: '#6B7280' },
  human_approval:    { typeLabel: 'Human Approval', Icon: ShieldCheck, color: '#D9B86C' },
  condition:         { typeLabel: 'Condition',      Icon: GitBranch,   color: '#6B7280' },
  db_query:          { typeLabel: 'DB Query',       Icon: Database,    color: '#10B981' },
  send_notification: { typeLabel: 'Notification',   Icon: Bell,        color: '#EF4444' },
  loop:              { typeLabel: 'Loop',           Icon: RefreshCw,   color: '#3B82F6' },
  judge:             { typeLabel: 'Judge',          Icon: CheckCircle2, color: '#F59E0B' },
  metadata_injector: { typeLabel: 'Metadata Injector', Icon: Tag,      color: '#6366F1' },
  db_connector:      { typeLabel: 'DB Connector',    Icon: Database,    color: '#0EA5B5' },
};

// â”€â”€â”€ Custom Node â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const CustomFlowNode = (props: NodeProps) => {
  const data      = props.data as FlowNodeData;
  const { selected, id } = props;
  const cfg       = NODE_STYLE[data.nodeType] || NODE_STYLE.trigger;
  const hasInput  = data.nodeType !== 'trigger';
  const hasOutput = data.nodeType !== 'output';
  const [hovered, setHovered] = useState(false);

  const { setNodes, setEdges } = useReactFlow();
  const addToast = useContext(BuilderToastCtx);

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setNodes(nds => nds.filter(n => n.id !== id));
    setEdges(eds => eds.filter(ed => ed.source !== id && ed.target !== id));
    addToast(`Node removed: ${data.label}`);
  }, [id, setNodes, setEdges, addToast, data.label]);

  // Viền sáng nhẹ màu tím (màu của Metadata Injector, không phải màu riêng
  // của node này) khi Metadata Injector đang được chọn — báo "node này đọc
  // metadata", không phải trạng thái selected thật của chính node.
  const relatedHighlight = !selected && (data as any)._relatedHighlight === true;

  return (
    <div
      style={{
        position: 'relative',
        background: selected ? `${cfg.color}22` : '#1e293b',
        border: `2px solid ${selected ? cfg.color : relatedHighlight ? '#6366F1' : 'rgba(255,255,255,0.15)'}`,
        borderRadius: 14,
        padding: '11px 16px',
        minWidth: 178,
        boxShadow: selected
          ? `0 0 0 3px ${cfg.color}20, 0 4px 24px rgba(0,0,0,0.5)`
          : relatedHighlight
          ? '0 0 0 3px #6366F133, 0 4px 16px rgba(0,0,0,0.4)'
          : '0 4px 16px rgba(0,0,0,0.4)',
        transition: 'border-color 0.15s, box-shadow 0.15s',
        cursor: 'default',
        userSelect: 'none',
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* X delete button — hidden for locked nodes */}
      {!data.locked && (hovered || selected) && (
        <button
          onClick={handleDelete}
          title="Remove node"
          style={{
            position: 'absolute',
            top: -8,
            right: -8,
            width: 18,
            height: 18,
            borderRadius: '50%',
            background: '#EF4444',
            border: '2px solid #1e293b',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            cursor: 'pointer',
            zIndex: 10,
            padding: 0,
          }}
        >
          <X size={9} color="white" />
        </button>
      )}

      {hasInput && (
        <Handle
          type="target"
          position={Position.Top}
          style={{ background: cfg.color, border: '2px solid #1e293b', width: 10, height: 10 }}
        />
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 6 }}>
        <div style={{
          background: `${cfg.color}22`, borderRadius: 7, padding: 5,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <cfg.Icon size={12} color={cfg.color} />
        </div>
        <span style={{
          fontSize: 9, fontWeight: 900, letterSpacing: '0.15em',
          textTransform: 'uppercase', color: cfg.color, opacity: 0.85,
        }}>
          {cfg.typeLabel}
        </span>
      </div>

      <div style={{ fontSize: 13, fontWeight: 700, color: 'white', lineHeight: 1.3 }}>
        {data.label}
      </div>

      {hasOutput && (
        <Handle
          type="source"
          position={Position.Bottom}
          style={{ background: cfg.color, border: '2px solid #1e293b', width: 10, height: 10 }}
        />
      )}
    </div>
  );
};

// â”€â”€â”€ Custom Edge with Delete Button â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const EdgeWithDelete = (props: EdgeProps) => {
  const {
    id, sourceX, sourceY, targetX, targetY,
    sourcePosition, targetPosition,
    style = {}, markerEnd, label, labelStyle,
    selected, data, source, target,
  } = props;

  const { setEdges, getNode } = useReactFlow();
  const addToast = useContext(BuilderToastCtx);

  // Locked nếu edge.data.locked=true HOẶC node đầu/cuối là guardrail (locked node).
  // Dùng getNode làm fallback vì data.locked không phải lúc nào cũng được giữ qua serialization.
  const isLocked =
    (data as any)?.locked === true ||
    (getNode(source)?.data as any)?.locked === true ||
    (getNode(target)?.data as any)?.locked === true;

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  const onDeleteEdge = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    setEdges(eds => eds.filter(ed => ed.id !== id));
    addToast('Edge removed');
  }, [id, setEdges, addToast]);

  const xBtnY = label ? labelY - 18 : labelY;

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style as React.CSSProperties} />
      {/* Wider invisible stroke for easier click-to-select */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={14}
        className="react-flow__edge-interaction"
      />

      {label && (
        <EdgeLabelRenderer>
          <div
            className="pointer-events-none"
            style={{
              position: 'absolute',
              transform: `translate(-50%,-50%) translate(${labelX}px,${labelY}px)`,
            }}
          >
            <span
              style={{
                ...(labelStyle as React.CSSProperties || {}),
                background: '#1e293b',
                padding: '2px 6px',
                borderRadius: 4,
                display: 'inline-block',
                fontSize: 9,
                fontWeight: 700,
                fontFamily: 'monospace',
              }}
            >
              {label}
            </span>
          </div>
        </EdgeLabelRenderer>
      )}

      {selected && !isLocked && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%,-50%) translate(${labelX}px,${xBtnY}px)`,
              pointerEvents: 'all',
            }}
          >
            <button
              onClick={onDeleteEdge}
              title="Remove edge"
              className="w-5 h-5 bg-red-500/80 hover:bg-red-500 rounded-full flex items-center justify-center border border-red-400/40 transition-all shadow-lg"
            >
              <X className="w-2.5 h-2.5 text-white" />
            </button>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
};

const nodeTypes = { flowNode: CustomFlowNode };
const edgeTypes = { wfEdge: EdgeWithDelete };

// ── Guardrail Banner ──────────────────────────────────────────────────────────

const GuardrailBanner = ({ position }: { position: 'top' | 'bottom' }) => (
  <div
    style={{
      position: 'absolute',
      [position]: 0,
      left: 0,
      right: 0,
      zIndex: 10,
      pointerEvents: 'none',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: '12px 16px',
      background: position === 'top'
        ? 'linear-gradient(to bottom, #0f172a 50%, transparent)'
        : 'linear-gradient(to top, #0f172a 50%, transparent)',
    }}
  >
    <div style={{
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      background: 'rgba(239,68,68,0.09)',
      border: '1px solid rgba(239,68,68,0.25)',
      borderRadius: 20,
      padding: '6px 18px',
    }}>
      <ShieldCheck size={13} color="#EF4444" />
      <span style={{
        fontSize: 11,
        fontWeight: 900,
        letterSpacing: '0.18em',
        textTransform: 'uppercase',
        color: '#EF4444',
        opacity: 0.85,
      }}>
        Guardrail
      </span>
    </div>
  </div>
);

// â”€â”€â”€ Edge Helpers (all use type 'wfEdge') â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const solidEdge = (id: string, src: string, tgt: string, color = '#ffffff22'): Edge => ({
  id, source: src, target: tgt,
  type: 'wfEdge', animated: true,
  style: { stroke: color, strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, color },
});

const labeledSolid = (id: string, src: string, tgt: string, label: string, color: string): Edge => ({
  ...solidEdge(id, src, tgt, color),
  label,
  labelStyle: { fill: color, fontSize: 9, fontWeight: 700, fontFamily: 'monospace' },
});

const dashedEdge = (id: string, src: string, tgt: string, label: string, color: string): Edge => ({
  id, source: src, target: tgt,
  type: 'wfEdge', animated: false,
  style: { stroke: color, strokeWidth: 2, strokeDasharray: '6,3' },
  markerEnd: { type: MarkerType.ArrowClosed, color },
  label,
  labelStyle: { fill: color, fontSize: 9, fontWeight: 700, fontFamily: 'monospace' },
});

// Locked edge — gắn vào/ra guardrail node, không xóa được
const lockedEdge = (id: string, src: string, tgt: string, color = '#EF444455'): Edge => ({
  id, source: src, target: tgt,
  type: 'wfEdge', animated: true,
  deletable: false,
  data: { locked: true },
  style: { stroke: color, strokeWidth: 2 },
  markerEnd: { type: MarkerType.ArrowClosed, color },
});

// â”€â”€â”€ Node Factory â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const mkNode = (
  id: string, nodeType: FlowNodeType, label: string,
  x: number, y: number, extra: Partial<FlowNodeData> = {},
): Node<FlowNodeData> => ({
  id, type: 'flowNode', position: { x, y },
  data: { nodeType, label, ...extra },
});

const mkLockedNode = (
  id: string, nodeType: FlowNodeType, label: string,
  x: number, y: number,
): Node<FlowNodeData> => ({
  id, type: 'flowNode', position: { x, y },
  deletable: false,
  data: { nodeType, label, locked: true },
});

// â”€â”€â”€ Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

// Planner/responder model names and the RRF k are no longer hardcoded here —
// they come from workflow-runtime (GET /api/llm-providers, /api/runtime-config)
// through useRuntimeConfig(), consumed in ConfigPanel.

// â”€â”€â”€ Workflow â†’ React Flow transform â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function transformWorkflowToFlow(workflow: Workflow): { nodes: Node<FlowNodeData>[]; edges: Edge[] } {
  const nodes: Node<FlowNodeData>[] = workflow.nodes.map(wn => ({
    id:       wn.id,
    type:     'flowNode',
    position: wn.position,
    data:     { nodeType: wn.type as FlowNodeType, label: wn.label, ...wn.config },
  }));

  const edges: Edge[] = workflow.edges.map(we => ({
    id: we.id, source: we.source, target: we.target,
    type: 'wfEdge', animated: true,
    style: { stroke: '#ffffff22', strokeWidth: 2 },
    markerEnd: { type: MarkerType.ArrowClosed, color: '#ffffff22' },
    ...(we.label ? { label: we.label, labelStyle: { fill: '#94a3b8', fontSize: 9, fontWeight: 700, fontFamily: 'monospace' } } : {}),
  }));

  return { nodes, edges };
}

// â”€â”€â”€ Template Flows â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export const TEMPLATE_FLOWS: Record<TemplateId, { nodes: Node<FlowNodeData>[]; edges: Edge[] }> = {

  blank: {
    nodes: [],
    edges: [],
  },

  rag: {
    nodes: [
      mkNode('planner',     'planner',     'Planner',     240, 0),
      mkNode('kb_search',   'kb_search',   'KB Search',   240, 110, ),
      mkNode('rrf_ranking', 'rrf_ranking', 'RRF Ranking', 240, 220),
      mkNode('reranker',    'reranker',    'Reranker',    240, 330, { rerankerModel: 'BGE-Reranker-v2-m3', topN: 5 }),
      mkNode('responder',   'responder',   'Responder',   240, 440, { systemPromptName: 'prompt_default', temperature: 0.2, maxTokens: 1000 }),
    ],
    edges: [
      solidEdge('e1', 'planner',     'kb_search',   '#3B82F666'),
      solidEdge('e2', 'kb_search',   'rrf_ranking', '#10B98166'),
      solidEdge('e3', 'rrf_ranking', 'reranker',    '#8B5CF666'),
      solidEdge('e4', 'reranker',    'responder',   '#8B5CF666'),
    ],
  },

  'multi-agent': {
    nodes: [
      mkNode('planner',       'planner',       'Planner',       340, 0),
      mkNode('kb_search',     'kb_search',     'KB Search',     40,  130),
      mkNode('mcp',           'mcp',           'MCP Tool',      340, 130, { mcpServerUrl: 'http://mcp.tenant.gov.vn/sse', allowedTools: ['tra_cuu_phat_nguoi', 'query_dashboard'] }),
      mkNode('tool_executor', 'tool_executor', 'Tool Executor', 640, 130, { allowedBuiltinTools: ['web_search'] }),
      mkNode('rrf_ranking',   'rrf_ranking',   'RRF Ranking',   340, 270),
      mkNode('reranker',      'reranker',      'Reranker',      340, 380, { rerankerModel: 'BGE-Reranker-v2-m3', topN: 5 }),
      mkNode('responder',     'responder',     'Responder',     340, 490, { systemPromptName: 'prompt_default', temperature: 0.2, maxTokens: 1000 }),
    ],
    edges: [
      labeledSolid('e1', 'planner',       'kb_search',     'always',        '#10B98199'),
      dashedEdge  ('e2', 'planner',       'mcp',           'if mcp_calls',  '#F59E0B99'),
      dashedEdge  ('e3', 'planner',       'tool_executor', 'if tool_calls', '#F9731699'),
      solidEdge   ('e4', 'kb_search',     'rrf_ranking',   '#10B98166'),
      solidEdge   ('e5', 'mcp',           'rrf_ranking',   '#F59E0B66'),
      solidEdge   ('e6', 'tool_executor', 'rrf_ranking',   '#F9731666'),
      solidEdge   ('e7', 'rrf_ranking',   'reranker',      '#8B5CF666'),
      solidEdge   ('e8', 'reranker',      'responder',     '#8B5CF666'),
    ],
  },

  hitl: {
    nodes: [
      mkNode('planner',        'planner',        'Planner',        340, 0),
      mkNode('kb_search',      'kb_search',      'KB Search',      40,  130),
      mkNode('mcp',            'mcp',            'MCP Tool',       340, 130, { mcpServerUrl: 'http://mcp.tenant.gov.vn/sse', allowedTools: ['tra_cuu_phat_nguoi', 'query_dashboard'] }),
      mkNode('tool_executor',  'tool_executor',  'Tool Executor',  640, 130, { allowedBuiltinTools: ['web_search'] }),
      mkNode('rrf_ranking',    'rrf_ranking',    'RRF Ranking',    340, 270),
      mkNode('reranker',       'reranker',       'Reranker',       340, 380, { rerankerModel: 'BGE-Reranker-v2-m3', topN: 5 }),
      mkNode('responder',      'responder',      'Responder',      340, 490, { systemPromptName: 'prompt_default', temperature: 0.2, maxTokens: 1000 }),
      mkNode('human_approval', 'human_approval', 'Human Review',   340, 600, { approverRole: 'MANAGER' }),
    ],
    edges: [
      labeledSolid('e1', 'planner',        'kb_search',      'always',        '#10B98199'),
      dashedEdge  ('e2', 'planner',        'mcp',            'if mcp_calls',  '#F59E0B99'),
      dashedEdge  ('e3', 'planner',        'tool_executor',  'if tool_calls', '#F9731699'),
      solidEdge   ('e4', 'kb_search',      'rrf_ranking',    '#10B98166'),
      solidEdge   ('e5', 'mcp',            'rrf_ranking',    '#F59E0B66'),
      solidEdge   ('e6', 'tool_executor',  'rrf_ranking',    '#F9731666'),
      solidEdge   ('e7', 'rrf_ranking',    'reranker',       '#8B5CF666'),
      solidEdge   ('e8', 'reranker',       'responder',      '#8B5CF666'),
      solidEdge   ('e9', 'responder',      'human_approval', '#D9B86C66'),
    ],
  },
};

// â”€â”€â”€ Config Panel Sub-components â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div>
    <label className="text-[9px] font-black text-slate-600 uppercase tracking-widest mb-1.5 block">
      {label}
    </label>
    {children}
  </div>
);

const TextInput = ({
  value, onChange, placeholder,
}: { value: string; onChange: (v: string) => void; placeholder: string }) => (
  <input
    type="text"
    value={value}
    onChange={e => onChange(e.target.value)}
    placeholder={placeholder}
    className="w-full bg-white/5 border border-white/10 rounded-xl py-2 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-white/20 placeholder:text-slate-700"
  />
);

const Readonly = ({ value }: { value: string }) => (
  <div className="w-full bg-white/[0.03] border border-white/10 rounded-xl py-2 px-3 text-xs font-mono text-slate-400">
    {value}
  </div>
);

const Sel = ({
  value, options, onChange,
}: { value: string; options: string[]; onChange: (v: string) => void }) => (
  <div className="relative">
    <select
      value={value}
      onChange={e => onChange(e.target.value)}
      className="w-full bg-white/5 border border-white/10 rounded-xl py-2 px-3 text-xs text-white appearance-none focus:outline-none focus:ring-1 focus:ring-white/20 cursor-pointer"
    >
      {options.map(o => <option key={o} value={o} className="bg-slate-900">{o}</option>)}
    </select>
    <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-600 pointer-events-none" />
  </div>
);

const Slider = ({
  label, value, min, max, step, onChange, color = '#8B5CF6',
}: {
  label: string; value: number; min: number; max: number;
  step: number; onChange: (v: number) => void; color?: string;
}) => (
  <div>
    <div className="flex justify-between items-center mb-1.5">
      <label className="text-[9px] font-black text-slate-600 uppercase tracking-widest">{label}</label>
      <span className="text-[10px] font-mono" style={{ color }}>{value}</span>
    </div>
    <input
      type="range"
      min={min} max={max} step={step} value={value}
      onChange={e => onChange(parseFloat(e.target.value))}
      className="w-full h-1 rounded-full appearance-none cursor-pointer bg-white/10"
      style={{ accentColor: color }}
    />
    <div className="flex justify-between text-[8px] text-slate-700 mt-1">
      <span>{min}</span><span>{max}</span>
    </div>
  </div>
);

const InfoNote = ({ text }: { text: string }) => (
  <div className="p-3 rounded-xl bg-white/[0.03] border border-white/10">
    <p className="text-[11px] text-slate-500 italic leading-relaxed">{text}</p>
  </div>
);

// Lưu trữ vẫn đúng 1 chuỗi "key1=value1,key2=value2" (không đổi format
// backend/config_loader._extract_node_config) — chỉ đổi cách NHẬP thành
// từng dòng Key/Value + nút xoá/thêm, thay vì 1 ô text thô dễ gõ nhầm dấu
// phẩy/dấu bằng.
const parseKv = (raw: string): { key: string; value: string }[] =>
  raw ? raw.split(',').map(pair => {
    const idx = pair.indexOf('=');
    return idx === -1 ? { key: pair, value: '' } : { key: pair.slice(0, idx), value: pair.slice(idx + 1) };
  }) : [];

const serializeKv = (rows: { key: string; value: string }[]): string =>
  rows.map(r => `${r.key}=${r.value}`).join(',');

const KeyValueEditor = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => {
  const rows = parseKv(value);

  const updateRow = (i: number, field: 'key' | 'value', v: string) => {
    const next = rows.map((r, idx) => idx === i ? { ...r, [field]: v } : r);
    onChange(serializeKv(next));
  };
  const removeRow = (i: number) => onChange(serializeKv(rows.filter((_, idx) => idx !== i)));
  const addRow = () => onChange(serializeKv([...rows, { key: '', value: '' }]));

  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="group flex items-center gap-1.5">
          <input
            type="text"
            value={row.key}
            onChange={e => updateRow(i, 'key', e.target.value)}
            placeholder="key"
            className="w-[38%] bg-white/5 border border-white/10 rounded-lg py-1.5 px-2 text-[11px] font-mono text-white focus:outline-none focus:ring-1 focus:ring-white/20 placeholder:text-slate-700"
          />
          <input
            type="text"
            value={row.value}
            onChange={e => updateRow(i, 'value', e.target.value)}
            placeholder="value"
            className="flex-1 bg-white/5 border border-white/10 rounded-lg py-1.5 px-2 text-[11px] font-mono text-white focus:outline-none focus:ring-1 focus:ring-white/20 placeholder:text-slate-700"
          />
          <button
            onClick={() => removeRow(i)}
            title="Xoá cặp key-value"
            className="shrink-0 w-5 h-5 flex items-center justify-center rounded-md text-slate-700 hover:text-red-400 hover:bg-red-500/10 opacity-0 group-hover:opacity-100 transition-opacity"
          >
            <X size={11} />
          </button>
        </div>
      ))}
      <button
        onClick={addRow}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border border-dashed border-white/15 text-[10px] font-semibold text-slate-500 hover:text-white hover:border-white/30 transition-colors"
      >
        <Plus size={11} /> Thêm cặp key-value
      </button>
    </div>
  );
};

// DB Connector — danh sách kết nối có tên (giống MCP có danh sách endpoint,
// không phải nhiều node trên canvas). Mỗi kết nối là host/port/database/user
// tách riêng (KHÔNG phải 1 chuỗi dbUrl như text_to_sql cũ) + password không
// bao giờ gõ tay lưu thẳng vào canvas — bấm "Lưu 🔒" gọi POST /api/auth/secrets
// (key_type=DB_CONNECTOR_PASSWORD, đã có sẵn, tenant-admin dùng được cho
// đúng tenant mình — xem services/auth-api/routers/secrets.py), chỉ
// secret_id trả về được lưu vào JSON, password thật không bao giờ vào state
// component sau khi lưu xong.
type DbConnRow = {
  name: string; host: string; port: number; database: string; user: string;
  secretId: string; note: string;
};

const parseDbConns = (raw: string): DbConnRow[] => {
  try {
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
};
const serializeDbConns = (rows: DbConnRow[]): string => JSON.stringify(rows);

// Modal xác nhận nhỏ, dùng chung style với ConfirmExitModal (motion + overlay
// mờ) — vì xoá 1 kết nối kéo theo xoá vĩnh viễn secret trong OpenBao
// (removeRow bên dưới), không thể hoàn tác, nên bắt xác nhận trước.
const ConfirmDangerModal = ({
  title, message, confirmLabel, busy, onConfirm, onCancel,
}: {
  title: string; message: string; confirmLabel: string; busy: boolean;
  onConfirm: () => void; onCancel: () => void;
}) => (
  <div className="fixed inset-0 z-[200] flex items-center justify-center">
    <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={busy ? undefined : onCancel} />
    <motion.div
      initial={{ scale: 0.95, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      exit={{ scale: 0.95, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 400, damping: 32 }}
      className="relative z-10 bg-[#1e293b] border border-white/15 rounded-2xl p-7 w-[380px] shadow-2xl"
    >
      <h3 className="text-base font-bold text-white mb-2">{title}</h3>
      <p className="text-sm text-slate-400 leading-relaxed mb-6">{message}</p>
      <div className="flex items-center justify-end gap-3">
        <button
          disabled={busy}
          onClick={onCancel}
          className="px-4 py-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-white/10 rounded-xl transition-all disabled:opacity-40"
        >
          Huỷ
        </button>
        <button
          disabled={busy}
          onClick={onConfirm}
          className="px-4 py-2 text-xs font-bold text-white bg-red-500/80 hover:bg-red-500 rounded-xl transition-all disabled:opacity-40"
        >
          {busy ? 'Đang xoá…' : confirmLabel}
        </button>
      </div>
    </motion.div>
  </div>
);

const DbConnectionEditor = ({ value, onChange }: { value: string; onChange: (v: string) => void }) => {
  const rows = parseDbConns(value);
  const addToast = useContext(BuilderToastCtx);
  const [pendingPasswords, setPendingPasswords] = useState<Record<number, string>>({});
  const [visiblePasswords, setVisiblePasswords] = useState<Record<number, boolean>>({});
  const [savingIdx, setSavingIdx] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<Record<number, { ok: boolean; message: string }>>({});
  const [deleteIdx, setDeleteIdx] = useState<number | null>(null);
  const [deleting, setDeleting] = useState(false);

  const updateRow = (i: number, patch: Partial<DbConnRow>) => {
    const next = rows.map((r, idx) => idx === i ? { ...r, ...patch } : r);
    onChange(serializeDbConns(next));
  };
  const addRow = () => onChange(serializeDbConns([
    ...rows, { name: '', host: '', port: 5432, database: '', user: '', secretId: '', note: '' },
  ]));

  // "Lưu" giờ gộp cả bước test kết nối — sai thì báo lỗi và KHÔNG lưu, đúng
  // mới thật sự lưu password vào OpenBao. Tránh lưu 1 password sai/gõ nhầm
  // mà không ai biết cho tới khi agent chạy thật mới phát hiện ra.
  const savePassword = async (i: number) => {
    const pw = pendingPasswords[i];
    if (!pw) return;
    const row = rows[i];
    setSavingIdx(i);
    setTestResult(prev => { const next = { ...prev }; delete next[i]; return next; });
    try {
      const testRes = await apiMutate<{ success: boolean; error?: string }>(
        'POST', '/api/db-connector/test-connection',
        { host: row.host, port: row.port || 5432, database: row.database, user: row.user, password: pw },
      );
      if (!testRes.success) {
        setTestResult(prev => ({ ...prev, [i]: { ok: false, message: testRes.error || 'Không kết nối được' } }));
        addToast('Không lưu — kết nối thất bại, kiểm tra lại thông tin');
        return;
      }

      const keyName = `db-connector-${row.name || 'conn'}-${Date.now()}`;
      const result = await apiMutate<{ id: string }>('POST', '/api/auth/secrets', {
        key_name: keyName, key_type: 'DB_CONNECTOR_PASSWORD', value: pw, rotation_days: 90,
      });
      updateRow(i, { secretId: result.id });
      setPendingPasswords(prev => {
        const next = { ...prev };
        delete next[i];
        return next;
      });
      addToast('Đã kiểm tra kết nối và lưu mật khẩu an toàn');
    } catch (e) {
      // apiMutate đã trả đúng lỗi thật từ server (403 thiếu quyền, 422 sai
      // dữ liệu, 500 lỗi backend...) — hiện nguyên lỗi đó, không tự đoán
      // luôn là "thiếu quyền" (dễ gây hiểu nhầm khi lỗi thật là chỗ khác).
      addToast(`Lưu mật khẩu thất bại: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSavingIdx(null);
    }
  };

  // Xoá kết nối phải xoá luôn secret trong OpenBao (nếu đã lưu) — không để
  // secret mồ côi vĩnh viễn trong vault. Best-effort: nếu API xoá secret
  // thất bại, vẫn xoá khỏi canvas nhưng báo rõ để dọn tay sau.
  const confirmRemove = async () => {
    if (deleteIdx === null) return;
    const row = rows[deleteIdx];
    setDeleting(true);
    try {
      if (row.secretId) {
        try {
          await apiMutate('DELETE', `/api/auth/secrets/${row.secretId}`);
        } catch (e) {
          addToast(`Đã xoá khỏi canvas nhưng chưa xoá được secret trên server (cần dọn tay): ${e instanceof Error ? e.message : String(e)}`);
        }
      }
      onChange(serializeDbConns(rows.filter((_, idx) => idx !== deleteIdx)));
    } finally {
      setDeleting(false);
      setDeleteIdx(null);
    }
  };

  return (
    <div className="space-y-3">
      {rows.map((row, i) => (
        <div key={i} className="p-3 rounded-xl bg-white/[0.03] border border-white/10 space-y-2.5 relative">
          <button
            onClick={() => setDeleteIdx(i)}
            title="Xoá kết nối"
            className="absolute top-2.5 right-2.5 w-5 h-5 flex items-center justify-center rounded-md text-slate-700 hover:text-red-400 hover:bg-red-500/10 transition-colors"
          >
            <X size={11} />
          </button>

          <Field label="Tên kết nối">
            <TextInput value={row.name} onChange={v => updateRow(i, { name: v })} placeholder="DB Bán hàng" />
          </Field>

          <div className="grid grid-cols-[2fr_1fr] gap-2">
            <Field label="Host">
              <TextInput value={row.host} onChange={v => updateRow(i, { host: v })} placeholder="db.company.internal" />
            </Field>
            <Field label="Port">
              <TextInput
                value={row.port ? String(row.port) : ''}
                onChange={v => updateRow(i, { port: parseInt(v, 10) || 5432 })}
                placeholder="5432"
              />
            </Field>
          </div>

          <Field label="Database">
            <TextInput value={row.database} onChange={v => updateRow(i, { database: v })} placeholder="salesdb" />
          </Field>

          <Field label="User">
            <TextInput value={row.user} onChange={v => updateRow(i, { user: v })} placeholder="readonly_agent" />
          </Field>

          <Field label="Password">
            {row.secretId ? (
              <div className="flex items-center gap-1.5 py-1.5 px-2 rounded-lg bg-emerald-500/10 border border-emerald-500/20">
                <ShieldCheck size={12} className="text-emerald-400 shrink-0" />
                <span className="text-[11px] text-emerald-400 flex-1 truncate">
                  Đã lưu an toàn (secret_id: {row.secretId.slice(0, 8)})
                </span>
                <button
                  onClick={() => updateRow(i, { secretId: '' })}
                  className="text-[10px] text-slate-500 hover:text-white underline shrink-0"
                >
                  Đổi
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <div className="relative flex-1">
                  <input
                    type={visiblePasswords[i] ? 'text' : 'password'}
                    value={pendingPasswords[i] || ''}
                    onChange={e => setPendingPasswords(prev => ({ ...prev, [i]: e.target.value }))}
                    placeholder="Nhập mật khẩu"
                    className="w-full bg-white/5 border border-white/10 rounded-lg py-1.5 pl-2 pr-8 text-[11px] text-white focus:outline-none focus:ring-1 focus:ring-white/20 placeholder:text-slate-700"
                  />
                  <button
                    type="button"
                    onClick={() => setVisiblePasswords(prev => ({ ...prev, [i]: !prev[i] }))}
                    title={visiblePasswords[i] ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 text-slate-600 hover:text-white transition-colors"
                  >
                    {visiblePasswords[i] ? <EyeOff size={12} /> : <Eye size={12} />}
                  </button>
                </div>
                <button
                  onClick={() => savePassword(i)}
                  disabled={!pendingPasswords[i] || savingIdx === i}
                  title="Kiểm tra kết nối thật rồi mới lưu — sai thì báo lỗi, không lưu"
                  className="shrink-0 px-2.5 py-1.5 rounded-lg bg-teal-600/80 hover:bg-teal-500 disabled:opacity-40 disabled:cursor-not-allowed text-white text-[10px] font-semibold whitespace-nowrap transition-colors flex items-center gap-1"
                >
                  <PlugZap size={11} /> {savingIdx === i ? 'Đang kiểm tra…' : 'Lưu 🔒'}
                </button>
              </div>
            )}
            {testResult[i] && !testResult[i].ok && (
              <p className="text-[10.5px] mt-1.5 text-red-400">✗ {testResult[i].message}</p>
            )}
          </Field>

          <Field label="Ghi chú thêm (tuỳ chọn)">
            <textarea
              value={row.note || ''}
              onChange={e => updateRow(i, { note: e.target.value })}
              placeholder="VD: bảng orders là đơn hàng, không phải lệnh sản xuất"
              rows={2}
              className="w-full bg-white/5 border border-white/10 rounded-xl py-2 px-3 text-xs text-white font-mono focus:outline-none focus:ring-1 focus:ring-white/20 placeholder:text-slate-700 resize-none"
            />
          </Field>
        </div>
      ))}
      <button
        onClick={addRow}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 rounded-lg border border-dashed border-white/15 text-[10px] font-semibold text-slate-500 hover:text-white hover:border-white/30 transition-colors"
      >
        <Plus size={11} /> Thêm kết nối
      </button>

      <AnimatePresence>
        {deleteIdx !== null && (
          <ConfirmDangerModal
            title="Xoá kết nối này?"
            message={
              rows[deleteIdx]?.secretId
                ? `Mật khẩu đã lưu (secret_id: ${rows[deleteIdx].secretId.slice(0, 8)}) sẽ bị xoá vĩnh viễn khỏi kho bí mật, không khôi phục được.`
                : 'Kết nối chưa lưu mật khẩu — xoá khỏi canvas, không ảnh hưởng gì khác.'
            }
            confirmLabel="Xoá"
            busy={deleting}
            onConfirm={confirmRemove}
            onCancel={() => setDeleteIdx(null)}
          />
        )}
      </AnimatePresence>
    </div>
  );
};

const MCP_TOOLS = ['tra_cuu_phat_nguoi', 'query_dashboard', 'send_alert', 'export_report'];

const BUILTIN_TOOLS: Array<{ name: string; hint: string; category: 'data' | 'filesystem' | 'filesystem-write' | 'shell' | 'langchain' | 'agent' }> = [
  // Data
  { name: 'text_to_pandas',    hint: 'Phân tích file CSV/Excel',       category: 'data'             },
  // File system — read
  { name: 'file_read',         hint: 'Đọc file text',                  category: 'filesystem'       },
  { name: 'grep',              hint: 'Tìm regex trong file',           category: 'filesystem'       },
  { name: 'glob',              hint: 'Liệt kê file theo pattern',      category: 'filesystem'       },
  // File system — write
  { name: 'file_write',        hint: 'Ghi/tạo file',                   category: 'filesystem-write' },
  { name: 'file_edit',         hint: 'Sửa file (string replacement)',  category: 'filesystem-write' },
  // Shell
  { name: 'bash',              hint: 'Thực thi lệnh shell',            category: 'shell'            },
  // LangChain
  { name: 'web_search',        hint: 'Tìm kiếm web (DuckDuckGo)',      category: 'langchain'        },
  { name: 'wikipedia',         hint: 'Tra cứu Wikipedia',              category: 'langchain'        },
  { name: 'calculator',        hint: 'Tính toán số học',               category: 'langchain'        },
  // Multi-agent
  { name: 'delegate_to_agent', hint: 'Ủy quyền cho agent khác',       category: 'agent'            },
];

// â”€â”€â”€ Config Panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface ConfigPanelProps {
  node: Node<FlowNodeData> | null;
  onClose: () => void;
  onUpdate: (id: string, key: string, value: unknown) => void;
}

const ConfigPanel = ({ node, onClose, onUpdate }: ConfigPanelProps) => {
  const { models, rrfK } = useRuntimeConfig();
  if (!node) return null;
  const d   = node.data as FlowNodeData;
  const cfg = NODE_STYLE[d.nodeType] || NODE_STYLE.trigger;
  const upd = (key: string, value: unknown) => onUpdate(node.id, key, value);

  return (
    <motion.div
      key={node.id}
      initial={{ x: 320, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 320, opacity: 0 }}
      transition={{ type: 'spring', stiffness: 340, damping: 34 }}
      className="w-80 border-l border-white/10 bg-[#111827] flex flex-col shrink-0"
    >
      <div className="p-5 border-b border-white/10 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          <div style={{ background: `${cfg.color}20`, borderRadius: 8, padding: 6, display: 'flex' }}>
            <cfg.Icon size={14} color={cfg.color} />
          </div>
          <div>
            <div className="text-white font-bold text-sm leading-none">{d.label}</div>
            <div className="text-[9px] font-black uppercase tracking-widest mt-0.5" style={{ color: cfg.color }}>
              {cfg.typeLabel}
            </div>
          </div>
        </div>
        <button onClick={onClose} className="w-7 h-7 flex items-center justify-center hover:bg-white/10 rounded-lg text-slate-500 hover:text-white transition-all text-xs">
          âœ•
        </button>
      </div>

      <div className="flex-1 overflow-auto custom-scrollbar p-5 space-y-5">
        {d.nodeType === 'trigger' && (
          <InfoNote text="Nhận tất cả loại input từ user" />
        )}

        {d.nodeType === 'guardrail' && (
          <InfoNote text="Node khóa — tự động kiểm tra input/output. Config guardrail rules trong Agent Settings." />
        )}

        {d.nodeType === 'planner' && (
          <>
            <Field label="Model"><Readonly value={models.planner ?? '—'} /></Field>
            <InfoNote text="Tá»± Ä‘á»™ng Ä‘á»c allowed_tools tá»« MCP Tool node" />
          </>
        )}

        {d.nodeType === 'kb_search' && (
          <InfoNote text="Tự động tìm kiếm knowledge base của tenant hiện tại. Không cần cấu hình thêm." />
        )}

        {d.nodeType === 'mcp' && (
          <>
            <Field label="MCP Server URL">
              <TextInput value={(d.mcpServerUrl as string) || ''} onChange={v => upd('mcpServerUrl', v)} placeholder="http://mcp.tenant.gov.vn/sse" />
            </Field>
            <Field label="Allowed Tools">
              <div className="space-y-2 mt-0.5">
                {MCP_TOOLS.map(tool => {
                  const tools   = (d.allowedTools as string[]) || [];
                  const checked = tools.includes(tool);
                  return (
                    <label key={tool} className="flex items-center gap-2.5 cursor-pointer group">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => upd('allowedTools', checked ? tools.filter(t => t !== tool) : [...tools, tool])}
                        className="w-3.5 h-3.5 rounded accent-amber-500 cursor-pointer"
                      />
                      <span className="text-[11px] font-mono text-slate-400 group-hover:text-white transition-colors">{tool}</span>
                    </label>
                  );
                })}
              </div>
            </Field>
            <InfoNote text="Planner Ä‘á»c danh sÃ¡ch nÃ y Ä‘á»ƒ quyáº¿t Ä‘á»‹nh" />
          </>
        )}

        {d.nodeType === 'tool_executor' && (() => {
          const selected = (d.allowedBuiltinTools as string[]) || [];
          const toggle   = (name: string) => upd(
            'allowedBuiltinTools',
            selected.includes(name) ? selected.filter(t => t !== name) : [...selected, name],
          );
          const hasPandas = selected.includes('text_to_pandas');
          const hasFile   = selected.includes('file_read');

          return (
            <>
              {(['data', 'filesystem', 'filesystem-write', 'shell', 'langchain', 'agent'] as const).map(cat => (
                <div key={cat}>
                  <div className={`text-[9px] font-black uppercase tracking-widest mb-2 ${
                    cat === 'data'             ? 'text-orange-400'  :
                    cat === 'filesystem'       ? 'text-emerald-400' :
                    cat === 'filesystem-write' ? 'text-yellow-500'  :
                    cat === 'shell'            ? 'text-red-400'     :
                    cat === 'langchain'        ? 'text-purple-400'  :
                                                 'text-sky-400'
                  }`}>
                    {cat === 'data'             ? 'Data'              :
                     cat === 'filesystem'       ? 'File system (read)':
                     cat === 'filesystem-write' ? 'File system (write)':
                     cat === 'shell'            ? 'Shell'             :
                     cat === 'langchain'        ? 'LangChain'         :
                                                  'Multi-agent'}
                  </div>
                  <div className="space-y-2">
                    {BUILTIN_TOOLS.filter(t => t.category === cat).map(tool => {
                      const checked = selected.includes(tool.name);
                      return (
                        <label key={tool.name} className="flex items-start gap-2.5 cursor-pointer group">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(tool.name)}
                            className="w-3.5 h-3.5 mt-0.5 rounded accent-orange-500 cursor-pointer shrink-0"
                          />
                          <div>
                            <span className="text-[11px] font-mono text-slate-400 group-hover:text-white transition-colors">{tool.name}</span>
                            <div className="text-[9px] text-slate-700">{tool.hint}</div>
                          </div>
                        </label>
                      );
                    })}
                  </div>
                </div>
              ))}

              {hasPandas && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-orange-400 uppercase tracking-widest">text_to_pandas</div>
                  <Field label="Default File Path">
                    <TextInput value={(d.defaultFilePath as string) || ''} onChange={v => upd('defaultFilePath', v)} placeholder="/data/reports/violations.csv" />
                  </Field>
                  <Field label="Allowed Dir">
                    <TextInput value={(d.pandasAllowedDir as string) || ''} onChange={v => upd('pandasAllowedDir', v)} placeholder="/data/reports" />
                  </Field>
                </>
              )}

              {hasFile && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-orange-400 uppercase tracking-widest">file_read</div>
                  <Field label="Allowed Dir">
                    <TextInput value={(d.fileReadAllowedDir as string) || ''} onChange={v => upd('fileReadAllowedDir', v)} placeholder="/data/docs" />
                  </Field>
                </>
              )}

              {selected.includes('grep') && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-emerald-400 uppercase tracking-widest">grep</div>
                  <Field label="Allowed Dir">
                    <TextInput value={(d.grepAllowedDir as string) || ''} onChange={v => upd('grepAllowedDir', v)} placeholder="/data" />
                  </Field>
                </>
              )}

              {selected.includes('glob') && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-emerald-400 uppercase tracking-widest">glob</div>
                  <Field label="Allowed Dir">
                    <TextInput value={(d.globAllowedDir as string) || ''} onChange={v => upd('globAllowedDir', v)} placeholder="/data" />
                  </Field>
                </>
              )}

              {(selected.includes('file_write') || selected.includes('file_edit')) && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-yellow-500 uppercase tracking-widest">file_write / file_edit</div>
                  <Field label="Allowed Dir">
                    <TextInput value={(d.writeAllowedDir as string) || ''} onChange={v => upd('writeAllowedDir', v)} placeholder="/data/output" />
                  </Field>
                </>
              )}

              {selected.includes('bash') && (
                <>
                  <div className="h-px bg-white/10" />
                  <div className="text-[9px] font-black text-red-400 uppercase tracking-widest">bash</div>
                  <label className="flex items-center gap-2.5 cursor-pointer group">
                    <input
                      type="checkbox"
                      checked={!!(d.bashEnabled)}
                      onChange={e => upd('bashEnabled', e.target.checked)}
                      className="w-3.5 h-3.5 rounded accent-red-500 cursor-pointer"
                    />
                    <span className="text-[11px] text-slate-400 group-hover:text-white transition-colors">Enable execution</span>
                  </label>
                  <Field label="Working Dir (cwd)">
                    <TextInput value={(d.bashCwd as string) || ''} onChange={v => upd('bashCwd', v)} placeholder="/workspace" />
                  </Field>
                </>
              )}

              <InfoNote text="Planner tự động biết các tool này và quyết định khi nào gọi dựa vào câu hỏi user." />
            </>
          );
        })()}

        {d.nodeType === 'rrf_ranking' && (
          <InfoNote text={`k=${rrfK ?? '…'}`} />
        )}

        {d.nodeType === 'reranker' && (
          <>
            <Field label="Model">
              <Sel value={(d.rerankerModel as string) || 'BGE-Reranker-v2-m3'} options={['BGE-Reranker-v2-m3', 'bge-reranker-large']} onChange={v => upd('rerankerModel', v)} />
            </Field>
            <Slider label="Top-N" value={Number(d.topN ?? 5)} min={1} max={10} step={1} onChange={v => upd('topN', v)} color="#8B5CF6" />
          </>
        )}

        {d.nodeType === 'responder' && (
          <>
            <Field label="Model"><Readonly value={models.responder ?? '—'} /></Field>
            <Field label="System Prompt">
              <Sel value={(d.systemPromptName as string) || 'prompt_default'} options={['prompt_default', 'prompt_phat_nguoi_v2', 'prompt_dashboard']} onChange={v => upd('systemPromptName', v)} />
            </Field>
            <Slider label="Temperature" value={Number(d.temperature ?? 0.2)} min={0} max={1} step={0.05} onChange={v => upd('temperature', v)} color="#3B82F6" />
            <Field label="Max Tokens">
              <input
                type="number"
                value={Number(d.maxTokens ?? 1000)}
                onChange={e => upd('maxTokens', parseInt(e.target.value, 10))}
                min={100} max={8000} step={100}
                className="w-full bg-white/5 border border-white/10 rounded-xl py-2 px-3 text-xs text-white focus:outline-none focus:ring-1 focus:ring-blue-500/40"
              />
            </Field>
          </>
        )}

        {d.nodeType === 'output' && (
          <InfoNote text="Tráº£ structured data vá» Chat UI" />
        )}

        {d.nodeType === 'human_approval' && (
          <Field label="Approver Role">
            <Sel value={(d.approverRole as string) || 'MANAGER'} options={['MANAGER', 'EXECUTIVE', 'LEGAL', 'CUSTOM']} onChange={v => upd('approverRole', v)} />
          </Field>
        )}

        {['condition', 'db_query', 'send_notification', 'loop'].includes(d.nodeType) && (
          <InfoNote text="Node nÃ y chÆ°a cÃ³ config. KÃ©o vÃ o canvas vÃ  káº¿t ná»‘i vá»›i cÃ¡c node khÃ¡c." />
        )}

        {d.nodeType === 'judge' && (
          <InfoNote text="Đánh giá toàn bộ trace sau responder. Route lại nếu lỗi: RETRIEVAL→kb_search, REASONING→responder, TOOL→tool_executor, DATA_QUALITY→human_approval." />
        )}

        {d.nodeType === 'metadata_injector' && (
          <>
            <Field label="Key-Value">
              <KeyValueEditor
                value={(d.metadataKv as string) || ''}
                onChange={v => upd('metadataKv', v)}
              />
            </Field>
            <InfoNote text="Các cặp key-value được chèn vào ngữ cảnh của Planner và Responder." />
          </>
        )}

        {d.nodeType === 'db_connector' && (
          <>
            <Field label="Kết nối DB">
              <DbConnectionEditor
                value={(d.dbConnections as string) || ''}
                onChange={v => upd('dbConnections', v)}
              />
            </Field>
            <InfoNote text="Schema tự đọc từ information_schema, không cần gõ tay. Chỉ 1 kết nối → dùng luôn. ≥2 kết nối → tự động chọn DB phù hợp theo câu hỏi (1 lệnh LLM), không cần Planner biết tên DB." />
          </>
        )}
      </div>
    </motion.div>
  );
};

// â”€â”€â”€ All Library Nodes (all non-trigger types â€” 14 items) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const ALL_LIBRARY_NODES: Array<{
  nodeType: FlowNodeType; label: string; color: string; Icon: any; allowMultiple: boolean;
}> = [
  { nodeType: 'planner',           label: 'Planner',           color: '#3B82F6', Icon: Bot,         allowMultiple: false },
  { nodeType: 'metadata_injector', label: 'Metadata Injector', color: '#6366F1', Icon: Tag,         allowMultiple: false },
  { nodeType: 'kb_search',         label: 'KB Search',         color: '#10B981', Icon: Database,    allowMultiple: true  },
  { nodeType: 'mcp',               label: 'MCP Tool',          color: '#F59E0B', Icon: Wrench,      allowMultiple: true  },
  { nodeType: 'db_connector',      label: 'DB Connector',      color: '#0EA5B5', Icon: Database,    allowMultiple: false },
  { nodeType: 'tool_executor',     label: 'Tool Executor',     color: '#F97316', Icon: Zap,         allowMultiple: false },
  { nodeType: 'rrf_ranking',       label: 'RRF Ranking',       color: '#8B5CF6', Icon: Layers,      allowMultiple: false },
  { nodeType: 'reranker',          label: 'Reranker',          color: '#8B5CF6', Icon: Filter,      allowMultiple: false },
  { nodeType: 'responder',         label: 'Responder',         color: '#3B82F6', Icon: Cpu,         allowMultiple: false },
  { nodeType: 'output',            label: 'Output',            color: '#6B7280', Icon: Activity,    allowMultiple: false },
  { nodeType: 'human_approval',    label: 'Human Approval',    color: '#D9B86C', Icon: ShieldCheck, allowMultiple: false },
  { nodeType: 'condition',         label: 'Condition',         color: '#6B7280', Icon: GitBranch,   allowMultiple: true  },
  { nodeType: 'send_notification', label: 'Send Notification', color: '#EF4444', Icon: Bell,        allowMultiple: true  },
  { nodeType: 'loop',              label: 'Loop',              color: '#3B82F6', Icon: RefreshCw,   allowMultiple: false },
  { nodeType: 'judge',             label: 'Judge',             color: '#F59E0B', Icon: CheckCircle2, allowMultiple: false },
];

// â”€â”€â”€ Node Library â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const NodeLibrary = ({ usedTypes, nodes: libraryNodes = ALL_LIBRARY_NODES }: { usedTypes: Set<FlowNodeType>; nodes?: typeof ALL_LIBRARY_NODES }) => {
  const [query, setQuery] = useState('');

  const available = libraryNodes.filter(n =>
    (n.allowMultiple || !usedTypes.has(n.nodeType)) &&
    n.label.toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="w-52 border-r border-white/10 bg-[#111827] flex flex-col shrink-0">
      <div className="p-3 border-b border-white/10">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-700" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Find nodeâ€¦"
            className="w-full bg-white/5 border border-white/10 rounded-xl py-1.5 pl-8 pr-3 text-[11px] focus:outline-none focus:ring-1 focus:ring-white/10 text-slate-400 placeholder:text-slate-700"
          />
        </div>
      </div>

      <div className="px-4 pt-3 pb-1">
        <span className="text-[9px] font-black text-slate-700 uppercase tracking-widest">
          Add to canvas
        </span>
      </div>

      <div className="flex-1 overflow-auto custom-scrollbar p-2 space-y-1">
        {available.length === 0 ? (
          <div className="px-3 py-6 text-center">
            <p className="text-[10px] text-slate-700 italic">All nodes on canvas</p>
          </div>
        ) : (
          available.map(item => (
            <div
              key={item.nodeType}
              draggable
              onDragStart={e => {
                e.dataTransfer.setData('nodeType', item.nodeType);
                e.dataTransfer.effectAllowed = 'move';
              }}
              className="group flex items-center gap-2.5 px-3 py-2.5 rounded-xl hover:bg-white/5 cursor-grab active:cursor-grabbing border border-transparent hover:border-white/10 transition-all"
            >
              <GripVertical className="w-3 h-3 text-slate-700 group-hover:text-slate-500 shrink-0 transition-colors" />
              <div className="p-1.5 rounded-lg shrink-0" style={{ background: `${item.color}20` }}>
                <item.Icon size={11} color={item.color} />
              </div>
              <span className="text-[11px] font-medium text-slate-500 group-hover:text-white transition-colors truncate">
                {item.label}
              </span>
            </div>
          ))
        )}
      </div>

      <div className="p-3 border-t border-white/10">
        <p className="text-[9px] text-slate-700 leading-relaxed">
          KÃ©o node vÃ o canvas Ä‘á»ƒ thÃªm vÃ o workflow
        </p>
      </div>
    </div>
  );
};

// â”€â”€â”€ Context Menu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface CtxMenuState { x: number; y: number; node: Node<FlowNodeData> }

const ContextMenu = ({
  menu, onConfigure, onDuplicate, onDelete, onClose,
}: {
  menu: CtxMenuState;
  onConfigure: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onClose: () => void;
}) => (
  <div
    className="fixed z-[200] min-w-[148px] bg-[#1e293b] border border-white/15 rounded-xl shadow-2xl overflow-hidden"
    style={{ left: menu.x, top: menu.y }}
    onClick={e => e.stopPropagation()}
  >
    <button
      onClick={() => { onConfigure(); onClose(); }}
      className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-bold text-slate-300 hover:bg-white/10 transition-all text-left"
    >
      Configure
    </button>
    <button
      onClick={() => { onDuplicate(); onClose(); }}
      className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-bold text-slate-300 hover:bg-white/10 transition-all text-left"
    >
      <Copy className="w-3 h-3" /> Duplicate
    </button>
    <div className="h-px bg-white/10" />
    <button
      onClick={() => { onDelete(); onClose(); }}
      className="w-full flex items-center gap-2.5 px-4 py-2.5 text-[11px] font-bold text-red-400 hover:bg-red-500/10 transition-all text-left"
    >
      Delete
    </button>
  </div>
);

// â”€â”€â”€ Confirm Exit Dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

const ConfirmExitModal = ({ onConfirm, onCancel }: { onConfirm: () => void; onCancel: () => void }) => {
  const [leaving, setLeaving] = React.useState(false);
  return (
    <div className="fixed inset-0 z-[200] flex items-center justify-center">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={leaving ? undefined : onCancel} />
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        transition={{ type: 'spring', stiffness: 400, damping: 32 }}
        className="relative z-10 bg-[#1e293b] border border-white/15 rounded-2xl p-7 w-[380px] shadow-2xl"
      >
        <h3 className="text-base font-bold text-white mb-2">Leave without saving?</h3>
        <p className="text-sm text-slate-400 leading-relaxed mb-6">
          Unsaved changes will be lost. Are you sure you want to leave?
        </p>
        <div className="flex items-center justify-end gap-3">
          <button
            disabled={leaving}
            onClick={onCancel}
            className="px-4 py-2 text-xs font-bold text-slate-400 hover:text-white hover:bg-white/10 rounded-xl transition-all disabled:opacity-40"
          >
            Stay
          </button>
          <button
            disabled={leaving}
            onClick={async () => {
              setLeaving(true);
              await onConfirm();
            }}
            className="px-4 py-2 text-xs font-bold text-white bg-red-500/80 hover:bg-red-500 rounded-xl transition-all disabled:opacity-40"
          >
            {leaving ? 'Leaving…' : 'Leave'}
          </button>
        </div>
      </motion.div>
    </div>
  );
};

// â”€â”€â”€ Main Builder â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

interface WorkflowVersion {
  id: string;
  version: number;
  status: 'draft' | 'published' | 'archived';
  created_at: string;
  published_at: string | null;
}

export interface BuilderProps {
  onClose: () => void;
  workflow: Workflow | null;
  template?: TemplateId;
  agentId?: string;
  workflowVersionId?: string;
  workflowId?: string;
  isNewWorkflow?: boolean;
  isNewDraft?: boolean;
  onLeave?: () => Promise<void>;
  initialNodes?: any[];
  initialEdges?: any[];
  sourceVersionId?: string;
}

// System nodes không phải user-designed, bỏ khi load từ DB (guardrail là banner, không phải RF node)
const SYSTEM_NODE_TYPES = new Set(['guardrail', 'trigger', 'output']);

const stripSystemNodes = (nodes: any[], edges: any[]) => {
  const sysIds = new Set(
    nodes.filter(n => SYSTEM_NODE_TYPES.has(n.data?.nodeType)).map((n: any) => n.id),
  );
  return {
    nodes: nodes.filter(n => !sysIds.has(n.id)),
    edges: edges.filter(e => !sysIds.has(e.source) && !sysIds.has(e.target)),
  };
};

const withLockedNodes = (nodes: any[]): any[] =>
  nodes.map(n => n.data?.locked ? { ...n, deletable: false } : n);

const withLockedEdges = (edges: any[]): any[] =>
  edges.map(e => e.data?.locked ? { ...e, deletable: false } : e);

const WorkflowBuilder = ({ onClose, workflow, template = 'multi-agent', agentId, workflowVersionId, workflowId, isNewWorkflow, isNewDraft, onLeave, initialNodes, initialEdges, sourceVersionId }: BuilderProps) => {
  const [viewMode, setViewMode]         = useState<'VISUAL' | 'CODE'>('VISUAL');
  const [selectedNode, setSelectedNode] = useState<Node<FlowNodeData> | null>(null);
  const [ctxMenu, setCtxMenu]           = useState<CtxMenuState | null>(null);
  const [showExitConfirm, setShowExitConfirm] = useState(false);
  const [saving, setSaving]             = useState(false);
  const [canvasLoading, setCanvasLoading] = useState(!!workflowVersionId && !initialNodes?.length);
  const [libraryNodes, setLibraryNodes] = useState(ALL_LIBRARY_NODES);
  const rfInstance                       = useRef<ReactFlowInstance<Node<FlowNodeData>, Edge> | null>(null);
  const [versions, setVersions]         = useState<WorkflowVersion[]>([]);
  const [activeVersionId, setActiveVersionId] = useState<string | undefined>(workflowVersionId);
  const [showVersions, setShowVersions] = useState(false);
  const [creatingVersion, setCreatingVersion] = useState(false);
  const savedSnapshot                   = useRef<string>('');

  // Fetch danh sách versions
  useEffect(() => {
    if (!workflowId) return;
    apiGet<WorkflowVersion[]>(`/api/workflows/${workflowId}/versions`)
      .then(setVersions)
      .catch(() => {});
  }, [workflowId]);

  // Load canvas khi activeVersionId thay đổi
  useEffect(() => {
    if (!activeVersionId) return;
    const snap = (ns: any[], es: any[]) => JSON.stringify({
      nodes: ns.map((n: any) => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
      edges: es.map((e: any) => ({ id: e.id, source: e.source, target: e.target, type: e.type, label: e.label })),
    });
    apiGet<any>(`/api/workflow-versions/${activeVersionId}/canvas`)
      .then(data => {
        const hasDbCanvas = data.nodes?.length > 0 || data.edges?.length > 0;
        if (hasDbCanvas) {
          const cleaned = stripSystemNodes(data.nodes, data.edges ?? []);
          setNodes(withLockedNodes(cleaned.nodes));
          setEdges(withLockedEdges(cleaned.edges));
          savedSnapshot.current = snap(cleaned.nodes, cleaned.edges);
          setCanvasLoading(false);
        } else if (initialNodes || initialEdges) {
          const cleaned = stripSystemNodes(initialNodes ?? [], initialEdges ?? []);
          setNodes(withLockedNodes(cleaned.nodes));
          setEdges(withLockedEdges(cleaned.edges));
          savedSnapshot.current = JSON.stringify({ nodes: [], edges: [] });
          setCanvasLoading(false);
        } else if (sourceVersionId) {
          apiGet<any>(`/api/workflow-versions/${sourceVersionId}/canvas`)
            .then(src => {
              const cleaned = stripSystemNodes(src.nodes ?? [], src.edges ?? []);
              setNodes(withLockedNodes(cleaned.nodes));
              setEdges(withLockedEdges(cleaned.edges));
              savedSnapshot.current = JSON.stringify({ nodes: [], edges: [] });
              setCanvasLoading(false);
            })
            .catch(() => { setCanvasLoading(false); });
        } else {
          setNodes([]);
          setEdges([]);
          savedSnapshot.current = JSON.stringify({ nodes: [], edges: [] });
          setCanvasLoading(false);
        }
      })
      .catch(() => { setCanvasLoading(false); });
  }, [activeVersionId]);

  // Fetch node registry từ workflow-runtime
  useEffect(() => {
    apiGet<Record<string, { display: string; category: string; locked: boolean }>>('/api/nodes/registry')
      .then((registry) => {
        const mapped = Object.entries(registry)
          .filter(([, v]) => !v.locked)
          .map(([nodeType, v]) => {
            const existing = ALL_LIBRARY_NODES.find(n => n.nodeType === nodeType);
            return existing ?? {
              nodeType: nodeType as FlowNodeType,
              label: v.display,
              color: '#6B7280',
              Icon: Zap,
              allowMultiple: true,
            };
          });
        if (mapped.length > 0) setLibraryNodes(mapped);
      })
      .catch(() => {});
  }, []);



  // â”€â”€ Toasts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const [toasts, setToasts] = useState<{ id: number; msg: string }[]>([]);
  const addToast = useCallback((msg: string) => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, msg }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 2500);
  }, []);

  const handleSwitchVersion = useCallback((versionId: string) => {
    setActiveVersionId(versionId);
    setShowVersions(false);
  }, []);

  const handleCreateDraftVersion = useCallback(async () => {
    if (!workflowId) return;
    setCreatingVersion(true);
    try {
      const data = await apiMutate<any>('POST', `/api/workflows/${workflowId}/versions`);
      const updated = await apiGet<WorkflowVersion[]>(`/api/workflows/${workflowId}/versions`);
      setVersions(updated);
      setActiveVersionId(data.workflow_version_id);
      addToast(`Tạo draft v${data.version} thành công`);
    } catch { addToast('Tạo version thất bại'); }
    finally { setCreatingVersion(false); }
  }, [workflowId, addToast]);

  const handleDeleteVersion = useCallback(async (versionId: string) => {
    if (!workflowId) return;
    try {
      await apiMutate('DELETE', `/api/workflow-versions/${versionId}`);
      const updated = await apiGet<WorkflowVersion[]>(`/api/workflows/${workflowId}/versions`);
      setVersions(updated);
      if (activeVersionId === versionId) setActiveVersionId(updated[0]?.id);
      addToast('Đã xóa version');
    } catch { addToast('Xóa thất bại'); }
  }, [workflowId, activeVersionId, addToast]);

  // â”€â”€ Flow state â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const initFlow = useMemo(() => {
    // Nếu load từ DB (workflowVersionId có), useEffect sẽ set nodes/edges sau — khởi tạo rỗng tránh flash template
    if (workflowVersionId) return { nodes: [], edges: [] };
    if (workflow && workflow.nodes.length > 0) return transformWorkflowToFlow(workflow);
    return TEMPLATE_FLOWS[template] ?? TEMPLATE_FLOWS['multi-agent'];
  }, [workflow, template, workflowVersionId]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initFlow.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initFlow.edges);

  const usedTypes = useMemo(
    () => new Set(nodes.map(n => (n.data as FlowNodeData).nodeType)),
    [nodes],
  );

  // Metadata Injector đang chọn → viền sáng nhẹ quanh Planner + Responder,
  // để lộ quan hệ "đọc state" (không có cạnh graph thật nào tới Responder —
  // xem onDrop bên dưới) mà không cần vẽ đường nối vắt qua cả canvas.
  const relatedHighlightIds = useMemo(() => {
    if ((selectedNode?.data as FlowNodeData | undefined)?.nodeType !== 'metadata_injector') {
      return new Set<string>();
    }
    return new Set(
      nodes
        .filter(n => ['planner', 'responder'].includes((n.data as FlowNodeData).nodeType))
        .map(n => n.id),
    );
  }, [selectedNode, nodes]);

  const displayNodes = useMemo(
    () => nodes.map(n => relatedHighlightIds.has(n.id)
      ? { ...n, data: { ...n.data, _relatedHighlight: true } }
      : (n.data as FlowNodeData)._relatedHighlight ? { ...n, data: { ...n.data, _relatedHighlight: false } } : n),
    [nodes, relatedHighlightIds],
  );

  // â”€â”€ Connect â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const onConnect = useCallback<OnConnect>(
    conn => {
      const srcType = (nodes.find(n => n.id === conn.source)?.data as FlowNodeData)?.nodeType;
      const tgtType = (nodes.find(n => n.id === conn.target)?.data as FlowNodeData)?.nodeType;
      const guardrailEdge = srcType === 'guardrail' || tgtType === 'guardrail';
      setEdges(eds => addEdge(
        {
          ...conn,
          type: 'wfEdge',
          animated: true,
          ...(guardrailEdge
            ? { deletable: false, data: { locked: true }, style: { stroke: '#EF444455', strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, color: '#EF444455' } }
            : { style: { stroke: '#ffffff22', strokeWidth: 2 }, markerEnd: { type: MarkerType.ArrowClosed, color: '#ffffff44' } }
          ),
        },
        eds,
      ));
    },
    [setEdges, nodes],
  );

  // â”€â”€ Drag & drop â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const normalize = useCallback((ns: typeof nodes, es: typeof edges) => JSON.stringify({
    nodes: ns.map(n => ({ id: n.id, type: n.type, position: n.position, data: n.data })),
    edges: es.map(e => ({ id: e.id, source: e.source, target: e.target, type: e.type, label: (e as any).label })),
  }), []);

  const isDirty = useCallback(
    (currentNodes: typeof nodes, currentEdges: typeof edges) =>
      normalize(currentNodes, currentEdges) !== savedSnapshot.current,
    [normalize],
  );

  const requestExit = useCallback(() => {
    if (!isDirty(nodes, edges)) { onClose(); return; }
    setShowExitConfirm(true);
  }, [nodes, edges, isDirty, onClose]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !showExitConfirm) requestExit();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [showExitConfirm, requestExit]);

  const onDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const nodeType = e.dataTransfer.getData('nodeType') as FlowNodeType;
    if (!nodeType || !rfInstance.current) return;
    const cfg = NODE_STYLE[nodeType];
    if (!cfg) return;
    const id = `${nodeType}-${Date.now()}`;

    // Metadata Injector: vị trí thực thi luôn cố định ngay trước Planner ở
    // backend (graph.py), không đọc cạnh canvas — cạnh vẽ ở đây CHỈ để
    // trực quan, cho người dùng thấy nó "gắn liền" Planner thay vì trôi nổi
    // một mình. Đặt NGAY TRÊN Planner (không phải bên cạnh) vì mọi node
    // trong canvas chỉ có handle nối trên/dưới (luồng dọc) — đặt ngang hàng
    // sẽ ra 1 đường nối vòng xuống-rồi-vòng lên rất rối; đặt trên/dưới cho
    // đường thẳng, khớp hướng render của toàn bộ DAG còn lại. Tự vẽ cạnh
    // khoá (không xoá được), giống cách guardrail node/edge đã làm (xem
    // lockedEdge ở trên).
    if (nodeType === 'metadata_injector') {
      const planner   = nodes.find(n => (n.data as FlowNodeData).nodeType === 'planner');
      const responder = nodes.find(n => (n.data as FlowNodeData).nodeType === 'responder');

      // Canvas trắng (chưa có Planner lẫn Responder) → chặn, không có gì để
      // "gắn liền" vào cả — tránh lặp lại bug đã gặp: kéo Metadata Injector
      // trước, Planner sau, thì cạnh/vị trí neo không tự vẽ lại được (onDrop
      // chỉ chạy đúng 1 lần lúc thả, không theo dõi canvas thay đổi sau đó).
      if (!planner && !responder) {
        addToast('Cần có node Planner hoặc Responder trên canvas trước khi thêm Metadata Injector');
        return;
      }

      // Planner có thật thì neo + vẽ cạnh (cạnh graph thật, xem comment trên).
      // Chỉ có Responder (chưa có Planner) → neo cạnh Responder, KHÔNG vẽ
      // cạnh — cạnh chỉ có ý nghĩa khi trỏ tới Planner thật.
      const anchor = planner ?? responder!;
      setNodes(nds => [...nds, mkNode(id, nodeType, cfg.typeLabel, anchor.position.x, anchor.position.y - 130)]);
      if (planner) {
        setEdges(eds => [...eds, lockedEdge(`e-${id}-planner`, id, planner.id, '#6366F155')]);
      }
      addToast(`Added: ${cfg.typeLabel}`);
      return;
    }

    const pos = rfInstance.current.screenToFlowPosition({ x: e.clientX, y: e.clientY });
    setNodes(nds => [...nds, mkNode(id, nodeType, cfg.typeLabel, pos.x - 89, pos.y - 35)]);
    addToast(`Added: ${cfg.typeLabel}`);
  }, [setNodes, setEdges, addToast, nodes]);

  // â”€â”€ Node interactions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const handleNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode(node as Node<FlowNodeData>);
    setCtxMenu(null);
  }, []);

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setCtxMenu(null);
  }, []);

  const handleNodeContextMenu = useCallback((e: React.MouseEvent, node: Node) => {
    e.preventDefault();
    setCtxMenu({ x: e.clientX, y: e.clientY, node: node as Node<FlowNodeData> });
  }, []);

  // â”€â”€ Config update â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const updateNodeConfig = useCallback((id: string, key: string, value: unknown) => {
    setNodes(nds => nds.map(n => n.id === id ? { ...n, data: { ...n.data, [key]: value } } : n));
    setSelectedNode(prev =>
      prev?.id === id ? { ...prev, data: { ...prev.data, [key]: value } } as Node<FlowNodeData> : prev,
    );
  }, [setNodes]);

  // ── Save canvas ───────────────────────────────────────────────────────────
  const handleSave = useCallback(async () => {
    if (!activeVersionId) { addToast('No workflow version — cannot save'); return; }
    setSaving(true);
    try {
      const payload = {
        // Metadata Injector: dòng key rỗng (nhấn "+" nhưng chưa gõ gì, hoặc
        // xoá dở) không được backend đọc (config_loader._extract_node_config
        // chỉ giữ key non-empty) — dọn trước khi lưu để không lưu rác kiểu
        // "=,=,=" vào canvas, không phải để tránh lỗi (đã an toàn sẵn).
        nodes: nodes.map(n => {
          const data = n.data as FlowNodeData;
          if (data.nodeType === 'metadata_injector') {
            const cleaned = parseKv(data.metadataKv || '').filter(r => r.key.trim() !== '');
            return { id: n.id, type: n.type, position: n.position, data: { ...data, metadataKv: serializeKv(cleaned) } };
          }
          return { id: n.id, type: n.type, position: n.position, data: n.data };
        }),
        edges: edges.map(e => ({ id: e.id, source: e.source, target: e.target, type: e.type, label: e.label })),
      };
      await apiMutate('POST', `/api/workflow-versions/${activeVersionId}/canvas`, payload);
      savedSnapshot.current = normalize(nodes, edges);
      addToast('Canvas saved');
    } catch {
      addToast('Save failed — network error');
    } finally {
      setSaving(false);
    }
  }, [activeVersionId, nodes, edges, normalize, addToast]);

  // ── Delete node (context menu) ────────────────────────────────────────────
  const deleteNode = useCallback((nodeId: string) => {
    const node = nodes.find(n => n.id === nodeId);
    if (!node) return;
    if ((node.data as FlowNodeData).locked) { addToast('Locked node cannot be deleted'); return; }
    addToast(`Node removed: ${(node.data as FlowNodeData).label}`);
    setNodes(nds => nds.filter(n => n.id !== nodeId));
    setEdges(eds => eds.filter(e => e.source !== nodeId && e.target !== nodeId));
    if (selectedNode?.id === nodeId) setSelectedNode(null);
  }, [nodes, setNodes, setEdges, selectedNode, addToast]);

  // ── Delete node (Delete key — fired by React Flow's onNodesDelete) ─────────
  const onNodesDelete = useCallback((deleted: Node[]) => {
    deleted.forEach(n => {
      if ((n.data as FlowNodeData).locked) return;
      addToast(`Node removed: ${(n.data as FlowNodeData).label}`);
    });
  }, [addToast]);

  // â”€â”€ Delete edge (Delete key) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    if (deleted.length > 0) addToast(`Edge${deleted.length > 1 ? 's' : ''} removed`);
  }, [addToast]);

  // â”€â”€ Duplicate node (context menu) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const duplicateNode = useCallback((node: Node<FlowNodeData>) => {
    const id   = `${node.data.nodeType}-${Date.now()}`;
    const copy = mkNode(id, node.data.nodeType, node.data.label, node.position.x + 40, node.position.y + 40, { ...node.data });
    setNodes(nds => [...nds, copy]);
    addToast(`Duplicated: ${node.data.label}`);
  }, [setNodes, addToast]);

  // â”€â”€ YAML preview â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
  const yamlPreview = `name: "${workflow?.name ?? 'New Workflow'}"
version: "${workflow?.version ?? 'v1.0.0-draft'}"
template: "${template}"

nodes:
${nodes.map(n => {
  const d = n.data as FlowNodeData;
  return `  - id: "${n.id}"
    type: "${d.nodeType}"
    label: "${d.label}"`;
}).join('\n')}

edges:
${edges.map(e => `  - from: "${e.source}"  to: "${e.target}"`).join('\n')}`;

  // â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

  return (
    <BuilderToastCtx.Provider value={addToast}>
      <div
        className="fixed inset-0 z-[100] flex flex-col bg-[#0d1117] text-slate-200"
        onClick={() => setCtxMenu(null)}
      >
        {canvasLoading && (
          <div className="absolute inset-0 z-50 flex items-center justify-center bg-[#0d1117]">
            <div className="flex items-center gap-2.5 text-slate-400 text-sm">
              <div className="w-4 h-4 border-2 border-blue-400 border-t-transparent rounded-full animate-spin" />
              Loading canvas…
            </div>
          </div>
        )}
        {/* â”€â”€ Toolbar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        <div className="h-14 border-b border-white/10 bg-[#0d1117] flex items-center justify-between px-5 shrink-0">
          <div className="flex items-center gap-4">
            <button
              onClick={requestExit}
              className="flex items-center gap-2 px-3 py-2 hover:bg-white/10 rounded-xl transition-all text-slate-400 hover:text-white group"
            >
              <ArrowLeft className="w-4 h-4" />
              <span className="text-[10px] font-black uppercase tracking-widest max-w-0 overflow-hidden group-hover:max-w-[3rem] transition-all duration-200 whitespace-nowrap">
                Back
              </span>
            </button>
            <div className="w-px h-5 bg-white/10" />
            <div>
              <div className="text-sm font-bold text-white leading-none">
                {workflow?.name ?? 'New Workflow'}
              </div>
              <div className="text-[9px] font-black text-slate-500 uppercase tracking-widest mt-0.5">
                {nodes.length} nodes · {edges.length} edges
              </div>
            </div>

            {/* Version badge */}
            {versions.length > 0 && (() => {
              const active = versions.find(v => v.id === activeVersionId);
              if (!active) return null;
              return (
                <div className="flex items-center gap-1.5 px-2.5 py-1.5 bg-white/5 border border-white/10 rounded-lg text-xs text-slate-300">
                  <GitBranch className="w-3 h-3" />
                  <span>v{active.version}</span>
                  <span className={`font-bold ${active.status === 'published' ? 'text-emerald-400' : active.status === 'draft' ? 'text-amber-400' : 'text-slate-500'}`}>
                    · {active.status}
                  </span>
                </div>
              );
            })()}
          </div>

          <div className="flex items-center gap-2.5">
            <div className="flex bg-white/5 p-1 rounded-xl border border-white/10">
              {([['VISUAL', Layout, 'Visual'], ['CODE', CodeIcon, 'YAML']] as const).map(([mode, Icon, lbl]) => (
                <button
                  key={mode}
                  onClick={() => setViewMode(mode)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-black uppercase tracking-widest transition-all ${
                    viewMode === mode ? 'bg-white/10 text-white' : 'text-slate-600 hover:text-white'
                  }`}
                >
                  <Icon className="w-3 h-3" />
                  {lbl}
                </button>
              ))}
            </div>

            <button className="flex items-center gap-1.5 px-3.5 py-2 bg-white/5 hover:bg-white/10 border border-white/10 text-slate-400 hover:text-white rounded-xl text-[10px] font-black uppercase tracking-widest transition-all">
              <Play className="w-3 h-3" />
              Dry Run
            </button>

            <button
              onClick={handleSave}
              disabled={saving}
              className="flex items-center gap-1.5 px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white rounded-xl text-[10px] font-black uppercase tracking-widest transition-all shadow-lg shadow-blue-500/20"
            >
              <Save className="w-3 h-3" />
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>

        {/* â”€â”€ Body â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        <div className="flex-1 flex overflow-hidden">

          <NodeLibrary usedTypes={usedTypes} nodes={libraryNodes} />

          {/* Canvas drop zone */}
          <div
            className="flex-1 relative overflow-hidden"
            onDragOver={onDragOver}
            onDrop={onDrop}
          >
            <GuardrailBanner position="top" />
            <GuardrailBanner position="bottom" />
            <AnimatePresence mode="wait">
              {viewMode === 'VISUAL' ? (
                <motion.div
                  key="visual"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.12 }}
                  className="absolute inset-0"
                >
                  <ReactFlow
                    nodes={displayNodes}
                    edges={edges}
                    onNodesChange={onNodesChange}
                    onEdgesChange={onEdgesChange}
                    onConnect={onConnect}
                    onNodeClick={handleNodeClick}
                    onPaneClick={handlePaneClick}
                    onNodeContextMenu={handleNodeContextMenu}
                    onNodesDelete={onNodesDelete}
                    onEdgesDelete={onEdgesDelete}
                    onBeforeDelete={async ({ nodes: ns, edges: es }) => {
                      const hasLocked =
                        ns.some(n => (n.data as any)?.locked) ||
                        es.some(e => (e as any).data?.locked ||
                          (nodes.find(n => n.id === e.source)?.data as any)?.locked ||
                          (nodes.find(n => n.id === e.target)?.data as any)?.locked);
                      return !hasLocked;
                    }}
                    onInit={inst => { rfInstance.current = inst as ReactFlowInstance<Node<FlowNodeData>, Edge>; }}
                    nodeTypes={nodeTypes}
                    edgeTypes={edgeTypes}
                    fitView
                    fitViewOptions={{ padding: 0.2 }}
                    style={{ background: '#0f172a' }}
                    connectionLineStyle={{ stroke: '#ffffff44', strokeWidth: 2 }}
                    deleteKeyCode="Delete"
                  >
                    <Background
                      variant={BackgroundVariant.Dots}
                      color="rgba(255,255,255,0.08)"
                      gap={24}
                      size={1.5}
                    />
                    <Controls
                      style={{
                        background: '#1e293b',
                        border: '1px solid rgba(255,255,255,0.12)',
                        borderRadius: 10,
                        overflow: 'hidden',
                        boxShadow: '0 4px 20px rgba(0,0,0,0.4)',
                      }}
                    />
                  </ReactFlow>
                </motion.div>
              ) : (
                <motion.div
                  key="yaml"
                  initial={{ opacity: 0, x: 10 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: -10 }}
                  transition={{ duration: 0.12 }}
                  className="absolute inset-0 bg-[#111827] p-8 overflow-auto custom-scrollbar"
                >
                  <pre className="font-mono text-sm text-slate-400 leading-relaxed whitespace-pre-wrap">
                    {yamlPreview}
                  </pre>
                </motion.div>
              )}
            </AnimatePresence>
          </div>

          <AnimatePresence>
            {selectedNode && (
              <ConfigPanel
                node={selectedNode}
                onClose={() => setSelectedNode(null)}
                onUpdate={updateNodeConfig}
              />
            )}
          </AnimatePresence>
        </div>

        {/* â”€â”€ Status Bar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        <div className="h-9 bg-[#111827] border-t border-white/10 px-5 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-5">
            <div className="flex items-center gap-1.5">
              <CheckCircle2 className="w-3 h-3 text-emerald-500" />
              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">DAG Valid</span>
            </div>
            <div className="flex items-center gap-1.5">
              <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" />
              <span className="text-[9px] font-black text-slate-600 uppercase tracking-widest">
                {nodes.length} nodes Â· {edges.length} edges
              </span>
            </div>
          </div>
          <button className="flex items-center gap-1.5 text-slate-700 hover:text-slate-400 transition-colors">
            <Terminal className="w-3 h-3" />
            <span className="text-[9px] font-black uppercase tracking-widest">Runtime Logs</span>
          </button>
        </div>

        {/* â”€â”€ Exit Confirm â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        <AnimatePresence>
          {showExitConfirm && (
            <ConfirmExitModal
              onConfirm={async () => {
                if (onLeave) {
                  await onLeave();
                  return;
                }
                if (isNewWorkflow && workflowId) {
                  await apiMutate('DELETE', `/api/workflows/${workflowId}`);
                } else if (isNewDraft && activeVersionId) {
                  await apiMutate('DELETE', `/api/workflow-versions/${activeVersionId}`);
                }
                onClose();
              }}
              onCancel={() => setShowExitConfirm(false)}
            />
          )}
        </AnimatePresence>

        {/* â”€â”€ Context Menu â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        {ctxMenu && (
          <ContextMenu
            menu={ctxMenu}
            onConfigure={() => setSelectedNode(ctxMenu.node)}
            onDuplicate={() => duplicateNode(ctxMenu.node)}
            onDelete={() => deleteNode(ctxMenu.node.id)}
            onClose={() => setCtxMenu(null)}
          />
        )}

        {/* â”€â”€ Toasts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€ */}
        <div className="fixed bottom-12 right-4 z-[300] flex flex-col gap-2 pointer-events-none">
          <AnimatePresence>
            {toasts.map(t => (
              <motion.div
                key={t.id}
                initial={{ opacity: 0, x: 20, scale: 0.92 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: 20, scale: 0.92 }}
                transition={{ duration: 0.16 }}
                className="bg-slate-800 border border-white/10 rounded-xl px-4 py-2.5 text-xs text-slate-300 shadow-xl"
              >
                {t.msg}
              </motion.div>
            ))}
          </AnimatePresence>
        </div>

      </div>
    </BuilderToastCtx.Provider>
  );
};

export default WorkflowBuilder;
