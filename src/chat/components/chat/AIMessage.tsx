import { useState } from "react"
import { Bot, LayoutDashboard, Pin, Bookmark, Copy, Check, ChevronRight } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { format } from "date-fns"
import { Message } from "../../lib/types"
import { useChatStore } from "../../lib/store"
import { AgentTrace } from "./AgentTrace"
import { CitationBadge } from "./CitationBadge"
import { ChartCard } from "./ChartCard"
import { DataTable } from "./DataTable"
import { cn } from "../../lib/utils"

function ConfidenceBar({ value }: { value: number }) {
  const color = value >= 80 ? "bg-emerald-500" : value >= 60 ? "bg-amber-500" : "bg-red-500"
  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1 rounded-full bg-muted overflow-hidden">
        <div className={cn("h-full rounded-full transition-all", color)} style={{ width: `${value}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground tabular-nums">{value}% tự tin</span>
    </div>
  )
}

export function AIMessage({ message, conversationId }: { message: Message; conversationId: string }) {
  const { artifacts, setActiveArtifact, pinMessage, bookmarkMessage } = useChatStore()
  const { sendMessage } = useChatStore.getState
    ? { sendMessage: undefined }
    : { sendMessage: undefined }
  const linkedArtifacts = artifacts.filter((a) => a.messageId === message.id)
  const [copied, setCopied] = useState(false)
  const [showActions, setShowActions] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  const handleFollowUp = (q: string) => {
    // Dispatch follow-up question to the chat input via custom event
    window.dispatchEvent(new CustomEvent("chat:followup", { detail: q }))
  }

  if (!message.agentTrace && !message.content && !message.hitl) return null

  return (
    <div
      className={cn("group flex gap-3", message.pinned && "ring-1 ring-amber-500/30 rounded-2xl px-2 py-1 -mx-2")}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-violet-600 text-white">
        <Bot size={14} />
      </div>
      <div className="flex flex-col gap-2 max-w-[85%] min-w-0 flex-1">
        {message.agentTrace && <AgentTrace steps={message.agentTrace} />}
        {(message.content || message.isStreaming) && (
          <div className="relative rounded-2xl rounded-tl-sm bg-muted/50 border border-border/40 px-4 py-3 text-sm leading-relaxed prose prose-sm dark:prose-invert max-w-none">
            {message.isStreaming && !message.content ? (
              <div className="flex items-center gap-1.5 py-1">
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
              </div>
            ) : (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
            )}
            {/* Hover action bar */}
            {showActions && !message.isStreaming && (
              <div className="absolute -top-3 right-2 flex items-center gap-0.5 rounded-full border border-border bg-background shadow-sm px-1 py-0.5">
                <button onClick={handleCopy} title="Copy" className="flex h-6 w-6 items-center justify-center rounded-full hover:bg-muted transition-colors text-muted-foreground hover:text-foreground">
                  {copied ? <Check size={11} className="text-emerald-500" /> : <Copy size={11} />}
                </button>
                <button onClick={() => pinMessage(conversationId, message.id, !message.pinned)} title={message.pinned ? "Bỏ ghim" : "Ghim"} className={cn("flex h-6 w-6 items-center justify-center rounded-full hover:bg-muted transition-colors", message.pinned ? "text-amber-500" : "text-muted-foreground hover:text-foreground")}>
                  <Pin size={11} />
                </button>
                <button onClick={() => bookmarkMessage(conversationId, message.id, !message.bookmarked)} title={message.bookmarked ? "Bỏ bookmark" : "Bookmark"} className={cn("flex h-6 w-6 items-center justify-center rounded-full hover:bg-muted transition-colors", message.bookmarked ? "text-blue-500" : "text-muted-foreground hover:text-foreground")}>
                  <Bookmark size={11} />
                </button>
              </div>
            )}
          </div>
        )}
        {linkedArtifacts.length > 0 && (
          <div className="flex flex-wrap gap-2 mt-1">
            {linkedArtifacts.map((art) => (
              <button key={art.id} onClick={() => setActiveArtifact(art.id)}
                className="flex items-center gap-1.5 rounded-lg border border-border/50 bg-muted/50 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors">
                <LayoutDashboard size={12} />
                {art.title}
                <span className="ml-1 text-primary">↗</span>
              </button>
            ))}
          </div>
        )}
        {message.chartData && <ChartCard data={message.chartData} />}
        {message.tableData && <DataTable headers={message.tableData.headers} rows={message.tableData.rows} />}
        {message.hitl && (
          <div className="flex items-center gap-2 rounded-xl border border-dashed px-3 py-2"
            style={{ borderColor: "#3f3f46", backgroundColor: "#18181b" }}>
            <span className="relative flex h-2 w-2 flex-shrink-0">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" style={{ backgroundColor: "#f59e0b" }} />
              <span className="relative inline-flex h-2 w-2 rounded-full" style={{ backgroundColor: "#f59e0b" }} />
            </span>
            <span className="text-xs" style={{ color: "#a1a1aa" }}>Đang chờ xác nhận của bạn...</span>
          </div>
        )}
        {message.citations && message.citations.length > 0 && <CitationBadge citations={message.citations} />}

        {/* Confidence indicator */}
        {message.confidence !== undefined && !message.isStreaming && (
          <ConfidenceBar value={message.confidence} />
        )}

        {/* Follow-up suggestions */}
        {message.followUps && message.followUps.length > 0 && !message.isStreaming && (
          <div className="flex flex-wrap gap-1.5 mt-1">
            {message.followUps.map((q) => (
              <button key={q} onClick={() => handleFollowUp(q)}
                className="flex items-center gap-1 rounded-full border border-border/50 bg-background px-2.5 py-1 text-[11px] text-muted-foreground hover:border-primary/40 hover:text-primary hover:bg-primary/5 transition-all">
                {q}
                <ChevronRight size={10} />
              </button>
            ))}
          </div>
        )}

        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground">{format(new Date(message.timestamp), "HH:mm")}</span>
          {message.pinned && <span className="text-[10px] text-amber-500 flex items-center gap-0.5"><Pin size={9} /> Đã ghim</span>}
          {message.bookmarked && <span className="text-[10px] text-blue-500 flex items-center gap-0.5"><Bookmark size={9} /> Đã đánh dấu</span>}
        </div>
      </div>
    </div>
  )
}
