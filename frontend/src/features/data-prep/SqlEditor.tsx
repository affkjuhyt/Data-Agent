import { PostgreSQL, sql, type SQLNamespace } from '@codemirror/lang-sql';
import { forceLinting, linter, lintGutter } from '@codemirror/lint';
import { Annotation, Compartment, EditorState, type Extension } from '@codemirror/state';
import {
  Decoration,
  EditorView,
  ViewPlugin,
  tooltips,
  type DecorationSet,
  type ViewUpdate,
} from '@codemirror/view';
import { basicSetup } from 'codemirror';
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { sqlDiagnostics } from '../../lib/sqlSyntax';

/** A parent-driven document change. It must not look like the analyst moved the caret. */
const externalChange = Annotation.define<true>();

const sqlLanguage = new Compartment();
const errorLine = new Compartment();

const workbenchTheme = EditorView.theme({
  '&': {
    height: '100%',
    backgroundColor: 'var(--card)',
    color: 'var(--foreground)',
    font: '400 12px/16px var(--font-mono)',
  },
  '&.cm-focused': { outline: 'none' },
  '.cm-scroller': { fontFamily: 'inherit', lineHeight: '16px' },
  '.cm-content': { padding: '12px 16px', caretColor: 'var(--foreground)', minHeight: '12rem' },
  '.cm-gutters': {
    backgroundColor: 'var(--subtle)',
    color: 'var(--muted-foreground)',
    borderRight: 'none',
  },
  '.cm-activeLine': { backgroundColor: 'transparent' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent' },
  '.cm-error-line': { backgroundColor: 'var(--error-subtle)' },
  '.cm-diagnostic-error': { borderLeftColor: 'var(--error)' },
});

/** The list is mounted on document.body, so these rules cannot be scoped to the editor. */
const tooltipTheme = EditorView.baseTheme({
  '.cm-tooltip.cm-tooltip-autocomplete': {
    zIndex: '40',
    backgroundColor: 'var(--card)',
    color: 'var(--foreground)',
    border: '1px solid var(--border)',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'var(--subtle)',
    color: 'var(--foreground)',
  },
});

const languageSupport = (
  schema: Record<string, unknown>,
  defaultSchema?: string,
  defaultTable?: string,
): Extension =>
  sql({
    dialect: PostgreSQL,
    schema: schema as SQLNamespace,
    upperCaseKeywords: true,
    defaultSchema,
    defaultTable,
  });

/** The line a server message named, painted behind the text. */
const serverErrorLine = (line: number | null): Extension => {
  if (line == null) return [];
  const mark = Decoration.line({ class: 'cm-error-line' });
  return ViewPlugin.fromClass(
    class {
      decorations: DecorationSet;
      constructor(view: EditorView) {
        this.decorations = this.build(view);
      }
      update(update: ViewUpdate) {
        if (update.docChanged) this.decorations = this.build(update.view);
      }
      build(view: EditorView) {
        if (line > view.state.doc.lines) return Decoration.none;
        const target = view.state.doc.line(line);
        return Decoration.set([mark.range(target.from)]);
      }
    },
    { decorations: (plugin) => plugin.decorations },
  );
};

export interface SqlEditorHandle {
  /** Remember where the next parent-driven text change should leave the caret. */
  placeCaret: (start: number, end: number) => void;
}

export interface SqlEditorProps {
  value: string;
  onChange: (value: string) => void;
  onRun: () => void;
  /** 1-based line from a server error, or null. */
  errorLine: number | null;
  onSelect: (selected: boolean) => void;
  onCaret?: (start: number, end: number) => void;
  schema: Record<string, unknown>;
  defaultSchema?: string;
  defaultTable?: string;
  /** Catalog tables, so a FROM name that is not one of them is marked. */
  tables: { name: string }[];
}

/**
 * CodeMirror with PostgreSQL highlighting and schema completion. The catalog is the schema.
 * ⌘↵ or Ctrl↵ runs. A selection is reported so the page can run that text; otherwise the
 * page runs the statement under the caret.
 */
export const SqlEditor = forwardRef<SqlEditorHandle, SqlEditorProps>(function SqlEditor(
  {
    value,
    onChange,
    onRun,
    errorLine: serverLine,
    onSelect,
    onCaret,
    schema,
    defaultSchema,
    defaultTable,
    tables,
  },
  ref,
) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const pendingCaret = useRef<{ start: number; end: number } | null>(null);
  const onChangeRef = useRef(onChange);
  const onRunRef = useRef(onRun);
  const onSelectRef = useRef(onSelect);
  const onCaretRef = useRef(onCaret);
  const tablesRef = useRef(tables);
  onChangeRef.current = onChange;
  onRunRef.current = onRun;
  onSelectRef.current = onSelect;
  onCaretRef.current = onCaret;
  tablesRef.current = tables;

  useImperativeHandle(ref, () => ({
    placeCaret(start: number, end: number) {
      pendingCaret.current = { start, end };
    },
  }));

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const view = new EditorView({
      parent: host,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          tooltips({ parent: document.body }),
          tooltipTheme,
          sqlLanguage.of(languageSupport(schema, defaultSchema, defaultTable)),
          errorLine.of(serverErrorLine(serverLine)),
          linter((current) =>
            sqlDiagnostics(current.state.doc.toString(), tablesRef.current).map((mark) => ({
              from: mark.from,
              to: Math.min(mark.to, current.state.doc.length),
              severity: 'error' as const,
              message: mark.message,
            })),
          ),
          lintGutter(),
          EditorView.domEventHandlers({
            keydown(event) {
              if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                onRunRef.current();
                return true;
              }
              return false;
            },
          }),
          workbenchTheme,
          EditorView.contentAttributes.of({ 'aria-label': 'Câu truy vấn SQL' }),
          EditorView.updateListener.of((update) => {
            const fromParent = update.transactions.every((transaction) =>
              transaction.annotation(externalChange),
            );
            if (update.docChanged && !fromParent) onChangeRef.current(update.state.doc.toString());
            if (!fromParent && (update.selectionSet || update.docChanged)) {
              const range = update.state.selection.main;
              onSelectRef.current(!range.empty);
              onCaretRef.current?.(range.from, range.to);
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => {
      view.destroy();
      viewRef.current = null;
    };
    // The view is configured by the effects below. Recreating it would drop the caret.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const caret = pendingCaret.current;
    pendingCaret.current = null;
    const current = view.state.doc.toString();
    const selection = caret
      ? { anchor: Math.min(caret.start, value.length), head: Math.min(caret.end, value.length) }
      : undefined;
    if (current === value) {
      if (selection) {
        view.dispatch({ selection, annotations: externalChange.of(true) });
        view.focus();
      }
      return;
    }
    view.dispatch({
      changes: { from: 0, to: current.length, insert: value },
      selection,
      annotations: externalChange.of(true),
    });
    if (caret) view.focus();
  }, [value]);

  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    view.dispatch({
      effects: sqlLanguage.reconfigure(languageSupport(schema, defaultSchema, defaultTable)),
    });
    forceLinting(view);
  }, [schema, defaultSchema, defaultTable, tables]);

  useEffect(() => {
    viewRef.current?.dispatch({
      effects: errorLine.reconfigure(serverErrorLine(serverLine)),
    });
  }, [serverLine]);

  return (
    <div className="flex min-h-[12rem] flex-1 flex-col overflow-hidden rounded-md border border-border bg-card">
      <div ref={hostRef} className="min-h-0 flex-1" />
    </div>
  );
});
