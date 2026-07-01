import { useCallback } from "react"
import { useChatStore } from "../lib/store"
import { Message, AgentStep } from "../lib/types"
import { AGENT_SKILLS } from "../lib/mock-data"
import { DEFAULT_DOMAIN } from "../lib/domains.config"

const MOCK_STEPS: AgentStep[] = [
  { id: "s1", workerName: "Planner",     description: "Phân tích yêu cầu",       status: "done",    durationMs: 210 },
  { id: "s2", workerName: "DataFetcher", description: "Truy vấn dữ liệu",        status: "done",    durationMs: 540 },
  { id: "s3", workerName: "Analyst",     description: "Phân tích & tổng hợp",    status: "done",    durationMs: 380 },
  { id: "s4", workerName: "Writer",      description: "Tạo câu trả lời",         status: "done",    durationMs: 190 },
]

const MOCK_FOLLOW_UPS = [
  ["So sánh với kỳ trước?", "Phân tích chi tiết hơn?", "Xuất báo cáo PDF?"],
  ["Dự báo xu hướng?", "Xem biểu đồ?", "Lọc theo ngành?"],
  ["Tìm nguyên nhân?", "Gợi ý giải pháp?", "Chia sẻ kết quả?"],
]

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

    // Add streaming placeholder
    const aiId = `msg-${Date.now() + 1}`
    store.addMessage(targetId, {
      id: aiId, role: "assistant", content: "", timestamp: new Date(),
      isStreaming: true,
      agentTrace: MOCK_STEPS.map((s) => ({ ...s, status: "pending" as const })),
    })

    // Simulate streaming steps one by one
    for (let i = 0; i < MOCK_STEPS.length; i++) {
      await new Promise((r) => setTimeout(r, 300 + i * 150))
      store.updateMessage(targetId, aiId, {
        agentTrace: MOCK_STEPS.map((s, j) => ({
          ...s,
          status: j < i ? "done" : j === i ? "running" : "pending",
        })),
      })
    }

    await new Promise((r) => setTimeout(r, 400))

    const followUps = MOCK_FOLLOW_UPS[Math.floor(Math.random() * MOCK_FOLLOW_UPS.length)]
    store.updateMessage(targetId, aiId, {
      content: "Mock response: agent đã xử lý câu hỏi của bạn. Tích hợp LangGraph ở sprint sau.",
      isStreaming: false,
      agentTrace: MOCK_STEPS,
      confidence: 75 + Math.floor(Math.random() * 24),
      followUps,
      tokenCount: 800 + Math.floor(Math.random() * 3200),
    })
  }, [store])

  const startNewChat = useCallback((domain: string = DEFAULT_DOMAIN) => {
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
