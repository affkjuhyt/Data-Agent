export function DataTable({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return (
    <div className="rounded-xl border border-border/50 overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/60 border-b border-border/50">
              {headers.map((h, i) => (
                <th key={i} className="px-3 py-2 text-left font-medium text-muted-foreground whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-border/30 last:border-0 hover:bg-muted/30 transition-colors">
                {row.map((cell, j) => (
                  <td key={j} className={`px-3 py-2 ${
                    typeof cell === "string" && cell.startsWith("-") ? "text-destructive"
                    : typeof cell === "string" && cell.startsWith("+") ? "text-emerald-600 dark:text-emerald-400"
                    : "text-foreground"
                  }`}>{cell}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
