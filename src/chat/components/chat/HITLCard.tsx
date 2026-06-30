import { useState, useRef, useCallback, useEffect } from "react"
import { CheckCircle2, X, Eye, Send, ShieldAlert, Pencil, CornerDownLeft, AlertTriangle } from "lucide-react"
import { useChatStore } from "../../lib/store"
import { HITLAction, HITLChoice } from "../../lib/types"
import { cn } from "../../lib/utils"

interface Props {
  hitl: {
    title: string
    description: string
    actions: HITLAction[]
    expiresAt?: Date
    choices?: HITLChoice[]
  }
  messageId: string
  conversationId: string
}

const ACTION_ICON: Record<HITLAction["variant"], React.ReactNode> = {
  confirm: <Send size={13} />,
  preview: <Eye size={13} />,
  cancel:  <X size={13} />,
}

const ACTION_COLORS: Record<HITLAction["variant"], { text: string; activeText: string; activeBg: string; badge?: string }> = {
  confirm: { text: "#d4d4d8", activeText: "#ffffff", activeBg: "#166534", badge: "#16a34a" },
  preview: { text: "#d4d4d8", activeText: "#fef9c3", activeBg: "#713f12", badge: "#ca8a04" },
  cancel:  { text: "#a1a1aa", activeText: "#f4f4f5", activeBg: "#3f3f46", badge: undefined },
}

/* ── Multiple Choice card (Claude-style) ────────────────── */

export function MultipleChoiceCard({
  title, choices, onSubmit, onSkip,
}: {
  title: string
  choices: HITLChoice[]
  onSubmit: (value: string, label: string) => void
  onSkip: () => void
}) {
  const [cursor, setCursor] = useState(0)           // 0..choices.length-1 = choice; choices.length = Other
  const [otherText, setOtherText] = useState("")
  const [directText, setDirectText] = useState("")
  const otherInputRef = useRef<HTMLInputElement>(null)
  const directRef = useRef<HTMLInputElement>(null)
  const totalRows = choices.length + 1              // choices + "Something else"

  const commit = useCallback((idx: number) => {
    if (idx === choices.length) {
      // "Something else"
      if (otherText.trim()) onSubmit("other", otherText.trim())
      else otherInputRef.current?.focus()
      return
    }
    onSubmit(choices[idx].id, choices[idx].label)
  }, [choices, otherText, onSubmit])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // don't steal keys when typing in inputs
      if (e.target === otherInputRef.current || e.target === directRef.current) return
      if (e.key === "ArrowDown") { e.preventDefault(); setCursor(c => Math.min(c + 1, totalRows - 1)) }
      if (e.key === "ArrowUp")   { e.preventDefault(); setCursor(c => Math.max(c - 1, 0)) }
      if (e.key === "Enter")     { e.preventDefault(); commit(cursor) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [cursor, totalRows, commit])

  // focus "Something else" input when cursor moves there
  useEffect(() => {
    if (cursor === choices.length) {
      setTimeout(() => otherInputRef.current?.focus(), 30)
    }
  }, [cursor, choices.length])

  const handleDirectSubmit = () => {
    if (directText.trim()) onSubmit("direct", directText.trim())
  }

  return (
    <div className="flex flex-col gap-2 w-full">
      {/* Choice card */}
      <div className="rounded-2xl overflow-hidden" style={{ backgroundColor: "#2a2a2a", border: "1px solid #3a3a3a" }}>
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3.5" style={{ borderBottom: "1px solid #3a3a3a" }}>
          <p className="text-sm font-medium" style={{ color: "#e4e4e7" }}>{title}</p>
          <button onClick={onSkip}
            className="flex h-6 w-6 items-center justify-center rounded-md transition-colors"
            style={{ color: "#71717a" }}
            onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
            onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
            <X size={13} />
          </button>
        </div>

        {/* Choices */}
        {choices.map((choice, idx) => {
          const isActive = cursor === idx
          return (
            <button key={choice.id}
              onClick={() => { setCursor(idx); setTimeout(() => commit(idx), 80) }}
              onMouseEnter={() => setCursor(idx)}
              className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors"
              style={{
                borderBottom: "1px solid #3a3a3a",
                backgroundColor: isActive ? "#3a3a3a" : "transparent",
              }}>
              <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
                style={{
                  backgroundColor: isActive ? "#525252" : "#3a3a3a",
                  color: isActive ? "#ffffff" : "#a1a1aa",
                }}>
                {idx + 1}
              </span>
              <span className="flex-1 text-sm" style={{ color: isActive ? "#ffffff" : "#d4d4d8" }}>
                {choice.label}
              </span>
              {choice.badge && (
                <span className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                  style={{ backgroundColor: "#3a3a3a", color: "#a1a1aa", border: "1px solid #525252" }}>
                  {choice.badge}
                </span>
              )}
              {isActive && (
                <CornerDownLeft size={13} style={{ color: "#71717a", flexShrink: 0 }} />
              )}
            </button>
          )
        })}

        {/* Something else row */}
        {(() => {
          const isActive = cursor === choices.length
          return (
            <div
              className="flex items-center gap-3 px-4 py-3"
              style={{ backgroundColor: isActive ? "#3a3a3a" : "transparent" }}
              onClick={() => setCursor(choices.length)}>
              <Pencil size={13} style={{ color: "#71717a", flexShrink: 0 }} />
              <input
                ref={otherInputRef}
                value={otherText}
                onChange={e => setOtherText(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && otherText.trim()) { e.preventDefault(); onSubmit("other", otherText.trim()) } }}
                placeholder="Something else..."
                className="flex-1 bg-transparent text-sm focus:outline-none"
                style={{ color: otherText ? "#e4e4e7" : "#71717a" }}
              />
              {isActive && otherText.trim() && (
                <button onClick={() => onSubmit("other", otherText.trim())}
                  className="rounded-md px-2.5 py-1 text-xs font-semibold transition-colors"
                  style={{ backgroundColor: "#525252", color: "#ffffff" }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#737373")}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#525252")}>
                  Gửi
                </button>
              )}
              {isActive && !otherText.trim() && (
                <button onClick={onSkip}
                  className="rounded-md px-2.5 py-1 text-xs font-semibold transition-colors"
                  style={{ backgroundColor: "#3f3f46", color: "#d4d4d8" }}
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#52525b")}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3f3f46")}>
                  Skip
                </button>
              )}
            </div>
          )
        })()}
      </div>

      {/* Direct reply input */}
      <div className="rounded-2xl px-4 py-3.5" style={{ backgroundColor: "#2a2a2a", border: "1px solid #3a3a3a" }}>
        <input
          ref={directRef}
          value={directText}
          onChange={e => setDirectText(e.target.value)}
          onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); handleDirectSubmit() } }}
          placeholder="Or reply directly..."
          className="w-full bg-transparent text-sm focus:outline-none"
          style={{ color: "#d4d4d8" }}
        />
        {directText.trim() && (
          <div className="flex justify-end mt-2">
            <button onClick={handleDirectSubmit}
              className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-semibold text-white transition-colors"
              style={{ backgroundColor: "#3b82f6" }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2563eb")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3b82f6")}>
              <CornerDownLeft size={11} /> Gửi
            </button>
          </div>
        )}
      </div>

      {/* Keyboard hint */}
      <p className="text-center text-[10px]" style={{ color: "#52525b" }}>
        ↑↓ để điều hướng · Enter để chọn · hoặc nhập trực tiếp bên dưới
      </p>
    </div>
  )
}

