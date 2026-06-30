import { useState } from "react"
import { Search, Plus, FolderOpen, ChevronDown, Check } from "lucide-react"
import { formatDistanceToNow } from "date-fns"
import { vi } from "date-fns/locale"
import { useChatStore } from "../../lib/store"
import { Project } from "../../lib/types"
import { cn } from "../../lib/utils"

const PROJECT_COLORS = ["bg-blue-600", "bg-violet-600", "bg-emerald-600", "bg-amber-600", "bg-rose-600", "bg-orange-600"]

function NewProjectModal({ onClose, onConfirm }: { onClose: () => void; onConfirm: (name: string, color: string) => void }) {
  const [name, setName] = useState("")
  const [color, setColor] = useState(PROJECT_COLORS[0])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm" onClick={onClose}>
      <div className="w-[360px] rounded-2xl overflow-hidden shadow-2xl" style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46" }} onClick={e => e.stopPropagation()}>
        <div className={cn("h-1.5 w-full transition-colors duration-200", color)} />
        <div className="p-6">
          <div className="flex items-center gap-3 mb-5">
            <div className={cn("flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-xl text-white font-bold text-sm transition-colors duration-200", color)}>
              {name ? name[0].toUpperCase() : "P"}
            </div>
            <div>
              <h2 className="text-sm font-semibold" style={{ color: "#f4f4f5" }}>New project</h2>
              <p className="text-xs" style={{ color: "#71717a" }}>Group related conversations together</p>
            </div>
          </div>
          <input autoFocus value={name} onChange={e => setName(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && name.trim()) onConfirm(name.trim(), color) }}
            placeholder="Project name..."
            className="w-full rounded-xl px-3 py-2.5 text-sm placeholder-zinc-600 focus:outline-none mb-4"
            style={{ backgroundColor: "#27272a", border: "1px solid #3f3f46", color: "#e4e4e7" }} />
          <p className="text-[10px] font-semibold uppercase tracking-widest mb-2.5" style={{ color: "#71717a" }}>Color</p>
          <div className="flex gap-2.5 mb-6">
            {PROJECT_COLORS.map(c => (
              <button key={c} onClick={() => setColor(c)}
                className={cn("h-7 w-7 rounded-full flex items-center justify-center transition-all duration-150", c,
                  color === c ? "ring-2 ring-white ring-offset-2 ring-offset-[#1c1c1f] scale-110" : "opacity-60 hover:opacity-100 hover:scale-105")}>
                {color === c && <Check size={12} className="text-white" />}
              </button>
            ))}
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="px-3 py-1.5 text-xs rounded-lg transition-colors"
              style={{ color: "#71717a" }}
              onMouseEnter={e => (e.currentTarget.style.color = "#e4e4e7")}
              onMouseLeave={e => (e.currentTarget.style.color = "#71717a")}>
              Cancel
            </button>
            <button onClick={() => { if (name.trim()) onConfirm(name.trim(), color) }} disabled={!name.trim()}
              className="px-4 py-1.5 text-xs font-semibold rounded-xl text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              style={{ backgroundColor: "#3b82f6" }}
              onMouseEnter={e => !(!name.trim()) && (e.currentTarget.style.backgroundColor = "#2563eb")}
              onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3b82f6")}>
              Create project
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}

function ProjectCard({ project, convCount, onClick }: { project: Project; convCount: number; onClick: () => void }) {
  return (
    <button onClick={onClick}
      className="group flex flex-col rounded-2xl p-5 text-left transition-all duration-150 hover:-translate-y-0.5"
      style={{ backgroundColor: "#1c1c1f", border: "1px solid #2a2a2d" }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = "#3f3f46")}
      onMouseLeave={e => (e.currentTarget.style.borderColor = "#2a2a2d")}>
      <div className="flex items-start justify-between mb-4">
        <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl text-white text-sm font-bold", project.color)}>
          {project.name[0].toUpperCase()}
        </div>
      </div>
      <p className="text-sm font-semibold mb-1 truncate" style={{ color: "#f4f4f5" }}>{project.name}</p>
      {project.description && (
        <p className="text-xs leading-relaxed mb-3 line-clamp-2" style={{ color: "#71717a" }}>{project.description}</p>
      )}
      <div className="flex-1" />
      <div className="flex items-center justify-between mt-4">
        <p className="text-xs" style={{ color: "#52525b" }}>
          {convCount} conversation{convCount !== 1 ? "s" : ""}
        </p>
        <p className="text-xs" style={{ color: "#52525b" }}>
          Updated {formatDistanceToNow(new Date(project.updatedAt), { addSuffix: false, locale: vi })} ago
        </p>
      </div>
    </button>
  )
}

type SortKey = "updated" | "name" | "created"

