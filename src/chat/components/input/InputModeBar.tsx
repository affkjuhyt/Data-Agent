import { MessageSquare, Database, Code2, Upload, Mic } from "lucide-react"
import { InputMode, AgentDomain } from "../../lib/types"
import { cn } from "../../lib/utils"
import { useChatStore } from "../../lib/store"
import { ToolPicker, EnabledToolChips } from "./ToolPicker"
import { SkillPicker, ActiveSkillChips } from "./SkillPicker"

const MODES: { id: InputMode; icon: React.ReactNode; label: string }[] = [
  { id: "text",   icon: <MessageSquare size={12} />, label: "Text" },
  { id: "sql",    icon: <Database size={12} />,      label: "SQL" },
  { id: "python", icon: <Code2 size={12} />,         label: "Python" },
  { id: "file",   icon: <Upload size={12} />,        label: "File" },
  { id: "voice",  icon: <Mic size={12} />,           label: "Voice" },
]

const DOMAINS: { id: AgentDomain; label: string }[] = [
  { id: "stockllm", label: "StockLLM" },
  { id: "enterprise", label: "Enterprise" },
  { id: "gov", label: "Gov" },
  { id: "document", label: "Document" },
  { id: "forecast", label: "Forecast" },
]

export function InputModeBar() {
  const { ui, setInputMode, setSelectedDomain } = useChatStore()
  return (
    <div>
      <div className="flex items-center justify-between gap-3 px-4 pb-1 pt-1">
        <div className="flex items-center gap-1 flex-wrap">
          {MODES.map((m) => (
            <button key={m.id} onClick={() => setInputMode(m.id)}
              className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs transition-colors border",
                ui.currentInputMode === m.id
                  ? "border-primary/30 bg-primary/10 text-primary font-medium"
                  : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted")}>
              {m.icon}{m.label}
            </button>
          ))}
          <div className="w-px h-4 bg-border/50 mx-1" />
          <ToolPicker />
          <SkillPicker />
        </div>
        <select value={ui.selectedDomain ?? "stockllm"}
          onChange={(e) => setSelectedDomain(e.target.value as AgentDomain)}
          className="text-xs border border-border/50 rounded-lg px-2 py-1 bg-background text-muted-foreground focus:outline-none focus:border-primary/50 cursor-pointer">
          {DOMAINS.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}
        </select>
      </div>
      <EnabledToolChips />
      <ActiveSkillChips />
    </div>
  )
}
