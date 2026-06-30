import { useEffect, useRef, useState } from "react"
import { Pencil, Trash2, Download, Eraser, Check, X } from "lucide-react"
import { useChatStore } from "../../lib/store"
import { Conversation } from "../../lib/types"
import { cn } from "../../lib/utils"

function MenuItem({ icon, label, onClick, destructive = false }: {
  icon: React.ReactNode; label: string; onClick: () => void; destructive?: boolean
}) {
  return (
    <button onClick={onClick}
      className={cn("flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs transition-colors",
        destructive ? "text-red-400 hover:bg-red-500/10" : "text-foreground hover:bg-accent")}>
      <span className="flex-shrink-0">{icon}</span>
      {label}
    </button>
  )
}

export function MoreOptionsMenu({ conversation, onClose }: { conversation: Conversation; onClose: () => void }) {
  const { renameConversation, deleteConversation, clearConversationMessages, setActiveConversation } = useChatStore()
  const menuRef = useRef<HTMLDivElement>(null)
  const [renaming, setRenaming] = useState(false)
  const [renameValue, setRenameValue] = useState(conversation.title)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener("mousedown", handler)
    return () => document.removeEventListener("mousedown", handler)
  }, [onClose])

  const handleRenameSubmit = () => {
    const trimmed = renameValue.trim()
    if (trimmed && trimmed !== conversation.title) renameConversation(conversation.id, trimmed)
    setRenaming(false)
    onClose()
  }

  const handleExport = () => {
    const lines = conversation.messages.map((m) => `[${m.role.toUpperCase()}] ${m.content}`).join("\n\n")
    const blob = new Blob([lines], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${conversation.title.replace(/\s+/g, "_")}.txt`
    a.click()
    URL.revokeObjectURL(url)
    onClose()
  }

  const handleClearHistory = () => { clearConversationMessages(conversation.id); onClose() }

  const handleDelete = () => {
    deleteConversation(conversation.id)
    setActiveConversation(null)
    onClose()
  }

  return (
    <div ref={menuRef}
      className="absolute right-0 top-10 z-50 w-52 rounded-xl border border-border bg-background/95 backdrop-blur-sm p-1.5 shadow-xl">
      {renaming ? (
        <div className="px-2 py-1.5 flex items-center gap-1.5">
          <input autoFocus value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") handleRenameSubmit()
              if (e.key === "Escape") { setRenaming(false); onClose() }
            }}
            className="flex-1 rounded-md bg-muted px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-primary/50"
          />
          <button onClick={handleRenameSubmit} className="flex h-6 w-6 items-center justify-center rounded text-emerald-400 hover:bg-emerald-500/10">
            <Check size={12} />
          </button>
          <button onClick={() => { setRenaming(false); onClose() }} className="flex h-6 w-6 items-center justify-center rounded text-muted-foreground hover:bg-accent">
            <X size={12} />
          </button>
        </div>
      ) : (
        <>
          <MenuItem icon={<Pencil size={13} />} label="Đổi tên hội thoại" onClick={() => setRenaming(true)} />
          <MenuItem icon={<Download size={13} />} label="Xuất nội dung (.txt)" onClick={handleExport} />
          <div className="my-1 h-px bg-border/50" />
          <MenuItem icon={<Eraser size={13} />} label="Xoá lịch sử chat" onClick={handleClearHistory} />
          <MenuItem icon={<Trash2 size={13} />} label="Xoá hội thoại" onClick={handleDelete} destructive />
        </>
      )}
    </div>
  )
}
