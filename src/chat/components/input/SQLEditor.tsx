import { lazy, Suspense, useRef, useCallback } from "react"
import type { OnMount } from "@monaco-editor/react"
import { pgsqlSnippets } from "monaco-sql-languages/esm/languages/pgsql/pgsql.snippet"

const MonacoEditor = lazy(() => import("@monaco-editor/react"))

const SQL_SNIPPETS: Array<{ label: string; filterText: string; insert: string; docs: string }> = [
  {
    label: "SELECT col FROM table",
    filterText: "select",
    insert: "SELECT ${2:column1}, ${3:column2} FROM ${1:table_name};\n$4",
    docs: "Basic SELECT query",
  },
  {
    label: "SELECT with JOIN",
    filterText: "select join",
    insert: "SELECT ${8:column1} FROM ${1:table_name} ${2:t1}\n${3:LEFT} JOIN ${4:table2} ${5:t2} ON ${2:t1}.${6:column1} = ${5:t2}.${7:column2};\n$9",
    docs: "SELECT with JOIN",
  },
  {
    label: "SELECT ORDER BY",
    filterText: "select order",
    insert: "SELECT ${2:column1}, ${3:column2} FROM ${1:table_name} ORDER BY ${4:column1} ${5:DESC};\n$6",
    docs: "SELECT with ORDER BY",
  },
  {
    label: "INSERT INTO table",
    filterText: "insert",
    insert: "INSERT INTO ${1:table_name} (${2:column1}, ${3:column2})\nVALUES (${4:value1}, ${5:value2});\n$6",
    docs: "INSERT INTO statement",
  },
  {
    label: "INSERT INTO … SELECT",
    filterText: "insert select",
    insert: "INSERT INTO ${1:table_name}\nSELECT ${3:column1}, ${4:column2}\nFROM ${2:source_table}\nWHERE ${5:conditions};\n$6",
    docs: "INSERT from SELECT",
  },
  {
    label: "UPDATE table SET",
    filterText: "update",
    insert: "UPDATE ${1:table_name}\nSET ${2:column1} = ${3:value1}\nWHERE ${4:column2} = ${5:value2};\n$6",
    docs: "UPDATE statement",
  },
  {
    label: "DELETE FROM table",
    filterText: "delete",
    insert: "DELETE FROM ${1:table_name}\nWHERE ${2:column1} = ${3:value1};\n$4",
    docs: "DELETE statement",
  },
  {
    label: "CREATE TABLE (...)",
    filterText: "create table",
    insert: "CREATE TABLE ${1:table_name} (\n\t${2:column1} ${3:INT},\n\t${4:column2} ${5:VARCHAR(255)},\n\tPRIMARY KEY (${2:column1})\n);\n$6",
    docs: "CREATE TABLE",
  },
  {
    label: "ALTER TABLE ADD COLUMN",
    filterText: "alter add column",
    insert: "ALTER TABLE ${1:table_name} ADD COLUMN ${2:column_name} ${3:INT};\n$4",
    docs: "Add column to table",
  },
  {
    label: "ALTER TABLE ADD PRIMARY KEY",
    filterText: "alter add primary key",
    insert: "ALTER TABLE ${1:table_name} ADD CONSTRAINT ${2:pk_name} PRIMARY KEY (${3:column_name});\n$4",
    docs: "Add primary key",
  },
  {
    label: "WITH cte AS (…) SELECT",
    filterText: "with cte",
    insert: "WITH ${1:cte_name} AS (\n\tSELECT ${3:column1}, ${4:column2}\n\tFROM ${2:source_table}\n\tWHERE ${5:conditions}\n)\nSELECT *\nFROM ${1:cte_name};\n$6",
    docs: "Common Table Expression",
  },
  {
    label: "CREATE TABLE PARTITION BY RANGE",
    filterText: "create partition range",
    insert: "CREATE TABLE ${1:table_name} (\n\t${2:column1} ${3:INT},\n\t${4:column2} ${5:INT}\n)\nPARTITION BY RANGE (${2:column1});\n\nCREATE TABLE ${6:p0} PARTITION OF ${1:table_name}\n\tFOR VALUES FROM (${7:start}) TO (${8:end});\n$9",
    docs: "Partitioned table by range",
  },
]

const PYTHON_SNIPPETS: Array<{ label: string; filterText: string; insert: string; docs: string }> = [
  {
    label: "pd.read_sql(query, engine)",
    filterText: "pandas read sql dataframe",
    insert: 'df = pd.read_sql("""\n  SELECT ${1:*}\n  FROM ${2:table_name}\n  WHERE ${3:1=1}\n""", con=engine)\ndf.head()',
    docs: "Query DB with pandas + SQLAlchemy",
  },
  {
    label: "px.bar(df, x=…, y=…)",
    filterText: "plotly bar chart",
    insert: 'fig = px.bar(df, x="${1:category}", y="${2:value}", title="${3:Chart Title}")\nfig.show()',
    docs: "Plotly bar chart",
  },
  {
    label: "df.groupby(…).agg(…)",
    filterText: "groupby aggregate sum count",
    insert: 'result = (\n    df\n    .groupby("${1:group_col}")\n    .agg(total=("${2:value_col}", "sum"), count=("${2:value_col}", "count"))\n    .reset_index()\n    .sort_values("total", ascending=False)\n)\nresult',
    docs: "GroupBy with aggregation",
  },
  {
    label: "df.describe() + dtypes",
    filterText: "describe dataframe info",
    insert: "print(df.shape)\nprint(df.dtypes)\ndf.describe()",
    docs: "Inspect DataFrame",
  },
  {
    label: "df.resample(freq).sum()",
    filterText: "resample time series",
    insert: 'ts = df.set_index("${1:date_col}")["${2:value_col}"].resample("${3:M}").${4:sum}()\nts.plot()',
    docs: "Time series resampling",
  },
]