/* ── Main HITLCard ──────────────────────────────────────── */

export function HITLCard({ hitl, messageId, conversationId }: Props) {
  const resolveHITL = useChatStore((s) => s.resolveHITL)
  const [cursor, setCursor] = useState(0)
  const [resolved, setResolved] = useState<{ label: string; variant: HITLAction["variant"] } | null>(null)
  const [confirming, setConfirming] = useState<HITLAction | null>(null)

  const actions = hitl.actions

  const commit = useCallback((action: HITLAction) => {
    if (action.variant === "confirm") { setConfirming(action); return }
    resolveHITL(conversationId, messageId, action.id)
    setResolved({ label: action.label, variant: action.variant })
  }, [resolveHITL, conversationId, messageId])

  useEffect(() => {
    if (confirming || resolved) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") { e.preventDefault(); setCursor(c => Math.min(c + 1, actions.length - 1)) }
      if (e.key === "ArrowUp")   { e.preventDefault(); setCursor(c => Math.max(c - 1, 0)) }
      if (e.key === "Enter")     { e.preventDefault(); commit(actions[cursor]) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [cursor, actions, commit, confirming, resolved])

  /* Multiple-choice mode */
  if (hitl.choices && hitl.choices.length > 0 && !resolved) {
    return (
      <MultipleChoiceCard
        title={hitl.title}
        choices={hitl.choices}
        onSubmit={(id, label) => {
          resolveHITL(conversationId, messageId, id)
          setResolved({ label, variant: "confirm" })
        }}
        onSkip={() => {
          resolveHITL(conversationId, messageId, "skip")
          setResolved({ label: "Đã bỏ qua", variant: "cancel" })
        }}
      />
    )
  }

  /* Resolved state */
  if (resolved) {
    const isCancel = resolved.variant === "cancel"
    return (
      <div className="flex items-center gap-3 rounded-2xl px-4 py-3 w-full"
        style={{ backgroundColor: isCancel ? "#27272a" : "#14532d", border: `1px solid ${isCancel ? "#3f3f46" : "#166534"}` }}>
        <span style={{ color: isCancel ? "#a1a1aa" : "#4ade80", flexShrink: 0 }}>
          {isCancel ? <X size={14} /> : <CheckCircle2 size={14} />}
        </span>
        <p className="text-xs font-medium" style={{ color: isCancel ? "#a1a1aa" : "#86efac" }}>
          {isCancel ? resolved.label : `Đã xác nhận: ${resolved.label}`}
        </p>
      </div>
    )
  }

  /* 2-step confirm dialog */
  if (confirming) {
    return (
      <div className="w-full rounded-2xl overflow-hidden" style={{ backgroundColor: "#2a2a2a", border: "1px solid #3a3a3a" }}>
        <div className="flex items-center gap-3 px-4 py-3.5" style={{ borderBottom: "1px solid #3a3a3a" }}>
          <AlertTriangle size={14} style={{ color: "#f59e0b", flexShrink: 0 }} />
          <p className="text-sm font-medium" style={{ color: "#e4e4e7" }}>Xác nhận hành động</p>
        </div>
        <div className="px-4 py-4">
          <p className="text-sm mb-1" style={{ color: "#d4d4d8" }}>
            Bạn chắc chắn muốn <span style={{ color: "#ffffff", fontWeight: 600 }}>"{confirming.label}"</span>?
          </p>
          <p className="text-xs mb-5" style={{ color: "#71717a" }}>Hành động này có thể không hoàn tác được.</p>
          <div className="flex gap-2.5">
            <button
              onClick={() => { resolveHITL(conversationId, messageId, confirming.id); setResolved({ label: confirming.label, variant: "confirm" }) }}
              className="flex flex-1 items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-xs font-semibold transition-colors"
              style={{ backgroundColor: "#166534", color: "#ffffff", border: "1px solid #15803d" }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#15803d")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#166534")}>
              <CheckCircle2 size={13} /> Xác nhận
            </button>
            <button
              onClick={() => setConfirming(null)}
              className="flex items-center gap-2 rounded-xl px-4 py-2.5 text-xs font-medium transition-colors"
              style={{ backgroundColor: "#3f3f46", color: "#d4d4d8", border: "1px solid #52525b" }}
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#52525b")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3f3f46")}>
              <X size={13} /> Quay lại
            </button>
          </div>
        </div>
      </div>
    )
  }

  /* Standard action card — dark theme matching MultipleChoiceCard */
  return (
    <div className="w-full rounded-2xl overflow-hidden" style={{ backgroundColor: "#2a2a2a", border: "1px solid #3a3a3a" }}>
      {/* Header */}
      <div className="flex items-start gap-3 px-4 py-3.5" style={{ borderBottom: "1px solid #3a3a3a" }}>
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg" style={{ backgroundColor: "#3a3a3a" }}>
          <ShieldAlert size={15} style={{ color: "#f59e0b" }} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium" style={{ color: "#e4e4e7" }}>{hitl.title}</p>
          <p className="text-xs mt-1 leading-relaxed" style={{ color: "#a1a1aa" }}>{hitl.description}</p>
        </div>
      </div>

      {/* Action rows */}
      {actions.map((action, idx) => {
        const isActive = cursor === idx
        const cfg = ACTION_COLORS[action.variant]
        return (
          <button key={action.id}
            onClick={() => commit(action)}
            onMouseEnter={() => setCursor(idx)}
            className="flex w-full items-center gap-3 px-4 py-3.5 text-left transition-colors"
            style={{
              borderBottom: idx < actions.length - 1 ? "1px solid #3a3a3a" : undefined,
              backgroundColor: isActive ? cfg.activeBg : "transparent",
            }}>
            <span className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-full text-[11px] font-semibold"
              style={{
                backgroundColor: isActive ? "#525252" : "#3a3a3a",
                color: isActive ? cfg.activeText : cfg.text,
              }}>
              {ACTION_ICON[action.variant]}
            </span>
            <span className="flex-1 text-sm font-medium" style={{ color: isActive ? cfg.activeText : cfg.text }}>
              {action.label}
            </span>
            {action.variant !== "cancel" && cfg.badge && (
              <span className="rounded-full px-2 py-0.5 text-[10px] font-semibold"
                style={{ backgroundColor: "#3a3a3a", color: cfg.badge, border: `1px solid ${cfg.badge}40` }}>
                {action.variant === "confirm" ? "Hành động chính" : "Xem trước"}
              </span>
            )}
            {isActive && (
              <CornerDownLeft size={13} style={{ color: "#71717a", flexShrink: 0 }} />
            )}
          </button>
        )
      })}
    </div>
  )
}
