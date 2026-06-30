import { Database, GitBranch, FileCode2, FileText } from "lucide-react"
import { Citation } from "../../lib/types"

const TYPE_CFG = {
  mcp:      { icon: <Database size={10} />,   color: "bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/50 dark:text-blue-400 dark:border-blue-800" },
  neo4j:    { icon: <GitBranch size={10} />,  color: "bg-violet-50 text-violet-700 border-violet-200 dark:bg-violet-950/50 dark:text-violet-400 dark:border-violet-800" },
  sql:      { icon: <FileCode2 size={10} />,  color: "bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/50 dark:text-emerald-400 dark:border-emerald-800" },
  document: { icon: <FileText size={10} />,   color: "bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/50 dark:text-amber-400 dark:border-amber-800" },
}

export function CitationBadge({ citations }: { citations: Citation[] }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 mt-2">
      <span className="text-[10px] text-muted-foreground">Nguồn:</span>
      {citations.map((c, i) => {
        const cfg = TYPE_CFG[c.type]
        return (
          <span key={i} className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono border ${cfg.color}`}>
            {cfg.icon}{c.source}
          </span>
        )
      })}
    </div>
  )
}
