import { create } from "zustand"
import { persist } from "zustand/middleware"
import { Conversation, Message, InputMode, AgentDomain, Artifact, Project, ProjectFile } from "./types"
import { MOCK_CONVERSATIONS, MOCK_ARTIFACTS, MCP_TOOLS, AGENT_SKILLS, MOCK_PROJECTS } from "./mock-data"
import { DEFAULT_DOMAIN } from "./domains.config"

interface UIState {
  isSidebarCollapsed: boolean
  isRightPanelOpen: boolean
  currentInputMode: InputMode
  selectedDomain: AgentDomain | null
}

interface ChatStore {
  conversations: Conversation[]
  activeConversationId: string | null
  projects: Project[]
  activeProjectId: string | null
  ui: UIState
  enabledToolIds: string[]
  activeSkillIds: string[]

  artifacts: Artifact[]
  openArtifactIds: string[]
  activeArtifactId: string | null
  isArtifactPanelOpen: boolean
  addArtifact: (artifact: Artifact) => void
  setActiveArtifact: (id: string | null) => void
  openArtifact: (id: string) => void
  closeArtifact: (id: string) => void
  reorderArtifacts: (ids: string[]) => void
  toggleArtifactPanel: () => void

  createProject: (name: string, color?: string) => string
  deleteProject: (id: string) => void
  renameProject: (id: string, name: string) => void
  setActiveProject: (id: string | null) => void
  addConversationToProject: (projectId: string, conversationId: string) => void
  removeConversationFromProject: (projectId: string, conversationId: string) => void
  updateProjectInstructions: (id: string, instructions: string) => void
  updateProjectMemory: (id: string, memory: string) => void
  addProjectFile: (id: string, file: ProjectFile) => void
  removeProjectFile: (id: string, fileId: string) => void

  setActiveConversation: (id: string | null) => void
  createConversation: (domain: AgentDomain) => string
  deleteConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  clearConversationMessages: (id: string) => void
  getActiveConversation: () => Conversation | undefined
  addMessage: (conversationId: string, message: Message) => void
  updateMessage: (conversationId: string, messageId: string, updates: Partial<Message>) => void
  resolveHITL: (conversationId: string, messageId: string, actionId: string) => void
  toggleSidebar: () => void
  toggleRightPanel: () => void
  setInputMode: (mode: InputMode) => void
  setSelectedDomain: (domain: AgentDomain | null) => void
  toggleTool: (id: string) => void
  setDefaultToolsForDomain: (domain: AgentDomain) => void
  toggleSkill: (id: string) => void
  clearSkills: () => void
  pinMessage: (conversationId: string, messageId: string, pinned: boolean) => void
  bookmarkMessage: (conversationId: string, messageId: string, bookmarked: boolean) => void
  reorderConversations: (ids: string[]) => void
  setConversationSummary: (conversationId: string, summary: string) => void
}

