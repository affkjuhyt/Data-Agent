import { useState, useEffect, lazy, Suspense } from "react"
import {
  X, Download, Copy, Check, BarChart2, TrendingUp, PieChart,
  Database, Table2, GitBranch, FileText, LayoutDashboard, Code2, ImageIcon, Key, Link2
} from "lucide-react"
import {
  BarChart, Bar, LineChart, Line, PieChart as RechartsPie, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip as RechartsTip, Legend, ResponsiveContainer,
} from "recharts"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Artifact, ArtifactType, SchemaColumn } from "../../lib/types"
import { cn } from "../../lib/utils"

const GraphViewLazy = lazy(() => import("./GraphView").then((m) => ({ default: m.GraphView })))

const CHART_COLORS = [
  "#6366f1","#8b5cf6","#ec4899","#f43f5e","#f97316",
  "#eab308","#22c55e","#14b8a6","#0ea5e9","#3b82f6",
]

type ChartVariant = "bar" | "line" | "pie"

const TYPE_ICON: Record<ArtifactType, React.ReactNode> = {
  markdown: <FileText size={14} />,
  html:     <LayoutDashboard size={14} />,
  code:     <Code2 size={14} />,
  chart:    <BarChart2 size={14} />,
  table:    <Table2 size={14} />,
  pdf:      <FileText size={14} />,
  graph:    <GitBranch size={14} />,
  image:    <ImageIcon size={14} />,
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false)
  return (
    <button onClick={() => { navigator.clipboard.writeText(text); setOk(true); setTimeout(() => setOk(false), 2000) }}
      className="flex items-center gap-1.5 rounded-md border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
      {ok ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
      {ok ? "Copied!" : "Copy"}
    </button>
  )
}

