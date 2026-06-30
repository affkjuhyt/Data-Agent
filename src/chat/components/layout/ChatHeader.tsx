import { useState } from "react"
import { Download, MoreHorizontal, PanelLeft, PanelLeftClose, FlaskConical, ChevronDown, X, FolderOpen } from "lucide-react"
import { MoreOptionsMenu } from "../chat/MoreOptionsMenu"
import { Conversation, AgentDomain, Message } from "../../lib/types"
import { useChatStore } from "../../lib/store"
import { cn } from "../../lib/utils"

const DOMAIN_BADGE: Record<AgentDomain, string> = {
  stockllm:   "bg-amber-100 text-amber-800 border-amber-200",
  enterprise: "bg-emerald-100 text-emerald-800 border-emerald-200",
  gov:        "bg-blue-100 text-blue-800 border-blue-200",
  document:   "bg-violet-100 text-violet-800 border-violet-200",
  forecast:   "bg-orange-100 text-orange-800 border-orange-200",
}
const DOMAIN_LABEL: Record<AgentDomain, string> = {
  stockllm: "StockLLM", enterprise: "Enterprise",
  gov: "Gov", document: "Document", forecast: "Forecast",
}

/* ── HITL test scenarios ─────────────────────────────────── */
interface HITLScenario {
  id: string
  label: string
  emoji: string
  message: Omit<Message, "id" | "timestamp">
}

