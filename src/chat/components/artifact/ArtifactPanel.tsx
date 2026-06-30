import { useState, useRef, useEffect, lazy, Suspense } from "react"
import {
  X, Plus, Copy, Download, Check, Code2, FileText,
  LayoutDashboard, Table2, GitBranch, ImageIcon, Maximize2,
} from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { useChatStore } from "../../lib/store"
import { Artifact, ArtifactType } from "../../lib/types"
import { cn } from "../../lib/utils"
import { FullViewModal } from "./FullViewModal"

const GraphViewLazy = lazy(() => import("./GraphView").then((m) => ({ default: m.GraphView })))

const TYPE_CFG: Record<ArtifactType, { icon: React.ReactNode; label: string; color: string }> = {
  markdown: { icon: <FileText size={12} />,       label: "MD",    color: "text-blue-400" },
  html:     { icon: <LayoutDashboard size={12} />, label: "HTML",  color: "text-emerald-400" },
  code:     { icon: <Code2 size={12} />,           label: "Code",  color: "text-amber-400" },
  chart:    { icon: <LayoutDashboard size={12} />, label: "Chart", color: "text-violet-400" },
  table:    { icon: <Table2 size={12} />,          label: "Table", color: "text-cyan-400" },
  pdf:      { icon: <FileText size={12} />,        label: "PDF",   color: "text-red-400" },
  graph:    { icon: <GitBranch size={12} />,       label: "Graph", color: "text-pink-400" },
  image:    { icon: <ImageIcon size={12} />,       label: "Image", color: "text-orange-400" },
}

const LANG_COLOR: Record<string, string> = {
  sql: "text-amber-300", python: "text-blue-300", typescript: "text-blue-200",
  javascript: "text-yellow-300",
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      className="flex items-center gap-1.5 rounded-md border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
      {copied ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
      {copied ? "Copied!" : "Copy"}
    </button>
  )
}