export const useChatStore = create<ChatStore>()(
  persist(
    (set, get) => ({
      conversations: MOCK_CONVERSATIONS,
      activeConversationId: MOCK_CONVERSATIONS[0]?.id ?? null,
      projects: MOCK_PROJECTS,
      activeProjectId: null,
      artifacts: MOCK_ARTIFACTS,
      openArtifactIds: ["art-1", "art-2"],
      activeArtifactId: "art-1",
      isArtifactPanelOpen: true,
      enabledToolIds: ["hose_foreign_flow", "yfinance", "neo4j_query"],
      activeSkillIds: [],

      ui: {
        isSidebarCollapsed: false,
        isRightPanelOpen: true,
        currentInputMode: "text",
        selectedDomain: DEFAULT_DOMAIN,
      },

      addArtifact: (artifact) =>
        set((s) => {
          const openArtifactIds = s.openArtifactIds.includes(artifact.id)
            ? s.openArtifactIds
            : [...s.openArtifactIds, artifact.id]
          return { artifacts: [artifact, ...s.artifacts], openArtifactIds, activeArtifactId: artifact.id, isArtifactPanelOpen: true }
        }),
      setActiveArtifact: (id) => {
        if (id === null) {
          set((s) => {
            const remaining = s.openArtifactIds
            return { activeArtifactId: null, isArtifactPanelOpen: remaining.length > 0 }
          })
        } else {
          get().openArtifact(id)
        }
      },
      openArtifact: (id) =>
        set((s) => ({
          openArtifactIds: s.openArtifactIds.includes(id) ? s.openArtifactIds : [...s.openArtifactIds, id],
          activeArtifactId: id,
          isArtifactPanelOpen: true,
        })),
      closeArtifact: (id) =>
        set((s) => {
          const remaining = s.openArtifactIds.filter((x) => x !== id)
          return {
            openArtifactIds: remaining,
            activeArtifactId: remaining.length > 0 ? remaining[remaining.length - 1] : null,
            isArtifactPanelOpen: remaining.length > 0,
          }
        }),
      reorderArtifacts: (ids) => set({ openArtifactIds: ids }),
      toggleArtifactPanel: () => set((s) => ({ isArtifactPanelOpen: !s.isArtifactPanelOpen })),

      createProject: (name, color = "bg-blue-600") => {
        const id = `proj-${Date.now()}`
        const project: Project = {
          id, name, color, conversationIds: [],
          createdAt: new Date(), updatedAt: new Date(),
        }
        set((s) => ({ projects: [project, ...s.projects] }))
        return id
      },

      deleteProject: (id) =>
        set((s) => ({
          projects: s.projects.filter((p) => p.id !== id),
          activeProjectId: s.activeProjectId === id ? null : s.activeProjectId,
        })),

      renameProject: (id, name) =>
        set((s) => ({ projects: s.projects.map((p) => p.id === id ? { ...p, name, updatedAt: new Date() } : p) })),

      setActiveProject: (id) => set({ activeProjectId: id }),

      addConversationToProject: (projectId, conversationId) =>
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === projectId && !p.conversationIds.includes(conversationId)
              ? { ...p, conversationIds: [...p.conversationIds, conversationId], updatedAt: new Date() }
              : p
          ),
        })),

      removeConversationFromProject: (projectId, conversationId) =>
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === projectId
              ? { ...p, conversationIds: p.conversationIds.filter((c) => c !== conversationId), updatedAt: new Date() }
              : p
          ),
        })),

      updateProjectInstructions: (id, instructions) =>
        set((s) => ({
          projects: s.projects.map((p) => p.id === id ? { ...p, instructions, updatedAt: new Date() } : p),
        })),

      updateProjectMemory: (id, memory) =>
        set((s) => ({
          projects: s.projects.map((p) => p.id === id ? { ...p, memory, updatedAt: new Date() } : p),
        })),

      addProjectFile: (id, file) =>
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === id ? { ...p, files: [file, ...(p.files ?? [])], updatedAt: new Date() } : p
          ),
        })),

      removeProjectFile: (id, fileId) =>
        set((s) => ({
          projects: s.projects.map((p) =>
            p.id === id ? { ...p, files: (p.files ?? []).filter((f) => f.id !== fileId), updatedAt: new Date() } : p
          ),
        })),

      setActiveConversation: (id) => set({ activeConversationId: id }),

      getActiveConversation: () => {
        const { conversations, activeConversationId } = get()
        return conversations.find((c) => c.id === activeConversationId)
      },

      createConversation: (domain) => {
        const id = `conv-${Date.now()}`
        const newConv: Conversation = {
          id, title: "Cuộc hội thoại mới", domain,
          messages: [], createdAt: new Date(), updatedAt: new Date(),
        }
        set((s) => ({ conversations: [newConv, ...s.conversations], activeConversationId: id }))
        return id
      },

      renameConversation: (id, title) =>
        set((s) => ({
          conversations: s.conversations.map((c) => c.id === id ? { ...c, title } : c),
        })),

      clearConversationMessages: (id) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === id ? { ...c, messages: [], updatedAt: new Date() } : c
          ),
        })),

      deleteConversation: (id) =>
        set((s) => ({
          conversations: s.conversations.filter((c) => c.id !== id),
          activeConversationId: s.activeConversationId === id
            ? (s.conversations.find(c => c.id !== id)?.id ?? null)
            : s.activeConversationId,
        })),

      addMessage: (conversationId, message) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, messages: [...c.messages, message], updatedAt: new Date() }
              : c
          ),
        })),

      updateMessage: (conversationId, messageId, updates) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, messages: c.messages.map((m) => m.id === messageId ? { ...m, ...updates } : m) }
              : c
          ),
        })),

      resolveHITL: (conversationId, messageId, _actionId) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, messages: c.messages.map((m) => m.id === messageId ? { ...m, hitl: undefined } : m) }
              : c
          ),
        })),

      toggleSidebar: () =>
        set((s) => ({ ui: { ...s.ui, isSidebarCollapsed: !s.ui.isSidebarCollapsed } })),

      toggleRightPanel: () =>
        set((s) => ({ ui: { ...s.ui, isRightPanelOpen: !s.ui.isRightPanelOpen } })),

      setInputMode: (mode) =>
        set((s) => ({ ui: { ...s.ui, currentInputMode: mode } })),

      setSelectedDomain: (domain) => {
        const defaults = domain
          ? MCP_TOOLS.filter(t => t.domain.includes(domain) && t.isDefault).map(t => t.id)
          : []
        set(s => ({ ui: { ...s.ui, selectedDomain: domain }, enabledToolIds: defaults }))
      },

      toggleTool: (id) => set(s => ({
        enabledToolIds: s.enabledToolIds.includes(id)
          ? s.enabledToolIds.filter(t => t !== id)
          : [...s.enabledToolIds, id]
      })),

      setDefaultToolsForDomain: (domain) => {
        const defaults = MCP_TOOLS.filter(t => t.domain.includes(domain) && t.isDefault).map(t => t.id)
        set({ enabledToolIds: defaults })
      },

      toggleSkill: (id) => set(s => ({
        activeSkillIds: s.activeSkillIds.includes(id)
          ? s.activeSkillIds.filter(x => x !== id)
          : [...s.activeSkillIds, id]
      })),

      clearSkills: () => set({ activeSkillIds: [] }),

      pinMessage: (conversationId, messageId, pinned) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, messages: c.messages.map((m) => m.id === messageId ? { ...m, pinned } : m) }
              : c
          ),
        })),

      bookmarkMessage: (conversationId, messageId, bookmarked) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId
              ? { ...c, messages: c.messages.map((m) => m.id === messageId ? { ...m, bookmarked } : m) }
              : c
          ),
        })),

      reorderConversations: (ids) =>
        set((s) => {
          const map = new Map(s.conversations.map((c) => [c.id, c]))
          const reordered = ids.map((id) => map.get(id)).filter(Boolean) as typeof s.conversations
          const rest = s.conversations.filter((c) => !ids.includes(c.id))
          return { conversations: [...reordered, ...rest] }
        }),

      setConversationSummary: (conversationId, summary) =>
        set((s) => ({
          conversations: s.conversations.map((c) =>
            c.id === conversationId ? { ...c, summary } : c
          ),
        })),
    }),
    {
      name: "data-agent-chat",
      partialize: (s) => ({
        conversations: s.conversations,
        activeConversationId: s.activeConversationId,
        projects: s.projects,
        activeProjectId: s.activeProjectId,
      }),
      merge: (persisted: any, current) => ({
        ...current,
        ...persisted,
        // Fall back to mock data when persisted state has empty arrays
        projects: persisted?.projects?.length > 0 ? persisted.projects : MOCK_PROJECTS,
        conversations: persisted?.conversations?.length > 0 ? persisted.conversations : current.conversations,
      }),
    }
  )
)
