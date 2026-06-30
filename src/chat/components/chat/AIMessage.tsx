import { Bot, LayoutDashboard } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { format } from "date-fns"
import { Message } from "../../lib/types"
import { useChatStore } from "../../lib/store"
import { AgentTrace } from "./AgentTrace"
import { CitationBadge } from "./CitationBadge"
import { ChartCard } from "./ChartCard"
import { DataTable } from "./DataTable"

export function AIMessage({ message, conversationId }: { message: Message; conversationId: string }) {
  const { artifacts, setActiveArtifact } = useChatStore()
  const linkedArtifacts = artifacts.filter((a) => a.messageId === message.id)

  if (!message.agentTrace && !message.content && !message.hitl) return null
  return (
    <div className="flex gap-3">
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-violet-600 text-white">
        <Bot size={14} />
      </div>
      <div className="flex flex-col gap-2 max-w-[85%] min-w-0">
        {message.agentTrace && <AgentTrace steps={message.agentTrace} />}
        {message.content && (
          <div className="rounded-2xl rounded-tl-sm bg-muted/50 border border-border/40 px-4 py-3 text-sm leading-relaxed prose prose-sm dark:prose-invert max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
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
        {/* All HITL types render as overlay above ChatInput — show only a waiting indicator here */}
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
        <span className="text-[10px] text-muted-foreground">{format(new Date(message.timestamp), "HH:mm")}</span>
      </div>
    </div>
  )
}
