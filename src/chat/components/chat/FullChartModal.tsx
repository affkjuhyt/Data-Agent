import { useState, useEffect } from "react"
import { X, BarChart2, TrendingUp, PieChart, Download } from "lucide-react"
import {
  BarChart, Bar, LineChart, Line, PieChart as RechartsPie, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer
} from "recharts"
import { ChartData } from "../../lib/types"

const COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#f43f5e", "#f97316",
  "#eab308", "#22c55e", "#14b8a6", "#0ea5e9", "#3b82f6"
]

type ChartType = "bar" | "line" | "pie"

const TYPE_ICONS: Record<ChartType, React.ReactNode> = {
  bar: <BarChart2 size={14} />,
  line: <TrendingUp size={14} />,
  pie: <PieChart size={14} />,
}

interface Props {
  data: ChartData
  onClose: () => void
}

function RechartsBar({ chartData }: { chartData: { name: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={chartData} margin={{ top: 8, right: 24, left: 8, bottom: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-35} textAnchor="end" interval={0} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }} formatter={(v) => [v, "Giá trị"]} />
        <Bar dataKey="value" radius={[4, 4, 0, 0]}>
          {chartData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}

function RechartsLine({ chartData }: { chartData: { name: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={chartData} margin={{ top: 8, right: 24, left: 8, bottom: 40 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-35} textAnchor="end" interval={0} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }} formatter={(v) => [v, "Giá trị"]} />
        <Legend />
        <Line type="monotone" dataKey="value" name="Giá trị" stroke={COLORS[0]} strokeWidth={2.5} dot={{ r: 4, fill: COLORS[0] }} activeDot={{ r: 6 }} />
      </LineChart>
    </ResponsiveContainer>
  )
}

function RechartsPieChart({ chartData }: { chartData: { name: string; value: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <RechartsPie>
        <Pie data={chartData} cx="50%" cy="50%" outerRadius="65%" dataKey="value" nameKey="name"
          label={({ name, percent }) => `${name} ${((percent ?? 0) * 100).toFixed(1)}%`} labelLine>
          {chartData.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: "1px solid #e5e7eb" }} formatter={(v) => [v, "Giá trị"]} />
        <Legend />
      </RechartsPie>
    </ResponsiveContainer>
  )
}

export function FullChartModal({ data, onClose }: Props) {
  const [activeType, setActiveType] = useState<ChartType>(data.type)

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose() }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [onClose])

  const chartData = data.labels.map((name, i) => ({ name, value: data.values[i] }))

  const handleDownload = () => {
    const csv = ["Nhãn,Giá trị", ...chartData.map(d => `${d.name},${d.value}`)].join("\n")
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = `${data.title.replace(/\s+/g, "_")}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
      onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="relative flex flex-col w-[min(92vw,860px)] h-[min(88vh,620px)] rounded-2xl bg-background border border-border shadow-2xl overflow-hidden">
        <div className="flex items-center gap-3 px-5 py-3.5 border-b border-border">
          <BarChart2 size={16} className="text-muted-foreground shrink-0" />
          <span className="font-semibold text-sm flex-1 truncate">{data.title}</span>
          {data.unit && <span className="text-[11px] text-muted-foreground bg-muted px-2 py-0.5 rounded-full">{data.unit}</span>}
          <div className="flex items-center gap-1 rounded-lg bg-muted p-0.5 ml-2">
            {(["bar", "line", "pie"] as ChartType[]).map(t => (
              <button key={t} onClick={() => setActiveType(t)}
                title={t === "bar" ? "Cột" : t === "line" ? "Đường" : "Tròn"}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                  activeType === t ? "bg-background shadow text-foreground" : "text-muted-foreground hover:text-foreground"
                }`}>
                {TYPE_ICONS[t]}
                <span className="capitalize">{t === "bar" ? "Cột" : t === "line" ? "Đường" : "Tròn"}</span>
              </button>
            ))}
          </div>
          <button onClick={handleDownload} title="Tải CSV"
            className="ml-1 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
            <Download size={14} />
          </button>
          <button onClick={onClose} className="p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
            <X size={16} />
          </button>
        </div>
        <div className="flex-1 p-5 min-h-0">
          {activeType === "bar" && <RechartsBar chartData={chartData} />}
          {activeType === "line" && <RechartsLine chartData={chartData} />}
          {activeType === "pie" && <RechartsPieChart chartData={chartData} />}
        </div>
        <div className="flex items-center gap-4 px-5 py-2.5 border-t border-border bg-muted/30">
          {[
            { label: "Tổng", val: data.values.reduce((a, b) => a + b, 0) },
            { label: "TB", val: +(data.values.reduce((a, b) => a + b, 0) / data.values.length).toFixed(2) },
            { label: "Max", val: Math.max(...data.values) },
            { label: "Min", val: Math.min(...data.values) },
          ].map(s => (
            <div key={s.label} className="flex items-center gap-1.5 text-[11px]">
              <span className="text-muted-foreground">{s.label}</span>
              <span className="font-mono font-semibold">{s.val}{data.unit ? ` ${data.unit}` : ""}</span>
            </div>
          ))}
          <span className="ml-auto text-[11px] text-muted-foreground">{data.labels.length} mục</span>
        </div>
      </div>
    </div>
  )
}
