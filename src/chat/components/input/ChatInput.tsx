import { useRef, useState, useCallback, useEffect, KeyboardEvent } from "react"
import { Send, Paperclip, Mic, StopCircle, Plus, FolderOpen, ChevronRight, Check, FolderPlus, Download } from "lucide-react"
import { cn } from "../../lib/utils"
import { useChat } from "../../hooks/useChat"
import { useChatStore } from "../../lib/store"
import { InputModeBar } from "./InputModeBar"
import { SQLEditor } from "./SQLEditor"
import { DOMAINS } from "../../lib/domains.config"

/* ── Add-to-project menu ──────────────────────────────────── */
function AddToProjectMenu({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const projects = useChatStore(s => s.projects)
  const createProject = useChatStore(s => s.createProject)
  const addConversationToProject = useChatStore(s => s.addConversationToProject)
  const removeConversationFromProject = useChatStore(s => s.removeConversationFromProject)
  const inProjects = new Set(projects.filter(p => p.conversationIds.includes(conversationId)).map(p => p.id))

  const toggle = (projectId: string) => {
    inProjects.has(projectId) ? removeConversationFromProject(projectId, conversationId) : addConversationToProject(projectId, conversationId)
    onClose()
  }
  const handleNewProject = () => {
    const name = prompt("Tên dự án mới:")
    if (name?.trim()) { const id = createProject(name.trim()); addConversationToProject(id, conversationId); onClose() }
  }

  return (
    <div className="absolute bottom-full left-0 z-50 mb-2 w-64 rounded-2xl overflow-hidden shadow-2xl"
      style={{ backgroundColor: "#1e1e1e", border: "1px solid #3a3a3a" }}>
      <div className="px-4 pt-2.5 pb-1">
        <p className="text-[10px] font-semibold uppercase tracking-wider mb-1" style={{ color: "#52525b" }}>Add to project</p>
      </div>
      <div className="max-h-[220px] overflow-y-auto px-1 pb-1">
        {projects.length === 0 ? (
          <p className="px-4 py-3 text-xs" style={{ color: "#52525b" }}>Chưa có dự án nào</p>
        ) : projects.map(p => {
          const inProject = inProjects.has(p.id)
          return (
            <button key={p.id} onClick={() => toggle(p.id)}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left transition-colors"
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2a2a2a")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
              <div className={cn("flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg text-white text-xs font-bold", p.color)}>
                {p.name[0].toUpperCase()}
              </div>
              <span className="flex-1 text-sm truncate" style={{ color: inProject ? "#f4f4f5" : "#a1a1aa" }}>{p.name}</span>
              {inProject && <Check size={13} style={{ color: "#3b82f6", flexShrink: 0 }} />}
            </button>
          )
        })}
      </div>
      <div className="p-1" style={{ borderTop: "1px solid #2a2a2a" }}>
        <button onClick={handleNewProject}
          className="flex w-full items-center gap-3 rounded-xl px-3 py-2.5 transition-colors"
          onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2a2a2a")}
          onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg" style={{ backgroundColor: "#2a2a2a", border: "1px solid #3a3a3a" }}>
            <Plus size={12} style={{ color: "#a1a1aa" }} />
          </div>
          <span className="text-sm" style={{ color: "#a1a1aa" }}>Tạo dự án mới</span>
        </button>
      </div>
    </div>
  )
}

/* ── @mention domain picker ──────────────────────────────── */
function MentionPicker({ onSelect, onClose }: { onSelect: (id: string) => void; onClose: () => void }) {
  return (
    <div className="w-52 rounded-xl overflow-hidden shadow-2xl border border-border bg-popover">
      <p className="px-3 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Chọn domain</p>
      {DOMAINS.map((d) => {
        const Icon = d.icon
        return (
          <button key={d.id} onClick={() => { onSelect(d.id); onClose() }}
            className="flex w-full items-center gap-2.5 px-3 py-2 text-sm hover:bg-muted transition-colors">
            <Icon size={13} className={d.cardColor} />
            <span>{d.labelShort}</span>
          </button>
        )
      })}
    </div>
  )
}

/* ── Slash command picker ─────────────────────────────────── */
const SLASH_COMMANDS = [
  { cmd: "/clear",     label: "Xoá lịch sử chat",     desc: "Xoá toàn bộ tin nhắn trong cuộc hội thoại" },
  { cmd: "/export",    label: "Xuất Markdown",          desc: "Tải cuộc hội thoại dưới dạng .md" },
  { cmd: "/summarize", label: "Tóm tắt",                desc: "Tóm tắt toàn bộ cuộc hội thoại" },
]

function SlashPicker({ filter, onSelect, onClose }: { filter: string; onSelect: (cmd: string) => void; onClose: () => void }) {
  const filtered = SLASH_COMMANDS.filter(c => c.cmd.includes(filter.toLowerCase()))
  if (filtered.length === 0) return null
  return (
    <div className="w-64 rounded-xl overflow-hidden shadow-2xl border border-border bg-popover">
      {filtered.map((c) => (
        <button key={c.cmd} onClick={() => { onSelect(c.cmd); onClose() }}
          className="flex w-full flex-col px-3 py-2.5 text-left hover:bg-muted transition-colors">
          <span className="text-sm font-medium text-primary">{c.cmd}</span>
          <span className="text-xs text-muted-foreground">{c.desc}</span>
        </button>
      ))}
    </div>
  )
}

/* ── Drag-drop overlay ──────────────────────────────────────  */
function DragOverlay() {
  return (
    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center rounded-2xl border-2 border-dashed border-primary bg-primary/5 pointer-events-none">
      <Paperclip size={24} className="text-primary mb-2" />
      <p className="text-sm font-medium text-primary">Thả file vào đây</p>
    </div>
  )
}

/* ── Export conversation ───────────────────────────────────── */
function exportToMarkdown(conversationId: string) {
  const { conversations } = useChatStore.getState()
  const conv = conversations.find(c => c.id === conversationId)
  if (!conv) return
  const lines = [
    `# ${conv.title}`,
    `Domain: ${conv.domain}  `,
    `Xuất lúc: ${new Date().toLocaleString("vi-VN")}`,
    "",
    ...conv.messages.map(m =>
      `**${m.role === "user" ? "Bạn" : "Agent"}** _(${new Date(m.timestamp).toLocaleTimeString("vi-VN")})_\n\n${m.content}\n`
    ),
  ]
  const blob = new Blob([lines.join("\n")], { type: "text/markdown" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url; a.download = `${conv.title.replace(/\s+/g, "-")}.md`; a.click()
  URL.revokeObjectURL(url)
}

/* ── Placeholders ─────────────────────────────────────────── */
const PLACEHOLDER: Record<string, string> = {
  text:   "Nhắn tin... (Enter gửi · @domain · /lệnh)",
  sql:    "Nhập SQL hoặc mô tả bằng ngôn ngữ tự nhiên...",
  python: "Nhập code Python hoặc mô tả logic cần thực thi...",
  file:   "Kéo thả file vào đây hoặc click để chọn (PDF, DOCX, XLSX)...",
  voice:  "Click micro để bắt đầu ghi âm...",
}

/* ── Main ChatInput ───────────────────────────────────────── */
export function ChatInput({ conversationId }: { conversationId: string }) {
  const [value, setValue] = useState("")
  const [isSending, setIsSending] = useState(false)
  const [isListening, setIsListening] = useState(false)
  const [transcript, setTranscript] = useState("")
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [showPlusMenu, setShowPlusMenu] = useState(false)
  const [showMention, setShowMention] = useState(false)
  const [showSlash, setShowSlash] = useState(false)
  const [slashFilter, setSlashFilter] = useState("")
  const [isDragOver, setIsDragOver] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const recognitionRef = useRef<{ stop: () => void } | null>(null)
  const { sendMessage } = useChat()
  const { ui, clearConversationMessages, setConversationSummary, setSelectedDomain } = useChatStore()

  // Listen for follow-up question events from AIMessage
  useEffect(() => {
    const handler = (e: Event) => {
      const q = (e as CustomEvent<string>).detail
      setValue(q)
      textareaRef.current?.focus()
    }
    window.addEventListener("chat:followup", handler)
    return () => window.removeEventListener("chat:followup", handler)
  }, [])

  useEffect(() => { return () => { recognitionRef.current?.stop() } }, [])

  const toggleListening = useCallback(() => {
    const SpeechRecognition = (window as any).SpeechRecognition ?? (window as any).webkitSpeechRecognition
    if (!SpeechRecognition) { setVoiceError("Trình duyệt không hỗ trợ ghi âm. Dùng Chrome hoặc Edge."); return }
    if (isListening) { recognitionRef.current?.stop(); return }
    setVoiceError(null); setTranscript("")
    const recognition = new SpeechRecognition()
    recognition.lang = "vi-VN"; recognition.interimResults = true; recognition.continuous = false
    recognitionRef.current = recognition
    recognition.onstart = () => setIsListening(true)
    recognition.onend = () => setIsListening(false)
    recognition.onerror = (e: any) => {
      setIsListening(false)
      if (e.error === "not-allowed") setVoiceError("Cần cấp quyền microphone.")
      else if (e.error === "no-speech") setVoiceError("Không nghe thấy giọng nói. Thử lại.")
      else setVoiceError(`Lỗi: ${e.error}`)
    }
    recognition.onresult = (e: any) => {
      const text = Array.from(e.results).map((r: any) => r[0].transcript).join("")
      setTranscript(text)
      if (e.results[e.results.length - 1].isFinal) setValue(text)
    }
    recognition.start()
  }, [isListening])

  const adjustHeight = useCallback(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = "auto"
    el.style.height = Math.min(el.scrollHeight, 200) + "px"
  }, [])

  const handleChange = (v: string) => {
    setValue(v)
    adjustHeight()
    // @mention detection
    const lastAt = v.lastIndexOf("@")
    setShowMention(lastAt !== -1 && lastAt === v.length - 1)
    // Slash detection
    if (v.startsWith("/")) {
      setShowSlash(true); setSlashFilter(v)
    } else {
      setShowSlash(false); setSlashFilter("")
    }
  }

  const handleSlashCommand = async (cmd: string) => {
    setValue("")
    if (cmd === "/clear") {
      clearConversationMessages(conversationId)
    } else if (cmd === "/export") {
      exportToMarkdown(conversationId)
    } else if (cmd === "/summarize") {
      const { conversations } = useChatStore.getState()
      const conv = conversations.find(c => c.id === conversationId)
      if (conv) {
        const preview = conv.messages.filter(m => m.content).slice(-10)
          .map(m => `${m.role === "user" ? "User" : "Agent"}: ${m.content.slice(0, 80)}`).join("\n")
        setConversationSummary(conversationId, `Tóm tắt (${conv.messages.length} tin nhắn): ${preview.slice(0, 200)}…`)
      }
    }
  }

  const handleMentionSelect = (domainId: string) => {
    setSelectedDomain(domainId)
    setValue(v => v.replace(/@$/, ""))
    textareaRef.current?.focus()
  }

  const handleSubmit = useCallback(async () => {
    const trimmed = value.trim()
    if (!trimmed || isSending) return
    if (trimmed.startsWith("/")) { handleSlashCommand(trimmed); return }
    setIsSending(true); setValue(""); setTranscript("")
    if (textareaRef.current) textareaRef.current.style.height = "auto"
    await sendMessage(trimmed, conversationId)
    setIsSending(false)
  }, [value, isSending, sendMessage, conversationId])

  const handleKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Escape") { setShowMention(false); setShowSlash(false); return }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSubmit() }
  }

  // Drag-drop handlers
  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setIsDragOver(true) }
  const handleDragLeave = () => setIsDragOver(false)
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault(); setIsDragOver(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length > 0) {
      const names = files.map(f => f.name).join(", ")
      setValue(v => v ? `${v} [${names}]` : `[${names}]`)
      textareaRef.current?.focus()
    }
  }

  const isSQL = ui.currentInputMode === "sql"
  const isPython = ui.currentInputMode === "python"
  const isFile = ui.currentInputMode === "file"
  const isVoice = ui.currentInputMode === "voice"
  const isCode = isSQL || isPython

  return (
    <div className="flex-shrink-0 border-t border-border/50 bg-background relative">
      {/* Pickers anchored above the entire ChatInput section (above InputModeBar) */}
      {showMention && (
        <div className="absolute bottom-full left-4 mb-1 z-50">
          <MentionPicker onSelect={handleMentionSelect} onClose={() => setShowMention(false)} />
        </div>
      )}
      {showSlash && (
        <div className="absolute bottom-full left-4 mb-1 z-50">
          <SlashPicker filter={slashFilter} onSelect={handleSlashCommand} onClose={() => setShowSlash(false)} />
        </div>
      )}

      <InputModeBar />
      <div className="px-4 pb-4">
        <div className="relative">

        <div
          className="rounded-2xl border border-border/60 bg-background transition-shadow focus-within:border-primary/40 focus-within:shadow-sm focus-within:shadow-primary/10"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDragOver && <DragOverlay />}

          {isCode && <SQLEditor value={value} onChange={setValue} language={isPython ? "python" : "sql"} />}
          {!isCode && !isFile && !isVoice && (
            <textarea ref={textareaRef} value={value}
              onChange={(e) => handleChange(e.target.value)}
              onKeyDown={handleKey}
              placeholder={PLACEHOLDER[ui.currentInputMode]}
              rows={1} disabled={isSending}
              className="w-full resize-none border-0 bg-transparent px-4 pt-3.5 pb-2 text-sm text-foreground placeholder:text-muted-foreground/60 focus:outline-none disabled:opacity-50"
              style={{ minHeight: "52px" }} />
          )}
          {isFile && (
            <div className="flex flex-col items-center justify-center gap-2 py-7 text-muted-foreground cursor-pointer hover:bg-muted/30 transition-colors">
              <Paperclip size={20} />
              <span className="text-sm">Kéo file vào đây hoặc click để chọn</span>
              <span className="text-xs">PDF, DOCX, XLSX, CSV — tối đa 50MB</span>
            </div>
          )}
          {isVoice && (
            <div className="flex flex-col items-center justify-center gap-3 py-7">
              <button onClick={toggleListening}
                className={cn("flex h-14 w-14 items-center justify-center rounded-full transition-all",
                  isListening ? "bg-destructive text-white animate-pulse shadow-lg shadow-destructive/30" : "bg-destructive/10 text-destructive hover:bg-destructive/20")}>
                {isListening ? <StopCircle size={22} /> : <Mic size={22} />}
              </button>
              {isListening && <span className="text-xs text-destructive font-medium animate-pulse">Đang ghi âm...</span>}
              {!isListening && !voiceError && <span className="text-xs text-muted-foreground">Click để bắt đầu ghi âm</span>}
              {voiceError && <span className="text-xs text-destructive">{voiceError}</span>}
              {transcript && <p className="text-sm text-foreground text-center max-w-xs px-2">{transcript}</p>}
            </div>
          )}

          <div className="flex items-center justify-between px-3 py-2 border-t border-border/30 bg-muted/20">
            <div className="flex items-center gap-1 relative">
              {/* Plus menu */}
              {showPlusMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setShowPlusMenu(false)} />
                  <AddToProjectMenu conversationId={conversationId} onClose={() => setShowPlusMenu(false)} />
                </>
              )}
              <button
                onClick={() => setShowPlusMenu(v => !v)}
                className={cn("flex h-7 w-7 items-center justify-center rounded-lg transition-colors",
                  showPlusMenu ? "bg-primary/20 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted")}>
                <Plus size={15} />
              </button>
              <button onClick={() => exportToMarkdown(conversationId)} title="Xuất Markdown"
                className="flex h-7 w-7 items-center justify-center rounded-lg transition-colors text-muted-foreground hover:text-foreground hover:bg-muted">
                <Download size={14} />
              </button>
            </div>
            <div className="flex items-center gap-2">
              {value.trim() && <span className="text-[10px] text-muted-foreground">{value.length} ký tự</span>}
              <button onClick={handleSubmit} disabled={!value.trim() || isSending}
                className={cn("flex h-8 w-8 items-center justify-center rounded-full transition-all",
                  value.trim() && !isSending
                    ? "bg-primary text-primary-foreground hover:bg-primary/90"
                    : "bg-muted text-muted-foreground cursor-not-allowed opacity-50")}>
                {isSending ? <StopCircle size={14} /> : <Send size={14} />}
              </button>
            </div>
          </div>
        </div>
        </div>{/* end inner relative wrapper */}
        <p className="mt-2 text-center text-[10px] text-muted-foreground/50">
          Data Agent có thể mắc lỗi. Luôn kiểm tra kết quả quan trọng.
        </p>
      </div>
    </div>
  )
}