const HITL_SCENARIOS: HITLScenario[] = [
  {
    id: "email-send",
    label: "Gửi email hàng loạt",
    emoji: "📧",
    message: {
      role: "assistant",
      content: "Tôi đã tổng hợp báo cáo ngành thép tuần này. Trước khi gửi, cần xác nhận của bạn.",
      hitl: {
        title: "Xác nhận gửi email",
        description: 'Tạo báo cáo PDF và gửi email cho 12 người theo dõi trong danh sách "Steel Watchers"?',
        expiresAt: new Date(Date.now() + 5 * 60 * 1000),
        actions: [
          { id: "confirm-send", label: "Xác nhận gửi", variant: "confirm" },
          { id: "preview",      label: "Xem trước",    variant: "preview" },
          { id: "cancel",       label: "Huỷ",          variant: "cancel" },
        ],
      },
    },
  },
  {
    id: "db-write",
    label: "Ghi vào database",
    emoji: "🗄️",
    message: {
      role: "assistant",
      content: "Phân tích hoàn tất. Agent muốn lưu kết quả vào database production.",
      hitl: {
        title: "Ghi dữ liệu vào Production DB",
        description: "INSERT 1,204 bản ghi vào bảng `stock_signals` trên PostgreSQL production. Thao tác này không thể hoàn tác.",
        expiresAt: new Date(Date.now() + 2 * 60 * 1000),
        actions: [
          { id: "confirm-write", label: "Cho phép ghi",   variant: "confirm" },
          { id: "cancel",        label: "Không cho phép", variant: "cancel" },
        ],
      },
    },
  },
  {
    id: "api-call",
    label: "Gọi API bên ngoài",
    emoji: "🌐",
    message: {
      role: "assistant",
      content: "Để hoàn thành yêu cầu, agent cần gọi API của bên thứ ba với dữ liệu khách hàng.",
      hitl: {
        title: "Gọi API bên ngoài",
        description: "POST dữ liệu KYC (tên, CCCD, số điện thoại) của 3 khách hàng lên API VNeID để xác thực danh tính.",
        expiresAt: new Date(Date.now() + 90 * 1000),
        actions: [
          { id: "confirm-api", label: "Cho phép gọi",  variant: "confirm" },
          { id: "preview",     label: "Xem payload",   variant: "preview" },
          { id: "cancel",      label: "Huỷ",           variant: "cancel" },
        ],
      },
    },
  },
  {
    id: "urgent",
    label: "Khẩn cấp (20 giây)",
    emoji: "⚠️",
    message: {
      role: "assistant",
      content: "Phát hiện tín hiệu bán mạnh HPG. Agent đề xuất hành động ngay.",
      hitl: {
        title: "⚠️ Cảnh báo khẩn: Tín hiệu bán HPG",
        description: "Thuật toán phát hiện cổ phiếu HPG vượt ngưỡng stop-loss -7%. Xác nhận để gửi lệnh bán tự động cho 3 tài khoản.",
        expiresAt: new Date(Date.now() + 20 * 1000),
        actions: [
          { id: "confirm-sell", label: "Xác nhận bán", variant: "confirm" },
          { id: "cancel",       label: "Bỏ qua",       variant: "cancel" },
        ],
      },
    },
  },
  {
    id: "multiple-choice",
    label: "Chọn định dạng báo cáo",
    emoji: "📋",
    message: {
      role: "assistant",
      content: "Phân tích hoàn tất. Chọn định dạng bạn muốn nhận báo cáo:",
      hitl: {
        title: "Chọn định dạng xuất báo cáo",
        description: "Agent đã tổng hợp xong dữ liệu. Vui lòng chọn định dạng phù hợp trước khi xuất.",
        expiresAt: new Date(Date.now() + 10 * 60 * 1000),
        choices: [
          {
            id: "pdf",
            label: "PDF Report",
            badge: "Phổ biến",
            description: "Báo cáo có đồ thị, bảng biểu, sẵn sàng trình bày. Khoảng 8–12 trang.",
          },
          {
            id: "excel",
            label: "Excel Workbook",
            description: "Dữ liệu thô + sheet tóm tắt. Phù hợp để tự phân tích tiếp.",
          },
          {
            id: "dashboard",
            label: "Interactive Dashboard",
            badge: "Mới",
            description: "Dashboard Plotly nhúng trực tiếp trong chat, có thể tương tác và lọc.",
          },
          {
            id: "email",
            label: "Gửi email tóm tắt",
            description: "Gửi bản tóm tắt 1 trang qua email cho các bên liên quan.",
          },
        ],
        actions: [
          { id: "confirm-export", label: "Xuất ngay", variant: "confirm" },
          { id: "cancel",         label: "Huỷ",       variant: "cancel" },
        ],
      },
    },
  },
  {
    id: "model-select",
    label: "Chọn mô hình AI",
    emoji: "🤖",
    message: {
      role: "assistant",
      content: "Yêu cầu phân tích phức tạp. Chọn mô hình AI phù hợp để tiếp tục:",
      hitl: {
        title: "Chọn mô hình AI cho tác vụ này",
        description: "Tác vụ dự báo chuỗi thời gian yêu cầu khả năng suy luận cao. Chọn mô hình bạn muốn sử dụng.",
        choices: [
          {
            id: "claude-opus",
            label: "Claude Opus 4",
            badge: "Mạnh nhất",
            description: "Suy luận sâu nhất, phù hợp tác vụ phân tích phức tạp. Chậm hơn ~2×.",
          },
          {
            id: "claude-sonnet",
            label: "Claude Sonnet 4.6",
            badge: "Khuyến nghị",
            description: "Cân bằng tốc độ và chất lượng. Lý tưởng cho hầu hết tác vụ.",
          },
          {
            id: "claude-haiku",
            label: "Claude Haiku 4.5",
            description: "Nhanh nhất, tiết kiệm chi phí. Phù hợp với tác vụ đơn giản.",
          },
        ],
        actions: [
          { id: "confirm-model", label: "Dùng mô hình này", variant: "confirm" },
          { id: "cancel",        label: "Huỷ",              variant: "cancel" },
        ],
      },
    },
  },
]

function HITLTestMenu({ conversationId, onClose }: { conversationId: string; onClose: () => void }) {
  const addMessage = useChatStore((s) => s.addMessage)

  const inject = (scenario: HITLScenario) => {
    const msg: Message = {
      ...scenario.message,
      id: `hitl-test-${Date.now()}`,
      timestamp: new Date(),
    }
    addMessage(conversationId, msg)
    onClose()
  }

  return (
    <>
      <div className="fixed inset-0 z-40" onClick={onClose} />
      <div className="absolute right-0 top-full z-50 mt-2 w-64 rounded-2xl border border-zinc-700/60 bg-zinc-900 p-2 shadow-2xl">
        <div className="px-2 py-1.5 mb-1">
          <p className="text-[11px] font-semibold text-zinc-300">Thêm HITL test vào chat</p>
          <p className="text-[10px] text-zinc-600 mt-0.5">Chọn kịch bản để inject vào conversation</p>
        </div>
        <div className="h-px bg-zinc-800 mb-1.5" />
        {HITL_SCENARIOS.map((s) => (
          <button key={s.id} onClick={() => inject(s)}
            className="flex w-full items-center gap-2.5 rounded-xl px-3 py-2.5 text-left hover:bg-zinc-800 transition-colors group">
            <span className="text-base flex-shrink-0">{s.emoji}</span>
            <span className="text-xs text-zinc-300 group-hover:text-zinc-100 transition-colors">{s.label}</span>
          </button>
        ))}
        <div className="h-px bg-zinc-800 my-1.5" />
        <p className="px-3 py-1 text-[9px] text-zinc-700 flex items-center gap-1">
          <FlaskConical size={9} /> Dev/test only — không gọi API thật
        </p>
      </div>
    </>
  )
}

