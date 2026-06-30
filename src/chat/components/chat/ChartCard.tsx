import { useState } from "react"
import { BarChart2 } from "lucide-react"
import { ChartData } from "../../lib/types"
import { FullChartModal } from "./FullChartModal"

function Bar({ label, value, max }: { label: string; value: number; max: number }) {
  const pct = Math.abs(value / max) * 100
  const isNeg = value < 0
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 text-right text-[10px] text-muted-foreground truncate">{label}</span>
      <div className="flex-1 flex items-center gap-1.5">
        <div className="relative h-5 flex-1 rounded overflow-hidden bg-muted/50">
          <div className="absolute inset-y-0 left-0 rounded transition-all duration-500"
            style={{ width: `${pct}%`, background: isNeg ? "hsl(var(--destructive))" : "hsl(var(--primary))", opacity: isNeg ? 0.7 : 1 }} />
        </div>
        <span className={`text-[10px] font-mono min-w-[38px] text-right ${isNeg ? "text-destructive" : "text-foreground"}`}>
          {value > 0 ? "+" : ""}{value}
        </span>
      </div>
    </div>
  )
}

export function ChartCard({ data }: { data: ChartData }) {
  const [open, setOpen] = useState(false)
  const max = Math.max(...data.values.map(Math.abs))
  return (
    <>
      <div className="rounded-xl border border-border/50 bg-muted/30 p-3.5">
        <div className="flex items-center gap-2 mb-3">
          <BarChart2 size={13} className="text-muted-foreground" />
          <span className="text-xs font-medium">{data.title}</span>
          {data.unit && <span className="text-[10px] text-muted-foreground ml-auto">{data.unit}</span>}
        </div>
        <div className="flex flex-col gap-1.5">
          {data.labels.map((label, i) => <Bar key={i} label={label} value={data.values[i]} max={max} />)}
        </div>
        <button
          onClick={() => setOpen(true)}
          className="mt-3 w-full rounded-lg border border-border/50 py-1.5 text-[11px] text-muted-foreground hover:bg-muted transition-colors"
        >
          Xem biểu đồ đầy đủ →
        </button>
      </div>
      {open && <FullChartModal data={data} onClose={() => setOpen(false)} />}
    </>
  )
}
