import CodeMirror, { keymap } from "@uiw/react-codemirror"
import { sql, PostgreSQL } from "@codemirror/lang-sql"
import { python } from "@codemirror/lang-python"
import { oneDark } from "@codemirror/theme-one-dark"
import { acceptCompletion } from "@codemirror/autocomplete"
import { Prec } from "@codemirror/state"

interface SQLEditorProps {
  value: string
  onChange: (v: string) => void
  language?: "sql" | "python"
  onCtrlEnter?: () => void
}

export function SQLEditor({ value, onChange, language = "sql", onCtrlEnter }: SQLEditorProps) {
  const isSQL = language === "sql"
  const isPython = language === "python"

  const extensions = [
    // Tab accepts completion when popup is open, falls through to indent otherwise
    Prec.highest(keymap.of([{ key: "Tab", run: acceptCompletion }])),
    keymap.of([{ key: "Mod-Enter", run: () => { onCtrlEnter?.(); return true } }]),
    isSQL ? sql({ dialect: PostgreSQL }) : python(),
  ]

  return (
    <div className="flex flex-col bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-800 bg-zinc-900 px-3 py-1.5">
        <div className="flex items-center gap-2.5">
          <div className="flex gap-1">
            {["bg-red-500", "bg-amber-400", "bg-emerald-400"].map((c, i) => (
              <div key={i} className={`h-2 w-2 rounded-full ${c} opacity-80`} />
            ))}
          </div>
          <span className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${isSQL ? "bg-amber-400/15 text-amber-300" : "bg-blue-400/15 text-blue-300"}`}>
            {language.toUpperCase()}
          </span>
          <span className="text-[10px] text-zinc-500 font-mono">
            Ctrl+Enter chạy · Tab hoàn thành
          </span>
        </div>
      </div>

      <div className="codemirror-wrapper" style={{ minHeight: 180 }}>
        <CodeMirror
          value={value}
          onChange={onChange}
          extensions={extensions}
          theme={oneDark}
          basicSetup={{
            lineNumbers: true,
            foldGutter: false,
            dropCursor: false,
            allowMultipleSelections: false,
            indentOnInput: true,
            bracketMatching: true,
            closeBrackets: true,
            autocompletion: true,
            rectangularSelection: false,
            crosshairCursor: false,
            highlightActiveLine: true,
            highlightSelectionMatches: false,
            closeBracketsKeymap: true,
            searchKeymap: false,
            foldKeymap: false,
            completionKeymap: true,
            lintKeymap: false,
          }}
          style={{ minHeight: 180, fontSize: "12.5px" }}
        />
      </div>

      <div className="flex items-center justify-between border-t border-zinc-800 bg-zinc-900 px-3 py-1">
        <div className="flex items-center gap-3 text-[10px] text-zinc-600">
          {isPython && (
            <>
              <span className="flex items-center gap-1">
                <div className="h-1.5 w-1.5 rounded-full bg-blue-500" />
                Python 3.11
              </span>
              <span>pandas · numpy · plotly</span>
            </>
          )}
          {isSQL && (
            <>
              <span className="flex items-center gap-1">
                <div className="h-1.5 w-1.5 rounded-full bg-amber-500" />
                PostgreSQL
              </span>
              <span>DML · DQL · CTEs · Window Functions</span>
            </>
          )}
        </div>
        <span className="text-[10px] text-zinc-700">{value.split("\n").length} dòng · {value.length} ký tự</span>
      </div>
    </div>
  )
}
