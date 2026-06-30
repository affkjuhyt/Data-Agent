import { useState, useRef, useEffect } from "react"
import { Sparkles, X, ChevronDown, Check } from "lucide-react"
import { useChatStore } from "../../lib/store"
import { AGENT_SKILLS } from "../../lib/mock-data"
import { AgentSkill } from "../../lib/types"
import { cn } from "../../lib/utils"

const CATEGORY_LABEL: Record<string, string> = {
  output:   "📤 Output",
  behavior: "🧠 Hành vi",
  analysis: "🔬 Phân tích",
  language: "🌐 Ngôn ngữ",
}

function SkillCard({ skill, active, onToggle }: { skill: AgentSkill; active: boolean; onToggle: () => void }) {
  const [showPrompt, setShowPrompt] = useState(false)

  return (
    <div className="relative">
      <button onClick={onToggle}
        onMouseEnter={() => setShowPrompt(true)}
        onMouseLeave={() => setShowPrompt(false)}
        className={cn("relative flex w-full flex-col gap-1 rounded-xl border p-2.5 text-left transition-all",
          active ? "border-primary/50 bg-primary/10 shadow-sm shadow-primary/10" : "border-border/50 hover:border-border hover:bg-muted/30")}>
        <div className="flex items-center justify-between gap-1">
          <span className="text-base leading-none">{skill.icon}</span>
          {active && <Check size={11} className="text-primary flex-shrink-0" />}
        </div>
        <p className="text-xs font-semibold leading-tight">{skill.name}</p>
        <p className="text-[10px] text-muted-foreground leading-tight">{skill.description}</p>
      </button>
      {showPrompt && (
        <div className="absolute bottom-full left-0 mb-2 z-[60] w-56 rounded-xl border border-border/60 bg-background p-3 shadow-2xl">
          <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">Prompt inject</p>
          <p className="text-[11px] text-foreground leading-relaxed italic">"{skill.promptTemplate}"</p>
        </div>
      )}
    </div>
  )
}

export function ActiveSkillChips() {
  const { activeSkillIds, toggleSkill } = useChatStore()
  const activeSkills = AGENT_SKILLS.filter(s => activeSkillIds.includes(s.id))
  if (activeSkills.length === 0) return null

  return (
    <div className="flex flex-wrap items-center gap-1.5 px-4 pb-2">
      <span className="text-[10px] text-muted-foreground">Skills:</span>
      {activeSkills.map(skill => (
        <span key={skill.id}
          className={cn("flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium", skill.color)}>
          {skill.icon} {skill.name}
          <button onClick={() => toggleSkill(skill.id)} className="ml-0.5 opacity-70 hover:opacity-100 transition-opacity">
            <X size={9} />
          </button>
        </span>
      ))}
    </div>
  )
}

export function SkillPicker() {
  const { ui, activeSkillIds, toggleSkill, clearSkills } = useChatStore()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [])

  const domainSkills = ui.selectedDomain
    ? AGENT_SKILLS.filter(s => s.domain.includes(ui.selectedDomain!))
    : AGENT_SKILLS

  const grouped = domainSkills.reduce<Record<string, AgentSkill[]>>((acc, skill) => {
    if (!acc[skill.category]) acc[skill.category] = []
    acc[skill.category].push(skill)
    return acc
  }, {})

  const activeCount = activeSkillIds.filter(id => domainSkills.some(s => s.id === id)).length

  return (
    <div className="relative" ref={ref}>
      <button onClick={() => setOpen(!open)}
        className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs border transition-colors",
          activeCount > 0
            ? "border-violet-500/40 bg-violet-500/10 text-violet-500 font-medium"
            : "border-transparent text-muted-foreground hover:text-foreground hover:bg-muted")}>
        <Sparkles size={11} />
        Skills
        {activeCount > 0 && (
          <span className="flex h-4 w-4 items-center justify-center rounded-full bg-violet-500 text-[9px] text-white font-bold">
            {activeCount}
          </span>
        )}
        <ChevronDown size={10} className={cn("transition-transform", open && "rotate-180")} />
      </button>

      {open && (
        <div className="absolute bottom-full left-0 mb-2 z-50 w-80 rounded-2xl border border-border/60 bg-background shadow-2xl overflow-hidden">
          <div className="flex items-center justify-between px-3 py-2.5 border-b border-border/50 bg-muted/20">
            <div>
              <p className="text-xs font-semibold">Agent Skills</p>
              <p className="text-[10px] text-muted-foreground mt-0.5">{activeCount} đang bật · Hover để xem prompt</p>
            </div>
            {activeCount > 0 && (
              <button onClick={clearSkills} className="text-[10px] text-muted-foreground hover:text-red-400 transition-colors">
                Xoá tất cả
              </button>
            )}
          </div>
          <div className="max-h-80 overflow-y-auto p-3 space-y-4">
            {Object.entries(grouped).map(([cat, skills]) => (
              <div key={cat}>
                <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-2">
                  {CATEGORY_LABEL[cat] ?? cat}
                </p>
                <div className="grid grid-cols-2 gap-1.5">
                  {skills.map(skill => (
                    <SkillCard key={skill.id} skill={skill} active={activeSkillIds.includes(skill.id)} onToggle={() => toggleSkill(skill.id)} />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