function TabBar() {
  const { artifacts, openArtifactIds, activeArtifactId, openArtifact, closeArtifact } = useChatStore()
  const openTabs = openArtifactIds.map((id) => artifacts.find((a) => a.id === id)).filter(Boolean) as Artifact[]
  const closedArtifacts = artifacts.filter((a) => !openArtifactIds.includes(a.id))
  const [showAdd, setShowAdd] = useState(false)
  const addRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const onClickOutside = (e: MouseEvent) => {
      if (addRef.current && !addRef.current.contains(e.target as Node)) setShowAdd(false)
    }
    if (showAdd) document.addEventListener("mousedown", onClickOutside)
    return () => document.removeEventListener("mousedown", onClickOutside)
  }, [showAdd])

  return (
    <div className="flex items-center border-b border-border/50 bg-muted/20 overflow-x-auto min-h-[38px]">
      {openTabs.map((art) => {
        const cfg = TYPE_CFG[art.type]
        const isActive = art.id === activeArtifactId
        return (
          <div key={art.id}
            className={cn("flex items-center gap-1.5 px-3 py-2 border-r border-border/30 cursor-pointer select-none flex-shrink-0 group transition-colors",
              isActive ? "bg-background border-b-2 border-b-primary -mb-px" : "hover:bg-muted/50 text-muted-foreground")}
            onClick={() => openArtifact(art.id)}>
            <span className={cn("flex-shrink-0", cfg.color)}>{cfg.icon}</span>
            <span className="text-xs font-medium max-w-[120px] truncate">{art.title}</span>
            <button className="ml-1 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded text-muted-foreground opacity-0 group-hover:opacity-100 hover:bg-muted hover:text-foreground transition-all"
              onClick={(e) => { e.stopPropagation(); closeArtifact(art.id) }}>
              <X size={10} />
            </button>
          </div>
        )
      })}
      <div className="relative flex-shrink-0" ref={addRef}>
        <button className="flex h-full items-center px-3 py-2 text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors"
          onClick={() => setShowAdd(!showAdd)}>
          <Plus size={13} />
        </button>
        {showAdd && closedArtifacts.length > 0 && (
          <div className="absolute left-0 top-full z-50 mt-1 w-52 rounded-xl border border-border/50 bg-background shadow-xl py-1">
            <p className="px-3 py-1.5 text-[10px] text-muted-foreground uppercase tracking-wider font-medium">Mở artifact</p>
            {closedArtifacts.map((art) => {
              const cfg = TYPE_CFG[art.type]
              return (
                <button key={art.id}
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-xs text-foreground hover:bg-muted transition-colors"
                  onClick={() => { openArtifact(art.id); setShowAdd(false) }}>
                  <span className={cfg.color}>{cfg.icon}</span>
                  <span className="truncate">{art.title}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function PreviewPane({ artifact }: { artifact: Artifact }) {
  if (artifact.type === "graph" && artifact.graphData)
    return (
      <Suspense fallback={<div className="flex flex-1 items-center justify-center text-muted-foreground text-xs">Loading graph…</div>}>
        <GraphViewLazy graphData={artifact.graphData} />
      </Suspense>
    )
  if (artifact.type === "html")
    return <iframe srcDoc={artifact.content} className="w-full flex-1 border-0 bg-white" title={artifact.title} sandbox="allow-scripts" />
  if (artifact.type === "image" && artifact.imageUrl)
    return (
      <div className="flex flex-1 items-center justify-center bg-muted/20 p-4 overflow-hidden">
        <img src={artifact.imageUrl} alt={artifact.imageAlt ?? artifact.title}
          className="max-w-full max-h-full rounded-lg object-contain shadow" />
      </div>
    )
  if (artifact.type === "table" && artifact.schemaData) {
    const first = artifact.schemaData[0]
    return (
      <div className="flex-1 overflow-auto">
        <div className="px-3 pt-3 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
          {artifact.schemaData.length} tables · {first?.name}
        </div>
        <div className="px-3 pb-3 flex flex-col gap-1">
          {first?.columns.map((col) => (
            <div key={col.name} className="flex items-center gap-2 text-[11px] py-1 border-b border-border/20">
              <span className="font-mono text-foreground w-28 truncate">{col.name}</span>
              <span className="font-mono text-violet-400 text-[10px]">{col.type}</span>
              {col.pk && <span className="text-[9px] bg-amber-500/20 text-amber-400 px-1 rounded">PK</span>}
            </div>
          ))}
          {artifact.schemaData.length > 1 && (
            <p className="text-[10px] text-muted-foreground mt-1">+{artifact.schemaData.length - 1} tables — mở full view để xem thêm</p>
          )}
        </div>
      </div>
    )
  }
  if (artifact.type === "markdown")
    return (
      <div className="flex-1 overflow-y-auto px-6 py-5 prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{artifact.content}</ReactMarkdown>
      </div>
    )
  return <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">Preview không khả dụng</div>
}

function CodePane({ artifact }: { artifact: Artifact }) {
  const color = artifact.language ? (LANG_COLOR[artifact.language] ?? "text-zinc-300") : "text-zinc-300"
  return (
    <div className="flex-1 overflow-auto bg-zinc-950">
      <pre className={cn("px-5 py-4 text-xs leading-relaxed font-mono", color)}>{artifact.content}</pre>
    </div>
  )
}

const ARTIFACT_W = { default: 440, min: 280, max: 700 }

export function ArtifactPanel() {
  const { artifacts, activeArtifactId, isArtifactPanelOpen, toggleArtifactPanel } = useChatStore()
  const [viewMode, setViewMode] = useState<"preview" | "code">("preview")
  const [fullViewOpen, setFullViewOpen] = useState(false)
  const [width, setWidth] = useState(ARTIFACT_W.default)
  const drag = useRef<{ startX: number; startW: number } | null>(null)

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!drag.current) return
      const w = Math.min(ARTIFACT_W.max, Math.max(ARTIFACT_W.min, drag.current.startW - (e.clientX - drag.current.startX)))
      setWidth(w)
    }
    const onUp = () => {
      if (!drag.current) return
      drag.current = null
      document.body.style.cursor = ""
      document.body.style.userSelect = ""
    }
    window.addEventListener("mousemove", onMove)
    window.addEventListener("mouseup", onUp)
    return () => {
      window.removeEventListener("mousemove", onMove)
      window.removeEventListener("mouseup", onUp)
    }
  }, [])

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    drag.current = { startX: e.clientX, startW: width }
    document.body.style.cursor = "col-resize"
    document.body.style.userSelect = "none"
  }

  const activeArtifact = artifacts.find((a) => a.id === activeArtifactId)
  const cfg = activeArtifact ? TYPE_CFG[activeArtifact.type] : null
  const hasPreview = activeArtifact && ["markdown", "html", "chart", "table", "graph", "image"].includes(activeArtifact.type)
  const hasCode = activeArtifact && ["markdown", "html", "code", "table"].includes(activeArtifact.type)

  if (!isArtifactPanelOpen) return null

  return (
    <div className="relative flex h-full flex-shrink-0 flex-col border-l border-border/50 bg-background" style={{ width }}>
      <div onMouseDown={startDrag}
        className="absolute top-0 left-0 h-full w-1.5 cursor-col-resize z-20 group select-none">
        <div className="absolute inset-y-0 left-0 w-px bg-border/60 group-hover:bg-primary/50 transition-colors duration-150" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col gap-[3px] opacity-0 group-hover:opacity-100 transition-opacity">
          {[0, 1, 2].map((i) => <div key={i} className="h-4 w-[2px] rounded-full bg-primary/50" />)}
        </div>
      </div>

      <div className="flex h-10 flex-shrink-0 items-center gap-2 border-b border-border/50 px-3">
        <span className={cn("text-muted-foreground", cfg?.color ?? "")}>{cfg?.icon}</span>
        <span className="flex-1 text-xs font-medium truncate text-muted-foreground">{activeArtifact?.title ?? "Artifact"}</span>
        {activeArtifact && (
          <button onClick={() => setFullViewOpen(true)} title="Xem toàn màn hình"
            className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
            <Maximize2 size={12} />
          </button>
        )}
        <button onClick={toggleArtifactPanel}
          className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
          <X size={13} />
        </button>
      </div>

      <TabBar />

      {hasPreview && hasCode && (
        <div className="flex flex-shrink-0 border-b border-border/50">
          {(["preview", "code"] as const).map((m) => (
            <button key={m} onClick={() => setViewMode(m)}
              className={cn("flex-1 py-2 text-xs font-medium transition-colors border-b-2",
                viewMode === m ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}>
              {m === "preview" ? "Preview" : "Source"}
            </button>
          ))}
        </div>
      )}

      <div className="flex flex-1 flex-col overflow-hidden">
        {activeArtifact ? (
          (viewMode === "preview" && hasPreview) || !hasCode
            ? <PreviewPane artifact={activeArtifact} />
            : <CodePane artifact={activeArtifact} />
        ) : (
          <div className="flex flex-1 items-center justify-center text-muted-foreground text-sm">
            Chọn artifact để xem
          </div>
        )}
      </div>

      {activeArtifact && (
        <div className="flex flex-shrink-0 items-center justify-between border-t border-border/50 px-4 py-2.5">
          <span className="text-[10px] font-mono text-muted-foreground">
            {activeArtifact.language ?? activeArtifact.type} · {activeArtifact.content.length} chars
          </span>
          <div className="flex gap-2">
            <CopyButton text={activeArtifact.content} />
            <button className="flex items-center gap-1.5 rounded-md border border-border/50 px-2.5 py-1.5 text-xs text-muted-foreground hover:bg-muted transition-colors">
              <Download size={11} /> Export
            </button>
          </div>
        </div>
      )}

      {fullViewOpen && activeArtifact && (
        <FullViewModal artifact={activeArtifact} onClose={() => setFullViewOpen(false)} />
      )}
    </div>
  )
}
