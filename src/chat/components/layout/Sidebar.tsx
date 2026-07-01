import { useState, useRef } from "react"
import {
  Plus, Search, Settings, MoreHorizontal, Trash2,
  FolderOpen, FolderInput, X,
} from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { vi } from "date-fns/locale"
import { cn } from "../../lib/utils"
import { useChatStore } from "../../lib/store"
import { useChat } from "../../hooks/useChat"
import { Conversation, Project } from "../../lib/types"
import { useAuth } from "../../../lib/AuthProvider"
import { getDomain } from "../../lib/domains.config"

function groupByDate(conversations: Conversation[]) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today.getTime() - 86400000)
  const weekAgo = new Date(today.getTime() - 7 * 86400000)
  const groups: Record<string, Conversation[]> = { "Hôm nay": [], "Hôm qua": [], "Tuần này": [], "Cũ hơn": [] }
  conversations.forEach((c) => {
    const d = new Date(c.updatedAt)
    if (d >= today) groups["Hôm nay"].push(c)
    else if (d >= yesterday) groups["Hôm qua"].push(c)
    else if (d >= weekAgo) groups["Tuần này"].push(c)
    else groups["Cũ hơn"].push(c)
  })
  return Object.entries(groups).filter(([, v]) => v.length > 0)
}

