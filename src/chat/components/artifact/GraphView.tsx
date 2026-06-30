import { useCallback } from "react"
import {
  ReactFlow, Background, Controls, MiniMap,
  useNodesState, useEdgesState, addEdge,
  type Connection, type NodeProps, Handle, Position, BackgroundVariant,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import { Database, Table2, Image, FileText, User, Cloud, Radio } from "lucide-react"
import { cn } from "../../lib/utils"
import { GraphData, GraphNodeType } from "../../lib/types"

const NODE_CFG: Record<GraphNodeType, { icon: React.ReactNode; color: string; bg: string; border: string }> = {
  database: { icon: <Database size={14} />, color: "text-blue-400",   bg: "bg-blue-500/10",   border: "border-blue-500/40" },
  table:    { icon: <Table2 size={14} />,   color: "text-cyan-400",   bg: "bg-cyan-500/10",   border: "border-cyan-500/40" },
  image:    { icon: <Image size={14} />,    color: "text-violet-400", bg: "bg-violet-500/10", border: "border-violet-500/40" },
  document: { icon: <FileText size={14} />, color: "text-amber-400",  bg: "bg-amber-500/10",  border: "border-amber-500/40" },
  entity:   { icon: <User size={14} />,     color: "text-emerald-400",bg: "bg-emerald-500/10",border: "border-emerald-500/40" },
  api:      { icon: <Cloud size={14} />,    color: "text-pink-400",   bg: "bg-pink-500/10",   border: "border-pink-500/40" },
  stream:   { icon: <Radio size={14} />,    color: "text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/40" },
}

const STATUS_DOT: Record<string, string> = {
  active: "bg-emerald-500",
  inactive: "bg-zinc-500",
  error: "bg-red-500",
}

function GraphNode({ data }: NodeProps) {
  const nodeData = data as { label: string; nodeType: GraphNodeType; description?: string; status?: string; properties?: Record<string, string> }
  const cfg = NODE_CFG[nodeData.nodeType] ?? NODE_CFG.entity

  return (
    <div className={cn("min-w-[140px] max-w-[200px] rounded-xl border px-3 py-2.5 shadow-md backdrop-blur-sm", cfg.bg, cfg.border)}>
      <Handle type="target" position={Position.Top} className="!bg-border !border-border" />
      <div className="flex items-center gap-2 mb-1">
        <span className={cn("flex-shrink-0", cfg.color)}>{cfg.icon}</span>
        <span className="text-xs font-semibold text-foreground truncate leading-tight">{nodeData.label}</span>
        {nodeData.status && (
          <div className={cn("ml-auto h-1.5 w-1.5 flex-shrink-0 rounded-full", STATUS_DOT[nodeData.status] ?? "bg-zinc-500")} />
        )}
      </div>
      {nodeData.description && (
        <p className="text-[10px] text-muted-foreground leading-relaxed truncate pl-[22px]">{nodeData.description}</p>
      )}
      {nodeData.properties && Object.entries(nodeData.properties).length > 0 && (
        <div className="mt-1.5 pl-[22px] flex flex-col gap-0.5">
          {Object.entries(nodeData.properties).slice(0, 3).map(([k, v]) => (
            <div key={k} className="flex gap-1 text-[10px]">
              <span className="text-muted-foreground/60 truncate">{k}:</span>
              <span className="text-muted-foreground truncate font-mono">{v}</span>
            </div>
          ))}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-border !border-border" />
    </div>
  )
}

const NODE_TYPES = { graphNode: GraphNode }

export function GraphView({ graphData }: { graphData: GraphData }) {
  const initialNodes = graphData.nodes.map((n) => ({ ...n, type: "graphNode" }))
  const [nodes, , onNodesChange] = useNodesState(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(
    graphData.edges.map((e) => ({
      ...e,
      style: { stroke: "hsl(var(--border))", strokeWidth: 1.5 },
      labelStyle: { fill: "hsl(var(--muted-foreground))", fontSize: 10 },
      labelBgStyle: { fill: "hsl(var(--background))" },
    }))
  )

  const onConnect = useCallback(
    (params: Connection) => setEdges((eds) => addEdge(params, eds)),
    [setEdges]
  )

  return (
    <div className="w-full h-full bg-background">
      <ReactFlow
        nodes={nodes} edges={edges}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onConnect={onConnect} nodeTypes={NODE_TYPES}
        fitView fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3} maxZoom={2}
        proOptions={{ hideAttribution: true }}>
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="hsl(var(--border))" className="opacity-40" />
        <Controls className="[&_button]:bg-background [&_button]:border-border [&_button]:text-foreground [&_button:hover]:bg-muted" showInteractive={false} />
        <MiniMap className="!bg-background !border !border-border/50 !rounded-xl overflow-hidden"
          nodeColor={(n) => {
            const nodeType = (n.data as { nodeType?: GraphNodeType })?.nodeType
            const colors: Record<GraphNodeType, string> = {
              database: "#60a5fa", table: "#22d3ee", image: "#a78bfa",
              document: "#fbbf24", entity: "#34d399", api: "#f472b6", stream: "#fb923c",
            }
            return nodeType ? colors[nodeType] ?? "#94a3b8" : "#94a3b8"
          }}
          maskColor="hsl(var(--background) / 0.7)"
        />
      </ReactFlow>
    </div>
  )
}
