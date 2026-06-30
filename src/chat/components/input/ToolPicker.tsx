import { useState, useRef, useEffect } from "react"
import { Wrench, X, ChevronDown, AlertTriangle, Check } from "lucide-react"
import { useChatStore } from "../../lib/store"
import { MCP_TOOLS } from "../../lib/mock-data"
import { MCPTool } from "../../lib/types"
import { cn } from "../../lib/utils"

const CATEGORY_LABEL: Record<string, string> = {
  data:     "Data & Query",
  gov:      "Dịch vụ công",
  analysis: "Phân tích",
  output:   "Output",
}

function ToolCheckItem({ tool, enabled, onToggle }: { tool: MCPTool; enabled: boolean; onToggle: () => void }) {
  return (
    <button onClick={onToggle}
      className={cn("flex w-full items-start gap-3 rounded-lg px-3 py-2.5 text-left transition-colors",
        enabled ? "bg-primary/10" : "hover:bg-muted/50")}>
      <div className={cn("mt-0.5 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-colors",
        enabled ? "border-primary bg-primary" : "border-border/50 bg-background")}>
        {enabled && <Check size={10} className="text-white" />}
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-sm">{tool.icon}</span>
          <span className="text-xs font-medium text-foreground">{tool.name}</span>
          {tool.isSensitive && (
            <span className="flex items-center gap-0.5 rounded px-1 py-0.5 text-[9px] font-medium bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-400">
              <AlertTriangle size={8} /> Write
            </span>
          )}
          {tool.isDefault && (
            <span className="rounded px-1 py-0.5 text-[9px] bg-muted text-muted-foreground">Default</span>
          )}
        </div>
        <p className="text-[10px] text-muted-foreground mt-0.5 leading-relaxed">{tool.description}</p>
      </div>
    </button>
  )
}

export function EnabledToolChips() {
  const { enabledToolIds, toggleTool } = useChatStore()
  const enabledTools = MCP_TOOLS.filter(t => enabledToolIds.includes(t.id))
  if (enabledTools.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-4 pb-2">
      <span className="text-[10px] text-muted-foreground">Tools:</span>
      {enabledTools.map(tool => (
        <span key={tool.id}
          className="flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-[10px] text-primary">
          {tool.icon} {tool.name}
          <button onClick={() => toggleTool(tool.id)} className="ml-0.5 hover:text-primary/70 transition-colors">
            <X size={9} />
          </button>
        </span>
      ))}
    </div>
  )
}

export function ToolPicker() {
  const { ui, enabledToolIds, toggleTool } = useChatStore()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const domainTools = ui.selectedDomain
    ? MCP_TOOLS.filter(t => t.domain.includes(ui.selectedDomain!))
    : MCP_TOOLS

  const grouped = domainTools.reduce<Record<string, MCPTool[]>>((acc, tool) => {
    if (!acc[tool.category]) acc[tool.category] = []
    acc[tool.category].push(tool)
    return acc
  }, {})

  const enabledCount = enabledToolIds.filter(id => domainTools.some(t => t.id === id)).length

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)}
        className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs border transition-colors",
          enabledCount > 0
            ? "border-primary/40 bg-primary/10 text-primary font-medium"
            : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted")}>
        <Wrench size={11} />
        Tools
        {enabledCount > 0 && (
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-primary text-[9px] text-white font-bold">
            {enabledCount}
          </span>
        )}
        <ChevronDown size={10} className={cn("transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-2 z-50 w-72 rounded-2xl border border-border/60 bg-background shadow-2xl overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50 bg-muted/20">
            <div>
              <p className="text-xs font-semibold">MCP Tools</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">{enabledCount} đang bật · {domainTools.length} khả dụng</p>
            </div>
            <button
              onClick={() => {
                const allIds = domainTools.map(t => t.id)
                const allEnabled = allIds.every(id => enabledToolIds.includes(id))
                if (allEnabled) allIds.forEach(id => { if (enabledToolIds.includes(id)) toggleTool(id) })
                else allIds.forEach(id => { if (!enabledToolIds.includes(id)) toggleTool(id) })
              }}
              className="text-[10px] text-primary hover:underline">
              {domainTools.every(t => enabledToolIds.includes(t.id)) ? "Bỏ tất cả" : "Chọn tất cả"}
            </button>
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {Object.entries(grouped).map(([cat, tools]) => (
              <div key={cat}>
                <p className="px-3 pt-2.5 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {CATEGORY_LABEL[cat] ?? cat}
                </p>
                {tools.map(tool => (
                  <ToolCheckItem key={tool.id} tool={tool} enabled={enabledToolIds.includes(tool.id)} onToggle={() => toggleTool(tool.id)} />
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