export function ChatHeader({ conversation, onExit }: { conversation: Conversation; onExit?: () => void }) {
  const { ui, toggleSidebar } = useChatStore()
  const projects = useChatStore(s => s.projects)
  const hasRunning = conversation.messages.some(m => m.agentTrace?.some(s => s.status === "running"))
  const [moreOpen, setMoreOpen] = useState(false)
  const [hitlMenuOpen, setHitlMenuOpen] = useState(false)

  const convProjects = projects.filter(p => p.conversationIds.includes(conversation.id))

  return (
    <header className="flex h-14 flex-shrink-0 items-center gap-3 border-b border-border/50 bg-background px-4">
      <button onClick={toggleSidebar}
        title={ui.isSidebarCollapsed ? "Mở sidebar" : "Đóng sidebar"}
        className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
        {ui.isSidebarCollapsed ? <PanelLeft size={15} /> : <PanelLeftClose size={15} />}
      </button>

      <div className="flex-1 min-w-0 flex items-center gap-2">
        <h1 className="text-sm font-medium text-foreground truncate">{conversation.title}</h1>
        <div className={cn("flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium flex-shrink-0", DOMAIN_BADGE[conversation.domain])}>
          {hasRunning && (
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-500" />
            </span>
          )}
          {DOMAIN_LABEL[conversation.domain]} Agent
        </div>
        {convProjects.map(p => (
          <div key={p.id}
            className={cn("flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium flex-shrink-0 text-white", p.color)}
            style={{ opacity: 0.85 }}>
            <FolderOpen size={10} />
            {p.name}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-1">
        {/* HITL Test Button */}
        <div className="relative">
          <button
            onClick={() => setHitlMenuOpen((v) => !v)}
            title="Test Human-in-the-Loop"
            className={cn(
              "flex items-center gap-1.5 h-8 px-2.5 rounded-lg text-xs font-medium transition-colors border",
              hitlMenuOpen
                ? "bg-amber-500/20 text-amber-400 border-amber-500/40"
                : "text-zinc-500 border-zinc-700/50 hover:bg-zinc-800 hover:text-amber-400 hover:border-amber-500/30"
            )}>
            <FlaskConical size={13} />
            <span className="hidden sm:inline">HITL</span>
            <ChevronDown size={10} className={cn("transition-transform", hitlMenuOpen && "rotate-180")} />
          </button>
          {hitlMenuOpen && (
            <HITLTestMenu conversationId={conversation.id} onClose={() => setHitlMenuOpen(false)} />
          )}
        </div>

        <button title="Xuất PDF/CSV"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground transition-colors">
          <Download size={15} />
        </button>

        <div className="relative">
          <button onClick={() => setMoreOpen((v) => !v)} title="Tuỳ chọn"
            className={cn("flex h-8 w-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground transition-colors",
              moreOpen && "bg-accent text-foreground")}>
            <MoreHorizontal size={15} />
          </button>
          {moreOpen && <MoreOptionsMenu conversation={conversation} onClose={() => setMoreOpen(false)} />}
        </div>

        {onExit && (
          <button onClick={onExit} title="Thoát"
            className="ml-1 flex h-8 items-center gap-1.5 px-3 rounded-lg text-xs text-muted-foreground hover:bg-accent hover:text-foreground transition-colors border border-border/50">
            <X size={12} /> Thoát
          </button>
        )}
      </div>
    </header>
  )
}
