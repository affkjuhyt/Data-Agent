import { useState } from "react"
import { CheckCircle2, Circle, Loader2, AlertCircle, ChevronDown, ChevronRight } from "lucide-react"
import { AgentStep, AgentStepStatus } from "../../lib/types"
import { cn } from "../../lib/utils"

const STATUS_CFG: Record<AgentStepStatus, { icon: React.ReactNode; color: string }> = {
  done:    { icon: <CheckCircle2 size={13} />, color: "text-emerald-500" },
  running: { icon: <Loader2 size={13} className="animate-spin" />, color: "text-blue-500" },
  pending: { icon: <Circle size={13} />, color: "text-muted-foreground/40" },
  error:   { icon: <AlertCircle size={13} />, color: "text-destructive" },
}

function StepItem({ step }: { step: AgentStep }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = STATUS_CFG[step.status]
  return (
    <div>
      <div className={cn("flex items-center gap-2 py-1.5 px-1 rounded", step.detail && "cursor-pointer hover:bg-muted/50")}
        onClick={() => step.detail && setExpanded(!expanded)}>
        <span className={cfg.color}>{cfg.icon}</span>
        <span className={cn("text-xs flex-1", step.status === "pending" ? "text-muted-foreground/40" : "text-foreground")}>
          <span className="font-medium text-muted-foreground">{step.workerName}</span> — {step.description}
        </span>
        {step.durationMs && <span className="text-[10px] text-muted-foreground">{(step.durationMs/1000).toFixed(1)}s</span>}
        {step.detail && (expanded ? <ChevronDown size={12} className="text-muted-foreground" /> : <ChevronRight size={12} className="text-muted-foreground" />)}
      </div>
      {expanded && step.detail && (
        <pre className="mx-5 mt-1 mb-2 rounded-md bg-muted px-3 py-2 text-[11px] font-mono text-muted-foreground overflow-x-auto">{step.detail}</pre>
      )}
    </div>
  )
}

export function AgentTrace({ steps }: { steps: AgentStep[] }) {
  const doneCount = steps.filter(s => s.status === "done").length
  const isComplete = steps.every(s => s.status === "done" || s.status === "error")
  return (
    <div className="rounded-xl border border-border/50 bg-muted/30 p-3">
      <div className="mb-2 flex items-center gap-2">
        {!isComplete && <Loader2 size={12} className="animate-spin text-blue-500" />}
        <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wide">
          {isComplete ? "Hoàn thành" : `Đang xử lý — ${doneCount}/${steps.length} bước`}
        </span>
      </div>
      <div className="divide-y divide-border/30">{steps.map(s => <StepItem key={s.id} step={s} />)}</div>
    </div>
  )
}
