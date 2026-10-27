/**
 * Answer text as markdown (react-markdown + GFM: headings, lists, tables, fenced code, bold, links)
 * with the `[n]` citation markers of the answer. The text comes from a model, so nothing in it is
 * trusted: raw HTML is not rendered, rehype-sanitize keeps only the elements and link protocols of
 * its default schema plus the citation marker, and a link opens in a new tab without an opener.
 * Prose is DA/Reading, as in "P01 · Hỏi đáp" (2296:18565).
 */
import type { Root, Text } from 'mdast';
import type { ReactNode } from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import rehypeSanitize, { defaultSchema } from 'rehype-sanitize';
import remarkGfm from 'remark-gfm';
import { SKIP, visit } from 'unist-util-visit';
import type { Citation } from '../types';

type OpenCitation = (citation: Citation) => void;

const CITATION = /\[(\d+)\]/g;

/** `[n]` in plain text (not in code, not a link's own text) becomes a citation marker element. */
function remarkCitations() {
  return (tree: Root) => {
    visit(tree, 'text', (node: Text, index, parent) => {
      if (!parent || index === undefined || parent.type === 'link') return;
      const parts = node.value.split(CITATION);
      if (parts.length === 1) return;
      // split() with a group alternates text and the captured number.
      const nodes = parts.flatMap((part, i): Root['children'][number][] => {
        if (i % 2 === 0) return part ? [{ type: 'text', value: part }] : [];
        return [
          {
            type: 'text',
            value: part,
            data: { hName: 'cite-ref', hProperties: { dataIndex: part }, hChildren: [] },
          },
        ];
      }) as Text[];
      parent.children.splice(index, 1, ...nodes);
      return [SKIP, index + nodes.length];
    });
  };
}

const SCHEMA = {
  ...defaultSchema,
  tagNames: [...(defaultSchema.tagNames ?? []), 'cite-ref'],
  attributes: { ...defaultSchema.attributes, 'cite-ref': ['dataIndex'] },
};

function CitationMarker({
  index,
  citations,
  onOpenCitation,
}: {
  index: string;
  citations?: Citation[];
  onOpenCitation?: OpenCitation;
}) {
  const c = citations?.find((x) => x.index === Number(index));
  // A number in brackets that names no citation is just text.
  if (!c) return <>[{index}]</>;
  const scoreStr = typeof c.score === 'number' ? ` · điểm ${c.score.toFixed(2)}` : '';
  const title = `${c.source}${scoreStr}\n${c.snippet}`;
  if (onOpenCitation && c.type === 'document' && c.doc_id)
    return (
      <sup className="ml-px align-[2px]">
        <button
          type="button"
          title={title}
          aria-label={`Mở nguồn ${index}: ${c.source}`}
          onClick={() => onOpenCitation(c)}
          className="inline-flex h-4 min-w-4 cursor-pointer items-center justify-center rounded-sm bg-accent px-da-xs text-primary da-label hover:underline"
        >
          {index}
        </button>
      </sup>
    );
  return (
    <sup
      title={title}
      className="ml-px inline-flex h-4 min-w-4 items-center justify-center rounded-sm bg-accent px-da-xs align-[2px] text-primary da-label"
    >
      {index}
    </sup>
  );
}

const heading2 = ({ children }: { children?: ReactNode }) => (
  <p className="mt-da-md da-heading-2">{children}</p>
);
const heading3 = ({ children }: { children?: ReactNode }) => (
  <p className="da-heading-3">{children}</p>
);

export function MarkdownContent({
  text,
  citations,
  onOpenCitation,
}: {
  text: string;
  citations?: Citation[];
  onOpenCitation?: OpenCitation;
}) {
  const components = {
    h1: heading2,
    h2: heading2,
    h3: heading3,
    h4: heading3,
    h5: heading3,
    h6: heading3,
    // A single newline in an answer is a new line, as the model writes it.
    p: ({ children }) => <p className="m-0 whitespace-pre-line da-reading">{children}</p>,
    ul: ({ children }) => <ul className="m-0 list-disc pl-da-2xl da-reading">{children}</ul>,
    ol: ({ children }) => <ol className="m-0 list-decimal pl-da-2xl da-reading">{children}</ol>,
    strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
    code: ({ children }) => (
      <code className="rounded-sm bg-subtle px-da-xs da-mono">{children}</code>
    ),
    pre: ({ children }) => (
      <pre className="m-0 overflow-x-auto whitespace-pre rounded-md border border-border bg-subtle px-da-lg py-da-md text-foreground da-mono [&>code]:bg-transparent [&>code]:p-0">
        {children}
      </pre>
    ),
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
        {children}
      </a>
    ),
    table: ({ children }) => (
      <div className="overflow-x-auto">
        <table className="w-full border-collapse da-body-small">{children}</table>
      </div>
    ),
    th: ({ children }) => (
      <th className="border-b border-border px-da-md py-da-sm text-left font-semibold">
        {children}
      </th>
    ),
    td: ({ children }) => <td className="border-b border-border px-da-md py-da-sm">{children}</td>,
    'cite-ref': ({ node }: { node?: { properties?: Record<string, unknown> } }) => (
      <CitationMarker
        index={String(node?.properties?.dataIndex ?? '')}
        citations={citations}
        onOpenCitation={onOpenCitation}
      />
    ),
  } as Components;

  return (
    <div className="flex flex-col gap-da-sm text-foreground">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkCitations]}
        rehypePlugins={[[rehypeSanitize, SCHEMA]]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