function ChartPane({ artifact }: { artifact: Artifact }) {
  const [variant, setVariant] = useState<ChartVariant>("bar")
  if (!artifact.chartData) return null
  const { chartData } = artifact
  const data = chartData.labels.map((name, i) => ({ name, value: chartData.values[i] }))
  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 px-5 py-2.5 border-b border-border/50 flex-shrink-0">
        <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5">
          {([["bar","Cột",<BarChart2 size={12}/>],["line","Đường",<TrendingUp size={12}/>],["pie","Tròn",<PieChart size={12}/>]] as [ChartVariant, string, React.ReactNode][]).map(([v, label, icon]) => (
            <button key={v} onClick={() => setVariant(v)}
              className={cn("flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium transition-colors",
                variant === v ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground")}>
              {icon} {label}
            </button>
          ))}
        </div>
        {chartData.unit && <span className="ml-auto text-[11px] text-muted-foreground bg-muted px-2 py-0.5 rounded-full">{chartData.unit}</span>}
      </div>
      <div className="flex-1 p-5 min-h-0">
        <ResponsiveContainer width="100%" height="100%">
          {variant === "bar" ? (
            <BarChart data={data} margin={{ top: 8, right: 24, left: 8, bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" interval={0} />
              <YAxis tick={{ fontSize: 11 }} />
              <RechartsTip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Bar dataKey="value" radius={[4,4,0,0]}>
                {data.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
              </Bar>
            </BarChart>
          ) : variant === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 24, left: 8, bottom: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
              <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-30} textAnchor="end" interval={0} />
              <YAxis tick={{ fontSize: 11 }} />
              <RechartsTip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend />
              <Line type="monotone" dataKey="value" name="Giá trị" stroke={CHART_COLORS[0]} strokeWidth={2.5} dot={{ r: 4 }} activeDot={{ r: 6 }} />
            </LineChart>
          ) : (
            <RechartsPie>
              <Pie data={data} cx="50%" cy="50%" outerRadius="60%" dataKey="value" nameKey="name"
                label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(1)}%`} labelLine>
                {data.map((_, i) => <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />)}
              </Pie>
              <RechartsTip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend />
            </RechartsPie>
          )}
        </ResponsiveContainer>
      </div>
      <div className="flex items-center gap-4 px-5 py-2.5 border-t border-border/50 bg-muted/30 flex-shrink-0 text-[11px]">
        {[["Tổng", chartData.values.reduce((a,b)=>a+b,0)],["TB", +(chartData.values.reduce((a,b)=>a+b,0)/chartData.values.length).toFixed(2)],["Max", Math.max(...chartData.values)],["Min", Math.min(...chartData.values)]].map(([l,v]) => (
          <div key={l as string} className="flex items-center gap-1">
            <span className="text-muted-foreground">{l}</span>
            <span className="font-mono font-semibold">{v}{chartData.unit ? ` ${chartData.unit}` : ""}</span>
          </div>
        ))}
        <span className="ml-auto text-muted-foreground">{chartData.labels.length} mục</span>
      </div>
    </div>
  )
}

function ColBadge({ col }: { col: SchemaColumn }) {
  return (
    <div className="flex items-center gap-2 py-2 px-4 border-b border-border/30 hover:bg-muted/30 transition-colors group">
      <div className="flex items-center gap-1.5 w-5 flex-shrink-0">
        {col.pk && <span title="Primary Key"><Key size={10} className="text-amber-400" /></span>}
        {col.fk && <span title={`FK → ${col.fk}`}><Link2 size={10} className="text-blue-400" /></span>}
      </div>
      <span className="w-48 text-xs font-mono text-foreground truncate">{col.name}</span>
      <span className="w-36 text-[11px] font-mono text-violet-400 truncate">{col.type}</span>
      <span className={cn("w-16 text-[10px]", col.nullable ? "text-muted-foreground" : "text-emerald-500 font-medium")}>
        {col.nullable ? "NULL" : "NOT NULL"}
      </span>
      <span className="flex-1 text-[11px] text-muted-foreground truncate">{col.fk ? `→ ${col.fk}` : (col.description ?? "")}</span>
    </div>
  )
}

function SchemaPane({ artifact }: { artifact: Artifact }) {
  const [active, setActive] = useState(0)
  const tables = artifact.schemaData ?? []
  if (!tables.length) return <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">Không có schema data</div>
  const table = tables[active]
  return (
    <div className="flex h-full">
      <div className="w-44 flex-shrink-0 border-r border-border/50 flex flex-col overflow-y-auto">
        <p className="px-3 pt-3 pb-2 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Tables</p>
        {tables.map((t, i) => (
          <button key={t.name} onClick={() => setActive(i)}
            className={cn("flex flex-col items-start px-3 py-2.5 text-left border-b border-border/20 transition-colors",
              i === active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted/50")}>
            <div className="flex items-center gap-1.5">
              <Database size={11} />
              <span className="text-xs font-mono font-medium truncate max-w-[100px]">{t.name}</span>
            </div>
            {t.rowCount && <span className="text-[10px] text-muted-foreground/60 mt-0.5 pl-4">{t.rowCount} rows</span>}
          </button>
        ))}
      </div>
      <div className="flex-1 flex flex-col overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-2.5 border-b border-border/50 bg-muted/20 flex-shrink-0">
          <Database size={13} className="text-muted-foreground" />
          <span className="text-xs font-mono font-semibold">{table.name}</span>
          {table.rowCount && <span className="ml-auto text-[11px] text-muted-foreground">{table.rowCount} rows</span>}
        </div>
        <div className="flex items-center gap-2 px-4 py-1.5 border-b border-border/50 bg-muted/30 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground flex-shrink-0">
          <span className="w-5" /><span className="w-48">Column</span>
          <span className="w-36">Type</span><span className="w-16">Nullable</span>
          <span className="flex-1">Description / FK</span>
        </div>
        <div className="flex-1 overflow-y-auto">
          {table.columns.map((col) => <ColBadge key={col.name} col={col} />)}
        </div>
      </div>
    </div>
  )
}

function ImagePane({ artifact }: { artifact: Artifact }) {
  const [zoom, setZoom] = useState(false)
  if (!artifact.imageUrl) return <div className="flex flex-1 items-center justify-center text-muted-foreground">No image URL</div>
  return (
    <div className={cn("flex flex-1 items-center justify-center bg-muted/20 cursor-zoom-in overflow-hidden", zoom && "cursor-zoom-out")}>
      <img src={artifact.imageUrl} alt={artifact.imageAlt ?? artifact.title}
        onClick={() => setZoom(v => !v)}
        className={cn("rounded-lg shadow-lg transition-transform duration-300 max-h-full object-contain", zoom ? "scale-150" : "max-w-full")} />
    </div>
  )
}

export function FullViewModal({ artifact, onClose }: { artifact: Artifact; onClose: () => void }) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", handler)
    return () => window.removeEventListener("keydown", handler)
  }, [onClose])

  const handleDownload = () => {
    if (artifact.imageUrl) {
      const a = document.createElement("a"); a.href = artifact.imageUrl; a.target = "_blank"; a.click(); return
    }
    const text = artifact.content || JSON.stringify(artifact.schemaData, null, 2) || ""
    const ext = artifact.language ?? (artifact.type === "markdown" ? "md" : artifact.type === "html" ? "html" : "txt")
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a"); a.href = url; a.download = `${artifact.title}.${ext}`; a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="relative flex flex-col w-[min(94vw,1100px)] h-[min(90vh,700px)] rounded-2xl bg-background border border-border shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-3 border-b border-border/50 flex-shrink-0">
          <span className="text-muted-foreground">{TYPE_ICON[artifact.type]}</span>
          <span className="flex-1 text-sm font-semibold truncate">{artifact.title}</span>
          <div className="flex items-center gap-1">
            {artifact.content && <CopyBtn text={artifact.content} />}
            <button onClick={handleDownload}
              className="flex items-center gap-1.5 rounded-md border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
              <Download size={11} /> Export
            </button>
            <button onClick={onClose}
              className="ml-1 flex h-7 w-7 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
              <X size={15} />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-hidden flex flex-col">
          {artifact.type === "chart" && artifact.chartData && <ChartPane artifact={artifact} />}
          {artifact.type === "graph" && artifact.graphData && (
            <Suspense fallback={<div className="flex flex-1 items-center justify-center text-muted-foreground text-xs">Loading graph…</div>}>
              <GraphViewLazy graphData={artifact.graphData} />
            </Suspense>
          )}
          {artifact.type === "image" && <ImagePane artifact={artifact} />}
          {artifact.type === "table" && artifact.schemaData && <SchemaPane artifact={artifact} />}
          {artifact.type === "markdown" && (
            <div className="flex-1 overflow-y-auto px-8 py-6 prose prose-sm dark:prose-invert max-w-none">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
            </div>
          )}
          {artifact.type === "html" && (
            <iframe srcDoc={artifact.content} className="w-full flex-1 border-0 bg-white" title={artifact.title} sandbox="allow-scripts" />
          )}
          {artifact.type === "code" && (
            <div className="flex-1 overflow-auto bg-zinc-950">
              <pre className="px-6 py-5 text-xs leading-relaxed font-mono text-zinc-300">{artifact.content}</pre>
            </div>
          )}
          {artifact.type === "table" && !artifact.schemaData && (
            <div className="flex-1 overflow-y-auto px-4 py-3 text-xs text-muted-foreground">{artifact.content}</div>
          )}
        </div>
      </div>
    </div>
  )
}
