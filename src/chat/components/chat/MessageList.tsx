import { useEffect, useRef, useState } from "react"
import { Pin, FileText, Cpu } from "lucide-react"
import { Message } from "../../lib/types"
import { useChatStore } from "../../lib/store"
import { UserMessage } from "./UserMessage"
import { AIMessage } from "./AIMessage"

const SUMMARIZE_THRESHOLD = 20
const MAX_CONTEXT_TOKENS = 8000

function PinnedBanner({ messages, conversationId }: { messages: Message[]; conversationId: string }) {
  const pinned = messages.filter((m) => m.pinned)
  const [expanded, setExpanded] = useState(false)
  if (pinned.length === 0) return null
  return (
    <div className="mb-4 rounded-xl border border-amber-500/20 bg-amber-500/5 px-3 py-2">
      <button onClick={() => setExpanded(!expanded)} className="flex w-full items-center gap-2 text-xs font-medium text-amber-600 dark:text-amber-400">
        <Pin size={11} />
        {pinned.length} tin nhắn đã ghim
        <span className="ml-auto text-muted-foreground">{expanded ? "▲" : "▼"}</span>
      </button>
      {expanded && (
        <div className="mt-2 flex flex-col gap-2">
          {pinned.map((m) => (
            <div key={m.id} className="rounded-lg border border-border/40 bg-background px-3 py-2 text-xs text-muted-foreground truncate">
              <span className="font-medium text-foreground">{m.role === "user" ? "Bạn" : "Agent"}:</span> {m.content.slice(0, 120)}{m.content.length > 120 ? "…" : ""}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ContextWindowBar({ messages }: { messages: Message[] }) {
  const totalTokens = messages.reduce((sum, m) => sum + (m.tokenCount ?? Math.ceil(m.content.length / 4)), 0)
  const pct = Math.min(100, Math.round((totalTokens / MAX_CONTEXT_TOKENS) * 100))
  const color = pct >= 80 ? "bg-red-500" : pct >= 60 ? "bg-amber-500" : "bg-emerald-500"
  return (
    <div className="flex items-center gap-2 px-4 py-1.5 border-b border-border/30 bg-background/50">
      <Cpu size={11} className="text-muted-foreground flex-shrink-0" />
      <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground tabular-nums whitespace-nowrap">{pct}% context</span>
    </div>
  )
}

export function MessageList({ messages, conversationId }: { messages: Message[]; conversationId: string }) {
  const bottomRef = useRef<HTMLDivElement>(null)
  const setConversationSummary = useChatStore((s) => s.setConversationSummary)
  const conversation = useChatStore((s) => s.conversations.find((c) => c.id === conversationId))
  const [summaryLoading, setSummaryLoading] = useState(false)

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }) }, [messages.length])

  const handleSummarize = async () => {
    setSummaryLoading(true)
    await new Promise((r) => setTimeout(r, 1200))
    const preview = messages
      .filter((m) => m.content)
      .slice(-10)
      .map((m) => `${m.role === "user" ? "User" : "Agent"}: ${m.content.slice(0, 80)}`)
      .join("\n")
    setConversationSummary(conversationId, `Tóm tắt (${messages.length} tin nhắn): ${preview.slice(0, 200)}…`)
    setSummaryLoading(false)
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden">
      {messages.length > 0 && <ContextWindowBar messages={messages} />}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6 flex flex-col gap-5">
          <PinnedBanner messages={messages} conversationId={conversationId} />

          {/* Auto-summarize suggestion */}
          {messages.length >= SUMMARIZE_THRESHOLD && !conversation?.summary && (
            <div className="flex items-center gap-3 rounded-xl border border-border/50 bg-muted/20 px-4 py-3">
              <FileText size={14} className="text-muted-foreground flex-shrink-0" />
              <p className="text-xs text-muted-foreground flex-1">
                Cuộc hội thoại dài ({messages.length} tin nhắn). Tóm tắt để dễ theo dõi?
              </p>
              <button onClick={handleSummarize} disabled={summaryLoading}
                className="rounded-lg border border-border px-3 py-1 text-xs font-medium hover:bg-muted transition-colors disabled:opacity-50">
                {summaryLoading ? "Đang tóm tắt…" : "Tóm tắt"}
              </button>
            </div>
          )}

          {/* Summary banner */}
          {conversation?.summary && (
            <div className="rounded-xl border border-border/50 bg-muted/30 px-4 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground mb-1">Tóm tắt</p>
              <p className="text-xs text-muted-foreground leading-relaxed">{conversation.summary}</p>
            </div>
          )}

          {messages.map((msg) =>
            msg.role === "user"
              ? <UserMessage key={msg.id} message={msg} conversationId={conversationId} />
              : <AIMessage key={msg.id} message={msg} conversationId={conversationId} />
          )}
          <div ref={bottomRef} />
        </div>
      </div>
    </div>
  )
}
