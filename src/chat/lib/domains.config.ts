import { TrendingUp, BarChart2, Landmark, FileText, Zap } from "lucide-react"
import type { LucideIcon } from "lucide-react"

export interface DomainConfig {
  id: string
  label: string
  labelShort: string
  icon: LucideIcon
  // Sidebar conversation avatar
  avatarBg: string
  // ChatHeader badge
  badge: string
  // Welcome screen card
  cardColor: string
  cardBg: string
  cardBorder: string
  description: string
  examples: string[]
}

export const DOMAINS: DomainConfig[] = [
  {
    id: "enterprise",
    label: "Enterprise Analytics",
    labelShort: "Enterprise",
    icon: BarChart2,
    avatarBg: "bg-emerald-700",
    badge: "bg-emerald-100 text-emerald-800 border-emerald-200",
    cardColor: "text-emerald-400",
    cardBg: "bg-emerald-500/10",
    cardBorder: "border-emerald-500/30",
    description: "Phân tích dữ liệu kinh doanh, BI, báo cáo nội bộ",
    examples: ["Doanh thu quý 3 so với cùng kỳ", "Top 10 sản phẩm bán chạy", "Phân tích chi phí vận hành"],
  },
  {
    id: "stockllm",
    label: "StockLLM",
    labelShort: "StockLLM",
    icon: TrendingUp,
    avatarBg: "bg-amber-600",
    badge: "bg-amber-100 text-amber-800 border-amber-200",
    cardColor: "text-amber-400",
    cardBg: "bg-amber-500/10",
    cardBorder: "border-amber-500/30",
    description: "Phân tích cổ phiếu, thị trường tài chính, dự báo",
    examples: ["VN-Index hôm nay thế nào?", "Cổ phiếu VCB có nên mua?", "Phân tích kỹ thuật HPG"],
  },
  {
    id: "gov",
    label: "Gov Analytics",
    labelShort: "Gov",
    icon: Landmark,
    avatarBg: "bg-blue-700",
    badge: "bg-blue-100 text-blue-800 border-blue-200",
    cardColor: "text-blue-400",
    cardBg: "bg-blue-500/10",
    cardBorder: "border-blue-500/30",
    description: "Dữ liệu chính phủ, thống kê quốc gia, chính sách",
    examples: ["Tình trạng ngân sách nhà nước", "Tỷ lệ thất nghiệp theo quý", "Dữ liệu xuất nhập khẩu"],
  },
  {
    id: "document",
    label: "Document AI",
    labelShort: "Document",
    icon: FileText,
    avatarBg: "bg-violet-700",
    badge: "bg-violet-100 text-violet-800 border-violet-200",
    cardColor: "text-violet-400",
    cardBg: "bg-violet-500/10",
    cardBorder: "border-violet-500/30",
    description: "Phân tích, tóm tắt, trích xuất từ tài liệu",
    examples: ["Tóm tắt báo cáo thường niên", "Trích xuất điều khoản hợp đồng", "So sánh hai văn bản"],
  },
  {
    id: "forecast",
    label: "Forecast",
    labelShort: "Forecast",
    icon: Zap,
    avatarBg: "bg-orange-700",
    badge: "bg-orange-100 text-orange-800 border-orange-200",
    cardColor: "text-orange-400",
    cardBg: "bg-orange-500/10",
    cardBorder: "border-orange-500/30",
    description: "Dự báo xu hướng, time series, machine learning",
    examples: ["Dự báo doanh thu tháng tới", "Xu hướng nhu cầu sản phẩm", "Phân tích chuỗi thời gian"],
  },
]

export const DEFAULT_DOMAIN = "enterprise"

export const getDomain = (id: string): DomainConfig =>
  DOMAINS.find((d) => d.id === id) ?? DOMAINS[0]