function MoveToProjectModal({ projects, convId, onClose, onMove }: {
  projects: Project[]; convId: string; onClose: () => void; onMove: (projectId: string) => void
}) {
  const [search, setSearch] = useState("")
  const alreadyIn = new Set(projects.filter((p) => p.conversationIds.includes(convId)).map((p) => p.id))
  const filtered = projects.filter((p) => p.name.toLowerCase().includes(search.toLowerCase()))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[420px] rounded-2xl border border-zinc-700/60 bg-zinc-900 shadow-2xl overflow-hidden" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between px-6 pt-6 pb-4">
          <div>
            <h2 className="text-lg font-semibold text-zinc-100">Di chuyển chat</h2>
            <p className="text-sm text-zinc-400 mt-0.5">Chọn dự án để di chuyển chat vào.</p>
          </div>
          <button onClick={onClose} className="flex h-7 w-7 items-center justify-center rounded-lg text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800 transition-colors mt-0.5">
            <X size={15} />
          </button>
        </div>
        <div className="px-3 pb-2">
          <div className="flex items-center gap-2.5 rounded-xl border border-zinc-700/60 bg-zinc-800/60 px-3 py-2.5">
            <Search size={14} className="text-zinc-500 flex-shrink-0" />
            <input autoFocus value={search} onChange={(e) => setSearch(e.target.value)}
              placeholder="Tìm kiếm hoặc tạo dự án"
              className="flex-1 bg-transparent text-sm text-zinc-200 placeholder-zinc-500 outline-none" />
          </div>
        </div>
        <div className="px-3 pb-4 max-h-[300px] overflow-y-auto space-y-0.5 mt-1">
          {filtered.length === 0 ? (
            <p className="px-3 py-6 text-center text-sm text-zinc-600">Không tìm thấy dự án nào</p>
          ) : filtered.map((p) => {
            const inProject = alreadyIn.has(p.id)
            return (
              <button key={p.id} disabled={inProject} onClick={() => { onMove(p.id); onClose() }}
                className={cn("flex w-full items-center gap-3 rounded-xl px-4 py-3 text-left transition-colors",
                  inProject ? "opacity-40 cursor-not-allowed" : "hover:bg-zinc-800 cursor-pointer")}>
                <div className={cn("flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-white text-xs font-bold", p.color)}>
                  {p.name[0].toUpperCase()}
                </div>
                <span className="flex-1 text-sm font-medium text-zinc-200 truncate">{p.name}</span>
                {inProject && <span className="text-xs text-zinc-500 flex-shrink-0">Đã có</span>}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function ConvItem({ conv, isActive, projects, onSelect, onDelete, onAddToProject, onDragStart, onDragOver, onDrop }: {
  conv: Conversation; isActive: boolean; projects: Project[];
  onSelect: () => void; onDelete: () => void; onAddToProject: (projectId: string) => void
  onDragStart: () => void; onDragOver: (e: React.DragEvent) => void; onDrop: () => void
}) {
  const renameConversation = useChatStore((s) => s.renameConversation)
  const domainCfg = getDomain(conv.domain)
  const Icon = domainCfg.icon
  const [showMenu, setShowMenu] = useState(false)
  const [showMoveModal, setShowMoveModal] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editTitle, setEditTitle] = useState(conv.title)
  const inputRef = useRef<HTMLInputElement>(null)

  const commitRename = () => {
    const t = editTitle.trim()
    if (t && t !== conv.title) renameConversation(conv.id, t)
    setEditing(false)
  }

  return (
    <>
      <div
        draggable
        onDragStart={onDragStart}
        onDragOver={onDragOver}
        onDrop={onDrop}
        style={isActive ? { backgroundColor: "#27272a" } : undefined}
        className={cn("group relative flex items-center gap-2.5 rounded-xl px-3 py-2.5 cursor-pointer transition-colors mx-2",
          !isActive && "hover:bg-zinc-800/60")}
        onClick={onSelect}>
        <div className={cn("flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-md text-white", domainCfg.avatarBg)}>
          <Icon size={11} />
        </div>
        <div className="flex-1 min-w-0">
          {editing ? (
            <input
              ref={inputRef}
              autoFocus
              value={editTitle}
              onChange={(e) => setEditTitle(e.target.value)}
              onBlur={commitRename}
              onKeyDown={(e) => { if (e.key === "Enter") commitRename(); if (e.key === "Escape") setEditing(false) }}
              onClick={(e) => e.stopPropagation()}
              className="w-full bg-transparent text-sm font-medium outline-none border-b border-zinc-500 text-white pb-0.5"
            />
          ) : (
            <p
              className="text-sm font-medium truncate leading-tight"
              style={{ color: isActive ? "#ffffff" : "#d4d4d8" }}
              onDoubleClick={(e) => { e.stopPropagation(); setEditing(true); setEditTitle(conv.title) }}
            >{conv.title}</p>
          )}
          <p className="text-xs mt-0.5" style={{ color: "#71717a" }}>
            {domainCfg.labelShort} · {formatDistanceToNow(new Date(conv.updatedAt), { addSuffix: true, locale: vi })}
          </p>
        </div>
        <button
          className="hidden group-hover:flex h-6 w-6 flex-shrink-0 items-center justify-center rounded-md hover:bg-zinc-700"
          style={{ color: "#a1a1aa" }}
          onClick={(e) => { e.stopPropagation(); setShowMenu((v) => !v) }}>
          <MoreHorizontal size={14} />
        </button>
        {showMenu && (
          <>
            <div className="fixed inset-0 z-40" onClick={() => setShowMenu(false)} />
            <div className="absolute right-2 top-10 z-50 min-w-[180px] rounded-xl border border-zinc-700/80 bg-zinc-900 p-1 shadow-2xl"
              onClick={(e) => e.stopPropagation()}>
              {projects.length > 0 && (
                <button className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-zinc-300 hover:bg-zinc-800 transition-colors"
                  onClick={() => { setShowMoveModal(true); setShowMenu(false) }}>
                  <FolderInput size={13} className="text-zinc-400" />
                  <span>Di chuyển vào dự án</span>
                </button>
              )}
              <div className="my-0.5 h-px bg-zinc-800" />
              <button className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-xs text-red-400 hover:bg-zinc-800 transition-colors"
                onClick={() => { onDelete(); setShowMenu(false) }}>
                <Trash2 size={13} /> Xoá hội thoại
              </button>
            </div>
          </>
        )}
      </div>
      {showMoveModal && (
        <MoveToProjectModal projects={projects} convId={conv.id}
          onClose={() => setShowMoveModal(false)} onMove={onAddToProject} />
      )}
    </>
  )
}


export function Sidebar({ onExit, onNavigateProjects, onSelectConversation }: {
  onExit?: () => void
  onNavigateProjects?: () => void
  onSelectConversation?: (id: string) => void
}) {
  const conversations = useChatStore((s) => s.conversations)
  const projects = useChatStore((s) => s.projects)
  const activeConversationId = useChatStore((s) => s.activeConversationId)
  const deleteConversation = useChatStore((s) => s.deleteConversation)
  const addConversationToProject = useChatStore((s) => s.addConversationToProject)
  const setActiveConversation = useChatStore((s) => s.setActiveConversation)
  const reorderConversations = useChatStore((s) => s.reorderConversations)
  const { startNewChat } = useChat()
  const { user, logout } = useAuth()
  const [search, setSearch] = useState("")
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set())
  const dragIdRef = useRef<string | null>(null)

  const initials = user?.name
    ? user.name.split(" ").filter(Boolean).map((w) => w[0].toUpperCase()).slice(0, 2).join("")
    : "?"

  const currentId = activeConversationId ?? ""
  const filtered = conversations.filter((c) => c.title.toLowerCase().includes(search.toLowerCase()))
  const groups = groupByDate(filtered)

  const handleSelect = (id: string) => {
    setActiveConversation(id)
    onSelectConversation?.(id)
  }

  const toggleGroup = (label: string) =>
    setCollapsedGroups((prev) => {
      const next = new Set(prev)
      next.has(label) ? next.delete(label) : next.add(label)
      return next
    })

  const handleDrop = (targetId: string) => {
    const srcId = dragIdRef.current
    if (!srcId || srcId === targetId) return
    const ids = conversations.map((c) => c.id)
    const from = ids.indexOf(srcId)
    const to = ids.indexOf(targetId)
    const reordered = [...ids]
    reordered.splice(from, 1)
    reordered.splice(to, 0, srcId)
    reorderConversations(reordered)
    dragIdRef.current = null
  }

  return (
    <div className="flex h-full flex-col" style={{ backgroundColor: "#0f0f12", color: "#e4e4e7" }}>
      <div className="flex-shrink-0 px-3 pt-4 pb-2">
        <div className="flex items-center gap-2 px-1 mb-3">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600 text-white text-xs font-bold">D</div>
          <span className="text-sm font-semibold" style={{ color: "#f4f4f5" }}>Data Agent</span>
          {onExit && (
            <button onClick={onExit} title="Thoát chat"
              className="ml-auto text-zinc-500 hover:text-zinc-300 transition-colors text-xs">← Thoát</button>
          )}
        </div>
        <button onClick={() => startNewChat()}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm transition-colors mb-1"
          style={{ backgroundColor: "#3f3f46", color: "#ffffff" }}
          onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#52525b")}
          onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3f3f46")}>
          <Plus size={15} /> Cuộc hội thoại mới
        </button>
        <button onClick={onNavigateProjects}
          className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-sm transition-colors mb-2"
          style={{ color: "#a1a1aa" }}
          onMouseEnter={e => { e.currentTarget.style.backgroundColor = "#18181b"; e.currentTarget.style.color = "#e4e4e7" }}
          onMouseLeave={e => { e.currentTarget.style.backgroundColor = "transparent"; e.currentTarget.style.color = "#a1a1aa" }}>
          <FolderOpen size={15} /> Projects
        </button>
        <div className="relative">
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "#71717a" }} />
          <input value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="Tìm kiếm..."
            style={{ backgroundColor: "#18181b", borderColor: "#3f3f46", color: "#d4d4d8" }}
            className="w-full rounded-xl border pl-8 pr-3 py-2 text-xs placeholder-zinc-600 focus:outline-none" />
        </div>
      </div>

      <div className="flex-1 overflow-y-auto pb-2 scrollbar-thin scrollbar-track-transparent scrollbar-thumb-zinc-800">
        <div className="mt-2">
          {groups.length === 0 && !search && (
            <p className="px-5 py-2 text-xs" style={{ color: "#52525b" }}>Chưa có hội thoại nào</p>
          )}
          {groups.map(([label, convs]) => {
            const collapsed = collapsedGroups.has(label)
            return (
              <div key={label}>
                <button
                  onClick={() => toggleGroup(label)}
                  className="flex w-full items-center gap-1.5 px-4 pt-2 pb-1.5 text-[10px] font-semibold uppercase tracking-widest hover:opacity-80 transition-opacity"
                  style={{ color: "#a1a1aa" }}>
                  <span className="flex-1 text-left">{label}</span>
                  <span style={{ fontSize: 9 }}>{collapsed ? "▶" : "▼"}</span>
                </button>
                {!collapsed && convs.map((c) => (
                  <ConvItem key={c.id} conv={c} isActive={c.id === currentId}
                    projects={projects}
                    onSelect={() => handleSelect(c.id)}
                    onDelete={() => deleteConversation(c.id)}
                    onAddToProject={(projectId) => addConversationToProject(projectId, c.id)}
                    onDragStart={() => { dragIdRef.current = c.id }}
                    onDragOver={(e) => e.preventDefault()}
                    onDrop={() => handleDrop(c.id)} />
                ))}
              </div>
            )
          })}
        </div>
      </div>

      <div className="flex-shrink-0 px-4 py-3 flex items-center gap-3" style={{ borderTop: "1px solid #27272a" }}>
        <div className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-blue-600 text-xs font-semibold text-white">{initials}</div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate" style={{ color: "#e4e4e7" }}>{user?.name || user?.email || "—"}</p>
          <p className="text-xs truncate" style={{ color: "#71717a" }}>{user?.email || ""}</p>
        </div>
        <button onClick={logout} title="Đăng xuất" style={{ color: "#71717a" }} className="hover:text-white transition-colors"><Settings size={15} /></button>
      </div>
    </div>
  )
}
