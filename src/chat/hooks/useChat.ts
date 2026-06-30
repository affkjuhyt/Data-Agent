import { useCallback } from "react"
import { useChatStore } from "../lib/store"
import { Message, AgentDomain } from "../lib/types"
import { AGENT_SKILLS } from "../lib/mock-data"

export function useChat() {
  const store = useChatStore()

  const sendMessage = useCallback(async (content: string, conversationId?: string) => {
    const { activeSkillIds } = useChatStore.getState()
    const skillPrompts = AGENT_SKILLS
      .filter(s => activeSkillIds.includes(s.id))
      .map(s => s.promptTemplate)

    console.log("Active skill prompts:", skillPrompts)
    console.log("User message:", content)

    const targetId = conversationId ?? store.activeConversationId
    if (!targetId) return

    const userMsg: Message = {
      id: `msg-${Date.now()}`, role: "user",
      content, timestamp: new Date(),
    }
    store.addMessage(targetId, userMsg)

    await new Promise((r) => setTimeout(r, 1500))

    const aiMsg: Message = {
      id: `msg-${Date.now() + 1}`, role: "assistant",
      content: "Mock response: agent đang xử lý câu hỏi của bạn. Tích hợp LangGraph ở sprint sau.",
      timestamp: new Date(),
    }
    store.addMessage(targetId, aiMsg)
  }, [store])

  const startNewChat = useCallback((domain: AgentDomain = "stockllm") => {
    const id = store.createConversation(domain)
    store.setActiveConversation(id)
    return id
  }, [store])

  return {
    conversations: store.conversations,
    activeConversation: store.getActiveConversation(),
    activeConversationId: store.activeConversationId,
    ui: store.ui,
    sendMessage,
    startNewChat,
    setActiveConversation: store.setActiveConversation,
    resolveHITL: store.resolveHITL,
    toggleSidebar: store.toggleSidebar,
    setInputMode: store.setInputMode,
  }
}
