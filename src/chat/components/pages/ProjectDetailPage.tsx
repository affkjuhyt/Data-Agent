import { useState } from "react"
import {
  ArrowLeft, Plus, MoreHorizontal, Pencil, Trash2,
  Lock, ChevronRight, MessageSquare, X,
  Upload, File, AlertCircle, Search,
} from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { vi } from "date-fns/locale"
import { useChatStore } from "../../lib/store"
import { useChat } from "../../hooks/useChat"
import { AgentDomain } from "../../lib/types"
import { cn } from "../../lib/utils"

const DOMAIN_COLOR: Record<AgentDomain, string> = {
  stockllm: "bg-amber-600", enterprise: "bg-emerald-700",
  gov: "bg-blue-700", document: "bg-violet-700", forecast: "bg-orange-700",
}
const DOMAIN_LABEL: Record<AgentDomain, string> = {
  stockllm: "StockLLM", enterprise: "Enterprise",
  gov: "Gov", document: "Document", forecast: "Forecast",
}

function AddChatModal({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const conversations = useChatStore(s => s.conversations)
  const project = useChatStore(s => s.projects.find(p => p.id === projectId))
  const addConversationToProject = useChatStore(s => s.addConversationToProject)
  const [search, setSearch] = useState("")

  const existing = new Set(project?.conversationIds ?? [])
  const available = conversations
    .filter(c => !existing.has(c.id) && c.title.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[440px] rounded-2xl overflow-hidden shadow-2xl" style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46" }} onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between px-5 pt-5 pb-4" style={{ borderBottom: "1px solid #27272a" }}>
          <div>
            <h2 className="text-sm font-semibold" style={{ color: "#f4f4f5" }}>Thêm hội thoại vào dự án</h2>
            <p className="text-xs mt-0.5" style={{ color: "#71717a" }}>Chọn hội thoại để thêm vào "{project?.name}"</p>
          </div>
          <button onClick={onClose} className="flex h-7 w-7 items-center justify-center rounded-lg transition-colors"
            style={{ color: "#71717a" }}
            onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
            onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
            <X size={15} />
          </button>
        </div>
        <div className="px-4 py-3">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "#52525b" }} />
            <input autoFocus value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Tìm kiếm hội thoại..."
              className="w-full rounded-xl pl-9 pr-3 py-2.5 text-sm focus:outline-none"
              style={{ backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#e4e4e7" }} />
          </div>
        </div>
        <div className="max-h-[320px] overflow-y-auto px-2 pb-3">
          {available.length === 0 ? (
            <p className="py-8 text-center text-sm" style={{ color: "#52525b" }}>
              {search ? "Không tìm thấy hội thoại" : "Tất cả hội thoại đã được thêm"}
            </p>
          ) : available.map(c => (
            <button key={c.id}
              onClick={() => { addConversationToProject(projectId, c.id); onClose() }}
              className="flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors"
              onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#27272a")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
              <div className={cn("flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-white text-xs font-bold", DOMAIN_COLOR[c.domain])}>
                {DOMAIN_LABEL[c.domain][0]}
              </div>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium truncate" style={{ color: "#d4d4d8" }}>{c.title}</p>
                <p className="text-xs mt-0.5" style={{ color: "#52525b" }}>
                  {formatDistanceToNow(new Date(c.updatedAt), { addSuffix: true, locale: vi })}
                </p>
              </div>
              <Plus size={14} style={{ color: "#52525b", flexShrink: 0 }} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function MemorySection({ memory, onSave }: { memory?: string; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(memory ?? "")

  return (
    <div style={{ borderBottom: "1px solid #27272a" }} className="pb-5 mb-5">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-sm font-semibold" style={{ color: "#e4e4e7" }}>Memory</span>
          <div className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium"
            style={{ backgroundColor: "#1c1c1f", color: "#71717a", border: "1px solid #3f3f46" }}>
            <Lock size={9} /> Only you
          </div>
        </div>
        <button onClick={() => setEditing(v => !v)}
          className="flex h-6 w-6 items-center justify-center rounded-md transition-colors"
          style={{ color: "#71717a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
          onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
          <Pencil size={13} />
        </button>
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea value={val} onChange={e => setVal(e.target.value)} rows={4}
            placeholder="Add memory for this project..."
            className="w-full rounded-xl px-3 py-2.5 text-xs resize-none focus:outline-none"
            style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46", color: "#d4d4d8" }} />
          <div className="flex gap-2 justify-end">
            <button onClick={() => { setEditing(false); setVal(memory ?? "") }}
              className="px-3 py-1 text-xs rounded-lg transition-colors"
              style={{ color: "#71717a" }}>Huỷ</button>
            <button onClick={() => { onSave(val); setEditing(false) }}
              className="px-3 py-1 text-xs font-semibold rounded-lg text-white transition-colors"
              style={{ backgroundColor: "#3b82f6" }}>Lưu</button>
          </div>
        </div>
      ) : memory ? (
        <p className="text-xs leading-relaxed" style={{ color: "#a1a1aa" }}>{memory}</p>
      ) : (
        <p className="text-xs" style={{ color: "#3f3f46" }}>
          Chưa có memory. <button onClick={() => setEditing(true)} style={{ color: "#525252" }}
            className="hover:text-zinc-300 underline transition-colors">Thêm ngay</button>
        </p>
      )}
    </div>
  )
}

function InstructionsSection({ instructions, onSave }: { instructions?: string; onSave: (v: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(instructions ?? "")

  return (
    <div style={{ borderBottom: "1px solid #27272a" }} className="pb-5 mb-5">
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold" style={{ color: "#e4e4e7" }}>Instructions</span>
        <button onClick={() => setEditing(v => !v)}
          className="flex h-6 w-6 items-center justify-center rounded-md transition-colors"
          style={{ color: "#71717a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
          onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
          <Plus size={13} />
        </button>
      </div>
      {editing ? (
        <div className="flex flex-col gap-2">
          <textarea value={val} onChange={e => setVal(e.target.value)} rows={5}
            placeholder="Add instructions to tailor responses for this project..."
            className="w-full rounded-xl px-3 py-2.5 text-xs resize-none focus:outline-none"
            style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46", color: "#d4d4d8" }} />
          <div className="flex gap-2 justify-end">
            <button onClick={() => { setEditing(false); setVal(instructions ?? "") }}
              className="px-3 py-1 text-xs rounded-lg"
              style={{ color: "#71717a" }}>Huỷ</button>
            <button onClick={() => { onSave(val); setEditing(false) }}
              className="px-3 py-1 text-xs font-semibold rounded-lg text-white"
              style={{ backgroundColor: "#3b82f6" }}>Lưu</button>
          </div>
        </div>
      ) : instructions ? (
        <p className="text-xs leading-relaxed" style={{ color: "#a1a1aa" }}>{instructions}</p>
      ) : (
        <p className="text-xs" style={{ color: "#3f3f46" }}>
          Chưa có instructions. <button onClick={() => setEditing(true)} style={{ color: "#525252" }}
            className="hover:text-zinc-300 underline transition-colors">Thêm ngay</button>
        </p>
      )}
    </div>
  )
}

function FilesSection({ projectId }: { projectId: string }) {
  const project = useChatStore(s => s.projects.find(p => p.id === projectId))
  const removeProjectFile = useChatStore(s => s.removeProjectFile)
  const files = project?.files ?? []
  const usedKb = files.reduce((acc, f) => acc + (f.sizeKb ?? 0), 0)
  const capacityKb = 5000
  const pct = Math.min((usedKb / capacityKb) * 100, 100)

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold" style={{ color: "#e4e4e7" }}>Files</span>
        <button className="flex h-6 w-6 items-center justify-center rounded-md transition-colors"
          style={{ color: "#71717a" }}
          onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
          onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
          <Plus size={13} />
        </button>
      </div>

      {/* Storage bar */}
      <div className="mb-4">
        <div className="h-1.5 rounded-full overflow-hidden mb-1.5" style={{ backgroundColor: "#27272a" }}>
          <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, backgroundColor: "#3b82f6" }} />
        </div>
        <div className="flex justify-between text-[10px]" style={{ color: "#52525b" }}>
          <span>{pct.toFixed(0)}% of project capacity used</span>
          <span>Search mode</span>
        </div>
      </div>

      {files.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-6 rounded-xl text-center"
          style={{ border: "1px dashed #2a2a2d" }}>
          <Upload size={18} style={{ color: "#3f3f46" }} className="mb-2" />
          <p className="text-xs" style={{ color: "#52525b" }}>Upload files to this project</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          {files.map(f => (
            <div key={f.id} className="group relative flex flex-col gap-1 rounded-xl p-3"
              style={{ backgroundColor: "#1c1c1f", border: "1px solid #2a2a2d" }}>
              <button onClick={() => removeProjectFile(projectId, f.id)}
                className="absolute right-2 top-2 hidden group-hover:flex h-5 w-5 items-center justify-center rounded"
                style={{ color: "#71717a" }}
                onMouseEnter={e => (e.currentTarget.style.color = "#ef4444")}
                onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
                <X size={11} />
              </button>
              <div className="flex items-center gap-1.5 mb-1">
                <File size={13} style={{ color: "#71717a" }} />
                <span className="text-[10px] font-semibold uppercase px-1 py-0.5 rounded"
                  style={{ backgroundColor: "#27272a", color: "#a1a1aa" }}>
                  {f.type.toUpperCase()}
                </span>
              </div>
              <p className="text-xs font-medium truncate" style={{ color: "#d4d4d8" }}>{f.name}</p>
              {f.lines && <p className="text-[10px]" style={{ color: "#52525b" }}>{f.lines.toLocaleString()} lines</p>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export function ProjectDetailPage({
  projectId,
  onBack,
  onOpenConversation,
}: {
  projectId: string
  onBack: () => void
  onOpenConversation: (convId: string) => void
}) {
  const project = useChatStore(s => s.projects.find(p => p.id === projectId))
  const conversations = useChatStore(s => s.conversations)
  const deleteProject = useChatStore(s => s.deleteProject)
  const renameProject = useChatStore(s => s.renameProject)
  const updateProjectMemory = useChatStore(s => s.updateProjectMemory)
  const updateProjectInstructions = useChatStore(s => s.updateProjectInstructions)
  const addConversationToProject = useChatStore(s => s.addConversationToProject)
  const { startNewChat } = useChat()

  const [showMenu, setShowMenu] = useState(false)
  const [editingName, setEditingName] = useState(false)
  const [nameVal, setNameVal] = useState(project?.name ?? "")
  const [showAddChat, setShowAddChat] = useState(false)

  if (!project) return (
    <div className="flex flex-1 items-center justify-center" style={{ backgroundColor: "#111113" }}>
      <div className="text-center">
        <AlertCircle size={32} style={{ color: "#3f3f46" }} className="mx-auto mb-3" />
        <p className="text-sm" style={{ color: "#71717a" }}>Dự án không tồn tại</p>
        <button onClick={onBack} className="mt-3 text-sm" style={{ color: "#3b82f6" }}>← Quay lại</button>
      </div>
    </div>
  )

  const projectConvs = conversations
    .filter(c => project.conversationIds.includes(c.id))
    .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime())

  const handleNewChat = () => {
    const id = startNewChat("enterprise")
    addConversationToProject(projectId, id)
    onOpenConversation(id)
  }

  const handleDelete = () => {
    deleteProject(projectId)
    onBack()
  }

  return (
    <div className="flex flex-1 overflow-hidden" style={{ backgroundColor: "#111113" }}>
      {/* Left panel — conversations */}
      <div className="flex flex-col flex-1 min-w-0 overflow-hidden" style={{ borderRight: "1px solid #1c1c1f" }}>
        {/* Back nav */}
        <div className="flex-shrink-0 px-6 pt-5 pb-3">
          <button onClick={onBack}
            className="flex items-center gap-1.5 text-sm transition-colors mb-4"
            style={{ color: "#71717a" }}
            onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
            onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
            <ArrowLeft size={14} /> All projects
          </button>

          {/* Project header */}
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <div className={cn("flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-white font-bold", project.color)}>
                {project.name[0].toUpperCase()}
              </div>
              {editingName ? (
                <input autoFocus value={nameVal}
                  onChange={e => setNameVal(e.target.value)}
                  onBlur={() => { renameProject(projectId, nameVal); setEditingName(false) }}
                  onKeyDown={e => {
                    if (e.key === "Enter") { renameProject(projectId, nameVal); setEditingName(false) }
                    if (e.key === "Escape") { setNameVal(project.name); setEditingName(false) }
                  }}
                  className="text-2xl font-semibold bg-transparent focus:outline-none border-b"
                  style={{ color: "#f4f4f5", borderColor: "#3f3f46" }} />
              ) : (
                <h1 className="text-2xl font-semibold truncate" style={{ color: "#f4f4f5" }}>{project.name}</h1>
              )}
            </div>
            <div className="relative flex-shrink-0 ml-3">
              <button onClick={() => setShowMenu(v => !v)}
                className="flex h-8 w-8 items-center justify-center rounded-lg transition-colors"
                style={{ color: "#71717a" }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#27272a")}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
                <MoreHorizontal size={16} />
              </button>
              {showMenu && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
                  <div className="absolute right-0 top-full z-50 mt-1 w-48 rounded-xl overflow-hidden shadow-2xl"
                    style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46" }}>
                    <button onClick={() => { setEditingName(true); setShowMenu(false) }}
                      className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm transition-colors"
                      style={{ color: "#d4d4d8" }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#27272a")}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
                      <Pencil size={13} /> Đổi tên
                    </button>
                    <div style={{ borderTop: "1px solid #27272a" }} />
                    <button onClick={handleDelete}
                      className="flex w-full items-center gap-2.5 px-4 py-2.5 text-sm transition-colors"
                      style={{ color: "#ef4444" }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#27272a")}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
                      <Trash2 size={13} /> Xoá dự án
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {/* New chat button */}
        <div className="flex-shrink-0 px-6 py-3">
          <button onClick={handleNewChat}
            className="flex items-center gap-2 rounded-xl px-4 py-2.5 text-sm font-semibold text-white transition-colors"
            style={{ backgroundColor: "#3b82f6" }}
            onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2563eb")}
            onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3b82f6")}>
            <Plus size={15} /> Thêm hội thoại mới
          </button>
        </div>

        {/* Conversations list */}
        <div className="flex-1 overflow-y-auto px-6 pb-6">
          <div className="flex items-center justify-between mb-2">
            <p className="text-xs font-semibold uppercase tracking-wider" style={{ color: "#52525b" }}>
              Hội thoại · {projectConvs.length}
            </p>
            <button onClick={() => setShowAddChat(true)}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs transition-colors"
              style={{ color: "#71717a" }}
              onMouseEnter={e => { e.currentTarget.style.backgroundColor = "#27272a"; e.currentTarget.style.color = "#e4e4e7" }}
              onMouseLeave={e => { e.currentTarget.style.backgroundColor = "transparent"; e.currentTarget.style.color = "#71717a" }}>
              <Plus size={12} /> Thêm
            </button>
          </div>
          {projectConvs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-16 text-center">
              <MessageSquare size={28} style={{ color: "#27272a" }} className="mb-3" />
              <p className="text-sm" style={{ color: "#52525b" }}>Chưa có hội thoại nào</p>
              <button onClick={handleNewChat}
                className="mt-3 text-xs font-medium transition-colors"
                style={{ color: "#3b82f6" }}>
                Bắt đầu hội thoại mới →
              </button>
            </div>
          ) : (
            <div className="space-y-1">
              {projectConvs.map(conv => (
                <button key={conv.id}
                  onClick={() => onOpenConversation(conv.id)}
                  className="group flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left transition-colors"
                  onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#1c1c1f")}
                  onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
                  <div className={cn("flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-white text-xs font-bold", DOMAIN_COLOR[conv.domain])}>
                    {DOMAIN_LABEL[conv.domain][0]}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate" style={{ color: "#d4d4d8" }}>{conv.title}</p>
                    <p className="text-xs mt-0.5" style={{ color: "#52525b" }}>
                      Last message {formatDistanceToNow(new Date(conv.updatedAt), { addSuffix: true, locale: vi })}
                    </p>
                  </div>
                  <ChevronRight size={14} style={{ color: "#3f3f46" }} className="flex-shrink-0 opacity-0 group-hover:opacity-100 transition-opacity" />
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Right panel — Memory, Instructions, Files */}
      <div className="w-[320px] flex-shrink-0 overflow-y-auto px-5 py-6">
        <MemorySection
          memory={project.memory}
          onSave={v => updateProjectMemory(projectId, v)}
        />
        <InstructionsSection
          instructions={project.instructions}
          onSave={v => updateProjectInstructions(projectId, v)}
        />
        <FilesSection projectId={projectId} />
      </div>

      {showAddChat && (
        <AddChatModal projectId={projectId} onClose={() => setShowAddChat(false)} />
      )}
    </div>
  )
}