export function ProjectsPage({ onOpenProject }: { onOpenProject: (id: string) => void }) {
  const projects = useChatStore(s => s.projects)
  const conversations = useChatStore(s => s.conversations)
  const createProject = useChatStore(s => s.createProject)
  const [search, setSearch] = useState("")
  const [sortBy, setSortBy] = useState<SortKey>("updated")
  const [showSort, setShowSort] = useState(false)
  const [showNewProject, setShowNewProject] = useState(false)

  const SORT_LABELS: Record<SortKey, string> = {
    updated: "Last updated",
    name: "Name",
    created: "Created",
  }

  const filtered = projects
    .filter(p => p.name.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name)
      if (sortBy === "created") return new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime()
      return new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
    })

  return (
    <div className="flex flex-1 flex-col overflow-hidden" style={{ backgroundColor: "#111113" }}>
      {/* Header */}
      <div className="flex items-center justify-between px-8 pt-8 pb-5 flex-shrink-0">
        <h1 className="text-2xl font-semibold" style={{ color: "#f4f4f5" }}>Projects</h1>
        <div className="flex items-center gap-3">
          {/* Sort */}
          <div className="relative">
            <button
              onClick={() => setShowSort(v => !v)}
              className="flex items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors"
              style={{ backgroundColor: "#1c1c1f", borderColor: "#3f3f46", color: "#a1a1aa" }}>
              Sort by&nbsp;<span style={{ color: "#e4e4e7" }}>{SORT_LABELS[sortBy]}</span>
              <ChevronDown size={14} className={cn("transition-transform", showSort && "rotate-180")} />
            </button>
            {showSort && (
              <>
                <div className="fixed inset-0 z-40" onClick={() => setShowSort(false)} />
                <div className="absolute right-0 top-full z-50 mt-1.5 w-44 rounded-xl overflow-hidden shadow-2xl" style={{ backgroundColor: "#1c1c1f", border: "1px solid #3f3f46" }}>
                  {(Object.entries(SORT_LABELS) as [SortKey, string][]).map(([key, label]) => (
                    <button key={key} onClick={() => { setSortBy(key); setShowSort(false) }}
                      className="flex w-full items-center justify-between px-4 py-2.5 text-sm transition-colors"
                      style={{ color: sortBy === key ? "#f4f4f5" : "#a1a1aa" }}
                      onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#27272a")}
                      onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}>
                      {label}
                      {sortBy === key && <Check size={13} style={{ color: "#3b82f6" }} />}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
          <button
            onClick={() => setShowNewProject(true)}
            className="flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-semibold text-white transition-colors"
            style={{ backgroundColor: "#3b82f6" }}
            onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2563eb")}
            onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3b82f6")}>
            <Plus size={15} /> New project
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="px-8 mb-6 flex-shrink-0">
        <div className="relative">
          <Search size={15} className="absolute left-4 top-1/2 -translate-y-1/2 pointer-events-none" style={{ color: "#52525b" }} />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search projects..."
            className="w-full rounded-xl border pl-10 pr-4 py-3 text-sm focus:outline-none transition-colors"
            style={{ backgroundColor: "#1c1c1f", borderColor: "#3f3f46", color: "#e4e4e7" }}
            onFocus={e => (e.currentTarget.style.borderColor = "#525252")}
            onBlur={e => (e.currentTarget.style.borderColor = "#3f3f46")}
          />
        </div>
      </div>

      {/* Grid */}
      <div className="flex-1 overflow-y-auto px-8 pb-8">
        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <div className="flex h-16 w-16 items-center justify-center rounded-2xl mb-4" style={{ backgroundColor: "#1c1c1f", border: "1px solid #2a2a2d" }}>
              <FolderOpen size={28} style={{ color: "#3f3f46" }} />
            </div>
            <p className="text-sm font-medium mb-1" style={{ color: "#a1a1aa" }}>
              {search ? "No projects found" : "No projects yet"}
            </p>
            <p className="text-xs mb-4" style={{ color: "#52525b" }}>
              {search ? "Try a different search term" : "Create a project to group related conversations"}
            </p>
            {!search && (
              <button onClick={() => setShowNewProject(true)}
                className="flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-semibold text-white transition-colors"
                style={{ backgroundColor: "#3b82f6" }}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#2563eb")}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = "#3b82f6")}>
                <Plus size={13} /> Create first project
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filtered.map(p => (
              <ProjectCard
                key={p.id}
                project={p}
                convCount={conversations.filter(c => p.conversationIds.includes(c.id)).length}
                onClick={() => onOpenProject(p.id)}
              />
            ))}
          </div>
        )}
      </div>

      {showNewProject && (
        <NewProjectModal
          onClose={() => setShowNewProject(false)}
          onConfirm={(name, color) => { createProject(name, color); setShowNewProject(false) }}
        />
      )}
    </div>
  )
}
