import { useState } from "react"
import { ChevronRight, Sparkles } from "lucide-react"
import { useChatStore } from "./lib/store"
import { useChat } from "./hooks/useChat"
import { AgentDomain } from "./lib/types"
import { DOMAINS, DomainConfig, DEFAULT_DOMAIN } from "./lib/domains.config"
import { Sidebar } from "./components/layout/Sidebar"
import { ChatHeader } from "./components/layout/ChatHeader"
import { MessageList } from "./components/chat/MessageList"
import { ChatInput } from "./components/input/ChatInput"
import { ArtifactPanel } from "./components/artifact/ArtifactPanel"
import { MultipleChoiceCard, HITLCard } from "./components/chat/HITLCard"
import { ProjectsPage } from "./components/pages/ProjectsPage"
import { ProjectDetailPage } from "./components/pages/ProjectDetailPage"
import { cn } from "./lib/utils"

type View = "chat" | "projects" | "project-detail"

function WelcomeScreen({ onStart }: { onStart: (domain: AgentDomain, prompt?: string) => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center overflow-y-auto py-12 px-6">
      <div className="w-full max-w-2xl">
        <div className="flex flex-col items-center text-center mb-10">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-blue-600 text-white mb-4 shadow-lg shadow-blue-600/30">
            <Sparkles size={26} />
          </div>
          <h1 className="text-2xl font-bold text-foreground mb-2">Data Agent AI</h1>
          <p className="text-sm text-muted-foreground max-w-md leading-relaxed">
            Trợ lý phân tích dữ liệu thông minh. Chọn domain phù hợp với nhu cầu của bạn để bắt đầu.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {DOMAINS.map((d) => (
            <DomainCard key={d.id} domain={d} onClick={() => onStart(d.id)} />
          ))}
        </div>

        <div className="mt-8 rounded-2xl border border-border/50 bg-muted/20 p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-3 flex items-center gap-2">
            <Sparkles size={11} /> Câu hỏi gợi ý
          </p>
          <div className="flex flex-wrap gap-2">
            {DOMAINS.flatMap((d) => d.examples.slice(0, 1)).map((ex) => (
              <button key={ex}
                onClick={() => onStart(DEFAULT_DOMAIN, ex)}
                className="flex items-center gap-1.5 rounded-full border border-border/50 bg-background px-3 py-1.5 text-xs text-muted-foreground hover:border-primary/50 hover:text-primary hover:bg-primary/5 transition-all duration-150">
                {ex}
                <ChevronRight size={11} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function DomainCard({ domain, onClick }: { domain: DomainConfig; onClick: () => void }) {
  const Icon = domain.icon
  return (
    <button
      onClick={onClick}
      className={cn("group relative flex flex-col gap-3 rounded-2xl border p-4 text-left transition-all duration-200 hover:shadow-lg hover:-translate-y-0.5",
        domain.cardBg, domain.cardBorder, "hover:border-opacity-60")}>
      <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl", domain.cardBg, domain.cardColor)}>
        <Icon size={22} />
      </div>
      <div>
        <p className="text-sm font-semibold text-foreground mb-0.5">{domain.label}</p>
        <p className="text-xs text-muted-foreground leading-relaxed">{domain.description}</p>
      </div>
      <div className="flex flex-col gap-1 mt-auto">
        {domain.examples.map((ex) => (
          <div key={ex} className="flex items-center gap-1.5 text-[11px] text-muted-foreground/70">
            <div className={cn("h-1 w-1 rounded-full flex-shrink-0", domain.cardColor.replace("text-", "bg-"))} />
            <span className="truncate">{ex}</span>
          </div>
        ))}
      </div>
      <ChevronRight size={14} className={cn("absolute right-3 top-4 opacity-0 group-hover:opacity-100 transition-opacity", domain.cardColor)} />
    </button>
  )
}

export function ChatPage({ onExit }: { onExit?: () => void }) {
  const activeConversationId = useChatStore((s) => s.activeConversationId)
  const conversations = useChatStore((s) => s.conversations)
  const ui = useChatStore((s) => s.ui)
  const resolveHITL = useChatStore((s) => s.resolveHITL)
  const addMessage = useChatStore((s) => s.addMessage)
  const setActiveConversation = useChatStore((s) => s.setActiveConversation)
  const { startNewChat, sendMessage } = useChat()

  const [view, setView] = useState<View>("chat")
  const [viewProjectId, setViewProjectId] = useState<string | null>(null)

  const activeConversation = conversations.find((c) => c.id === activeConversationId) ?? null

  // Find last unresolved HITL (any type) — drives the overlay
  const pendingHITLMsg = activeConversation?.messages
    .filter((m) => m.hitl != null)
    .at(-1) ?? null

  const handleStart = (domain: AgentDomain, prompt?: string) => {
    const id = startNewChat(domain)
    if (prompt) setTimeout(() => sendMessage(prompt), 50)
  }

  const handleHITLSubmit = (choiceId: string, choiceLabel: string) => {
    if (!activeConversation || !pendingHITLMsg) return
    resolveHITL(activeConversation.id, pendingHITLMsg.id, choiceId)
    addMessage(activeConversation.id, {
      id: `hitl-reply-${Date.now()}`,
      role: "user",
      content: choiceId === "direct" ? choiceLabel : `✓ ${choiceLabel}`,
      timestamp: new Date(),
    })
  }

  const handleHITLSkip = () => {
    if (!activeConversation || !pendingHITLMsg) return
    resolveHITL(activeConversation.id, pendingHITLMsg.id, "skip")
  }

  const sidebarProps = {
    onExit,
    onNavigateProjects: () => setView("projects"),
    onSelectConversation: (id: string) => { setActiveConversation(id); setView("chat") },
  }

  if (view === "projects") {
    return (
      <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
        {!ui.isSidebarCollapsed && (
          <div className="w-[260px] flex-shrink-0 border-r border-border/50 overflow-hidden">
            <Sidebar {...sidebarProps} />
          </div>
        )}
        <ProjectsPage
          onOpenProject={(id) => { setViewProjectId(id); setView("project-detail") }}
        />
      </div>
    )
  }

  if (view === "project-detail" && viewProjectId) {
    return (
      <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
        {!ui.isSidebarCollapsed && (
          <div className="w-[260px] flex-shrink-0 border-r border-border/50 overflow-hidden">
            <Sidebar {...sidebarProps} />
          </div>
        )}
        <ProjectDetailPage
          projectId={viewProjectId}
          onBack={() => setView("projects")}
          onOpenConversation={(convId) => { setActiveConversation(convId); setView("chat") }}
        />
      </div>
    )
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      {!ui.isSidebarCollapsed && (
        <div className="w-[260px] flex-shrink-0 border-r border-border/50 overflow-hidden">
          <Sidebar {...sidebarProps} />
        </div>
      )}

      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {activeConversation ? (
          <>
            <ChatHeader conversation={activeConversation} onExit={onExit} />
            <div className="flex flex-1 overflow-hidden">
              <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
                <MessageList messages={activeConversation.messages} conversationId={activeConversation.id} />

                {/* HITL overlay — sits between messages and input, covers all HITL types */}
                {pendingHITLMsg && (
                  <div className="flex-shrink-0 px-4 pt-3 pb-3 flex justify-center bg-background">
                    <div className="w-full" style={{ maxWidth: "860px" }}>
                      {pendingHITLMsg.hitl!.choices && pendingHITLMsg.hitl!.choices!.length > 0 ? (
                        <MultipleChoiceCard
                          title={pendingHITLMsg.hitl!.title}
                          choices={pendingHITLMsg.hitl!.choices!}
                          onSubmit={handleHITLSubmit}
                          onSkip={handleHITLSkip}
                        />
                      ) : (
                        <HITLCard
                          hitl={pendingHITLMsg.hitl!}
                          messageId={pendingHITLMsg.id}
                          conversationId={activeConversation!.id}
                        />
                      )}
                    </div>
                  </div>
                )}

                <ChatInput conversationId={activeConversation.id} />
              </div>
              <ArtifactPanel />
            </div>
          </>
        ) : (
          <WelcomeScreen onStart={handleStart} />
        )}
      </div>
    </div>
  )
}