let completionsRegistered = false

function setupMonaco(monaco: any) {
  if (completionsRegistered) return
  completionsRegistered = true

  monaco.languages.registerCompletionItemProvider("sql", {
    provideCompletionItems: (model: any, position: any) => {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      return {
        suggestions: SQL_SNIPPETS.map(s => ({
          label: s.label,
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: s.insert,
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          documentation: s.docs,
          detail: "PostgreSQL · full template",
          sortText: s.label,
          range,
        })),
      }
    },
  })

  monaco.languages.registerCompletionItemProvider("python", {
    provideCompletionItems: (model: any, position: any) => {
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber,
        endLineNumber: position.lineNumber,
        startColumn: word.startColumn,
        endColumn: word.endColumn,
      }
      return {
        suggestions: PYTHON_SNIPPETS.map(s => ({
          label: s.label,
          kind: monaco.languages.CompletionItemKind.Snippet,
          insertText: s.insert,
          insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
          documentation: s.docs,
          detail: "Python · full template",
          sortText: `0_${s.label}`,
          preselect: true,
          range,
        })),
      }
    },
  })
}

interface SQLEditorProps {
  value: string
  onChange: (v: string) => void
  language?: "sql" | "python"
  onCtrlEnter?: () => void
}

export function SQLEditor({ value, onChange, language = "sql", onCtrlEnter }: SQLEditorProps) {
  const editorRef = useRef<any>(null)

  const handleMount: OnMount = useCallback((editor, monaco) => {
    editorRef.current = editor
    setupMonaco(monaco)

    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
      () => { onCtrlEnter?.() }
    )

    if (language === "sql") {
      editor.addAction({
        id: "sql-format",
        label: "Format SQL",
        keybindings: [monaco.KeyMod.Alt | monaco.KeyMod.Shift | monaco.KeyCode.KeyF],
        run: () => editor.getAction("editor.action.formatDocument")?.run(),
      })
    }
  }, [onCtrlEnter, language])

  const isSQL = language === "sql"
  const isPython = language === "python"

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
            Ctrl+Enter chạy · Ctrl+Space gợi ý
          </span>
        </div>
        {isSQL && <span className="text-[10px] text-zinc-600">Alt+Shift+F format</span>}
      </div>

      <Suspense fallback={
        <div className="flex h-[150px] items-center justify-center gap-2 text-xs text-zinc-500 bg-zinc-950">
          <div className="h-3 w-3 rounded-full bg-zinc-600 animate-pulse" />
          Đang tải editor...
        </div>
      }>
        <MonacoEditor
          height="180px"
          language={language}
          value={value}
          onChange={v => onChange(v ?? "")}
          onMount={handleMount}
          theme="vs-dark"
          options={{
            minimap: { enabled: false },
            fontSize: 12.5,
            lineNumbers: "on",
            lineNumbersMinChars: 3,
            scrollBeyondLastLine: false,
            wordWrap: "on",
            padding: { top: 10, bottom: 8 },
            fontFamily: "JetBrains Mono, Fira Code, 'Cascadia Code', monospace",
            fontLigatures: true,
            fixedOverflowWidgets: true,
            quickSuggestions: { other: true, comments: true, strings: true },
            suggestOnTriggerCharacters: true,
            wordBasedSuggestions: "allDocuments",
            parameterHints: { enabled: true },
            tabCompletion: "on",
            acceptSuggestionOnEnter: "on",
            suggest: {
              showWords: true,
              showKeywords: true,
              showSnippets: true,
              showFunctions: true,
              showVariables: true,
              showClasses: true,
              showModules: true,
              insertMode: "replace",
              preview: true,
            },
            tabSize: language === "python" ? 4 : 2,
            detectIndentation: false,
            renderLineHighlight: "gutter",
            cursorBlinking: "smooth",
            cursorSmoothCaretAnimation: "on",
            smoothScrolling: true,
            folding: true,
            bracketPairColorization: { enabled: true },
            guides: { bracketPairs: true, indentation: true },
            automaticLayout: true,
            overviewRulerLanes: 0,
            scrollbar: { vertical: "hidden", horizontal: "auto", alwaysConsumeMouseWheel: false },
          }}
        />
      </Suspense>

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
              <span>DML · DQL · CTEs · Window</span>
            </>
          )}
        </div>
        <span className="text-[10px] text-zinc-700">{value.split("\n").length} dòng · {value.length} ký tự</span>
      </div>
    </div>
  )
}
