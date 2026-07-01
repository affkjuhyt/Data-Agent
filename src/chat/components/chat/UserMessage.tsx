import { useState } from "react"
import { format } from "date-fns"
import { Pin, Bookmark, Copy, Check } from "lucide-react"
import { Message } from "../../lib/types"
import { useChatStore } from "../../lib/store"
import { useAuth } from "../../../lib/AuthProvider"
import { cn } from "../../lib/utils"

export function UserMessage({ message, conversationId }: { message: Message; conversationId: string }) {
  const { pinMessage, bookmarkMessage } = useChatStore()
  const { user } = useAuth()
  const [copied, setCopied] = useState(false)
  const [showActions, setShowActions] = useState(false)

  const initials = user?.name
    ? user.name.split(" ").filter(Boolean).map((w) => w[0].toUpperCase()).slice(0, 2).join("")
    : "U"

  const handleCopy = () => {
    navigator.clipboard.writeText(message.content)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div
      className={cn("group flex justify-end gap-3", message.pinned && "ring-1 ring-amber-500/30 rounded-2xl px-2 py-1 -mx-2")}
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      <div className="flex flex-col items-end gap-1 max-w-[75%]">
        <div className="relative">
          <div className="rounded-2xl rounded-tr-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground leading-relaxed">
            {message.content}
          </div>
          {/* Hover action bar */}
          {showActions && (
            <div className="absolute -top-3 left-2 flex items-center gap-0.5 rounded-full border border-border bg-background shadow-sm px-1 py-0.5">
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
        <div className="flex items-center gap-1.5">
          <span className="text-[10px] text-muted-foreground">{format(new Date(message.timestamp), "HH:mm")}</span>
          {message.pinned && <span className="text-[10px] text-amber-500 flex items-center gap-0.5"><Pin size={9} /> Đã ghim</span>}
          {message.bookmarked && <span className="text-[10px] text-blue-500 flex items-center gap-0.5"><Bookmark size={9} /> Đã đánh dấu</span>}
        </div>
      </div>
      <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white flex-shrink-0">
        {initials}
      </div>
    </div>
  )
}
